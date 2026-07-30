import Foundation
import JoeOSCore
import Security

struct StoredDeviceEnrollmentReceipt: Equatable, Sendable {
    let enrollmentID: UUID
    let deviceID: UUID
    let credentialID: String
    let observedServerID: UUID
    let audienceOrigin: String
    let state: String
    let enrolledAt: Date
    let authenticationKeyFingerprint: String
    let approvalKeyFingerprint: String
    let authorizationNotice: String

    init(_ receipt: DeviceEnrollmentReceipt) throws {
        guard Self.isVersion4(receipt.enrollmentID),
              Self.isVersion4(receipt.deviceID),
              Self.isVersion4(receipt.observedServerID),
              receipt.state == "active_unassigned",
              (try? EnrollmentAudienceOrigin(receipt.audienceOrigin).value) == receipt.audienceOrigin,
              Self.isCanonicalBase64URL32(receipt.credentialID),
              Self.isCanonicalBase64URL32(receipt.authenticationKeyFingerprint),
              Self.isCanonicalBase64URL32(receipt.approvalKeyFingerprint),
              receipt.authenticationKeyFingerprint != receipt.approvalKeyFingerprint,
              receipt.authorizationNotice == Self.authorizationNotice,
              let enrolledAtEpoch = Int64(exactly: receipt.enrolledAt.timeIntervalSince1970),
              (0...Self.maximumEpoch).contains(enrolledAtEpoch)
        else {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
        enrollmentID = receipt.enrollmentID
        deviceID = receipt.deviceID
        credentialID = receipt.credentialID
        observedServerID = receipt.observedServerID
        audienceOrigin = receipt.audienceOrigin
        state = receipt.state
        enrolledAt = receipt.enrolledAt
        authenticationKeyFingerprint = receipt.authenticationKeyFingerprint
        approvalKeyFingerprint = receipt.approvalKeyFingerprint
        authorizationNotice = receipt.authorizationNotice
    }

    fileprivate init(document: StoredDeviceEnrollmentReceiptDocument) throws {
        guard document.schemaVersion == 1,
              let enrollmentID = UUID(uuidString: document.enrollmentID),
              let deviceID = UUID(uuidString: document.deviceID),
              let serverID = UUID(uuidString: document.observedServerID),
              document.enrollmentID == enrollmentID.uuidString.lowercased(),
              document.deviceID == deviceID.uuidString.lowercased(),
              document.observedServerID == serverID.uuidString.lowercased(),
              Self.isVersion4(enrollmentID),
              Self.isVersion4(deviceID),
              Self.isVersion4(serverID),
              document.state == "active_unassigned",
              (try? EnrollmentAudienceOrigin(document.audienceOrigin).value) == document.audienceOrigin,
              Self.isCanonicalBase64URL32(document.credentialID),
              Self.isCanonicalBase64URL32(document.authenticationKeyFingerprint),
              Self.isCanonicalBase64URL32(document.approvalKeyFingerprint),
              document.authenticationKeyFingerprint != document.approvalKeyFingerprint,
              document.authorizationNotice == Self.authorizationNotice,
              (0...Self.maximumEpoch).contains(document.enrolledAtEpoch)
        else {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
        self.enrollmentID = enrollmentID
        self.deviceID = deviceID
        credentialID = document.credentialID
        observedServerID = serverID
        audienceOrigin = document.audienceOrigin
        state = document.state
        enrolledAt = Date(timeIntervalSince1970: TimeInterval(document.enrolledAtEpoch))
        authenticationKeyFingerprint = document.authenticationKeyFingerprint
        approvalKeyFingerprint = document.approvalKeyFingerprint
        authorizationNotice = document.authorizationNotice
    }

    fileprivate func makeDocument() throws -> StoredDeviceEnrollmentReceiptDocument {
        guard let enrolledAtEpoch = Int64(exactly: enrolledAt.timeIntervalSince1970),
              (0...Self.maximumEpoch).contains(enrolledAtEpoch)
        else {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
        return StoredDeviceEnrollmentReceiptDocument(
            schemaVersion: 1,
            enrollmentID: enrollmentID.uuidString.lowercased(),
            deviceID: deviceID.uuidString.lowercased(),
            credentialID: credentialID,
            observedServerID: observedServerID.uuidString.lowercased(),
            audienceOrigin: audienceOrigin,
            state: state,
            enrolledAtEpoch: enrolledAtEpoch,
            authenticationKeyFingerprint: authenticationKeyFingerprint,
            approvalKeyFingerprint: approvalKeyFingerprint,
            authorizationNotice: authorizationNotice
        )
    }

    private static let authorizationNotice =
        "Paired device has no role, session, approval, or execution authority."
    private static let maximumEpoch: Int64 = 4_102_444_800

    fileprivate static func isVersion4(_ value: UUID) -> Bool {
        var bytes = value.uuid
        return withUnsafeBytes(of: &bytes) {
            ($0[6] >> 4) == 4 && ($0[8] & 0xc0) == 0x80
        }
    }

    private static func isCanonicalBase64URL32(_ value: String) -> Bool {
        guard value.count == 43,
              value.unicodeScalars.allSatisfy({ scalar in
                  scalar.isASCII && (scalar.properties.isAlphabetic || scalar.properties.numericType != nil || scalar == "-" || scalar == "_")
              })
        else {
            return false
        }
        var encoded = value.replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        encoded.append("=")
        guard let decoded = Data(base64Encoded: encoded), decoded.count == 32 else {
            return false
        }
        return decoded.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "") == value
    }
}

enum DeviceEnrollmentLocalStoreError: Error, LocalizedError {
    case keychainUnavailable
    case invalidStoredState

    var errorDescription: String? {
        switch self {
        case .keychainUnavailable:
            "The iPhone Keychain is unavailable. No enrollment data was sent or discarded."
        case .invalidStoredState:
            "Stored JoeOS enrollment state did not pass validation. Review or discard it explicitly."
        }
    }
}

actor DeviceEnrollmentLocalStore {
    private static let service = "com.joeos.client.device-enrollment.v1"
    private static let clientInstanceAccount = "client-instance.v1"
    private static let maximumReceiptBytes = 8_192

    func stableClientInstanceID() throws -> UUID {
        if let data = try read(account: Self.clientInstanceAccount) {
            guard data.count <= 64,
                  let value = String(data: data, encoding: .utf8),
                  let identifier = UUID(uuidString: value),
                  value == identifier.uuidString.lowercased(),
                  StoredDeviceEnrollmentReceipt.isVersion4(identifier)
            else {
                throw DeviceEnrollmentLocalStoreError.invalidStoredState
            }
            return identifier
        }

        let identifier = UUID()
        guard StoredDeviceEnrollmentReceipt.isVersion4(identifier) else {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
        try write(
            Data(identifier.uuidString.lowercased().utf8),
            account: Self.clientInstanceAccount
        )
        return identifier
    }

    func loadReceipt(serverID: UUID) throws -> StoredDeviceEnrollmentReceipt? {
        guard let data = try read(account: receiptAccount(serverID)) else { return nil }
        guard !data.isEmpty, data.count <= Self.maximumReceiptBytes else {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
        do {
            let document = try JSONDecoder().decode(
                StoredDeviceEnrollmentReceiptDocument.self,
                from: data
            )
            let receipt = try StoredDeviceEnrollmentReceipt(document: document)
            guard receipt.observedServerID == serverID else {
                throw DeviceEnrollmentLocalStoreError.invalidStoredState
            }
            return receipt
        } catch let error as DeviceEnrollmentLocalStoreError {
            throw error
        } catch {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
    }

    func storeReceipt(_ receipt: DeviceEnrollmentReceipt) throws -> StoredDeviceEnrollmentReceipt {
        let stored = try StoredDeviceEnrollmentReceipt(receipt)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(stored.makeDocument())
        guard !data.isEmpty, data.count <= Self.maximumReceiptBytes else {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
        try write(data, account: receiptAccount(stored.observedServerID))
        return stored
    }

    func loadCompletionJournal(serverID: UUID) throws -> Data? {
        try read(account: journalAccount(serverID))
    }

    func storeCompletionJournal(_ data: Data, serverID: UUID) throws {
        guard !data.isEmpty,
              data.count <= SignedDeviceEnrollmentCompletion.maximumResumeDocumentBytes,
              let signed = try? SignedDeviceEnrollmentCompletion.resume(from: data),
              signed.review.observedServerID == serverID
        else {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
        try write(data, account: journalAccount(serverID))
    }

    func discardCompletionJournal(serverID: UUID) throws {
        try delete(account: journalAccount(serverID))
    }

    private func receiptAccount(_ serverID: UUID) -> String {
        "receipt.\(serverID.uuidString.lowercased())"
    }

    private func journalAccount(_ serverID: UUID) -> String {
        "signed-completion.\(serverID.uuidString.lowercased())"
    }

    private func baseQuery(account: String) -> [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: Self.service,
            kSecAttrAccount: account,
            kSecAttrSynchronizable: kCFBooleanFalse as Any,
        ]
    }

    private func read(account: String) throws -> Data? {
        var query = baseQuery(account: account)
        query[kSecReturnData] = kCFBooleanTrue
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else {
            throw DeviceEnrollmentLocalStoreError.keychainUnavailable
        }
        return data
    }

    private func write(_ data: Data, account: String) throws {
        guard !data.isEmpty else {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
        let query = baseQuery(account: account)
        let updated = SecItemUpdate(
            query as CFDictionary,
            [kSecValueData: data] as CFDictionary
        )
        if updated == errSecSuccess { return }
        guard updated == errSecItemNotFound else {
            throw DeviceEnrollmentLocalStoreError.keychainUnavailable
        }
        var item = query
        item[kSecValueData] = data
        item[kSecAttrAccessible] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else {
            throw DeviceEnrollmentLocalStoreError.keychainUnavailable
        }
    }

    private func delete(account: String) throws {
        let status = SecItemDelete(baseQuery(account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw DeviceEnrollmentLocalStoreError.keychainUnavailable
        }
    }
}

private struct StoredDeviceEnrollmentReceiptDocument: Codable {
    let schemaVersion: Int
    let enrollmentID: String
    let deviceID: String
    let credentialID: String
    let observedServerID: String
    let audienceOrigin: String
    let state: String
    let enrolledAtEpoch: Int64
    let authenticationKeyFingerprint: String
    let approvalKeyFingerprint: String
    let authorizationNotice: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case schemaVersion = "schema_version"
        case enrollmentID = "enrollment_id"
        case deviceID = "device_id"
        case credentialID = "credential_id"
        case observedServerID = "observed_server_id"
        case audienceOrigin = "audience_origin"
        case state
        case enrolledAtEpoch = "enrolled_at_epoch"
        case authenticationKeyFingerprint = "authentication_key_fingerprint"
        case approvalKeyFingerprint = "approval_key_fingerprint"
        case authorizationNotice = "authorization_notice"
    }

    init(
        schemaVersion: Int,
        enrollmentID: String,
        deviceID: String,
        credentialID: String,
        observedServerID: String,
        audienceOrigin: String,
        state: String,
        enrolledAtEpoch: Int64,
        authenticationKeyFingerprint: String,
        approvalKeyFingerprint: String,
        authorizationNotice: String
    ) {
        self.schemaVersion = schemaVersion
        self.enrollmentID = enrollmentID
        self.deviceID = deviceID
        self.credentialID = credentialID
        self.observedServerID = observedServerID
        self.audienceOrigin = audienceOrigin
        self.state = state
        self.enrolledAtEpoch = enrolledAtEpoch
        self.authenticationKeyFingerprint = authenticationKeyFingerprint
        self.approvalKeyFingerprint = approvalKeyFingerprint
        self.authorizationNotice = authorizationNotice
    }

    init(from decoder: Decoder) throws {
        let all = try decoder.container(keyedBy: EnrollmentLocalAnyCodingKey.self)
        let allowed = Set(CodingKeys.allCases.map(\.rawValue))
        guard Set(all.allKeys.map(\.stringValue)).isSubset(of: allowed) else {
            throw DeviceEnrollmentLocalStoreError.invalidStoredState
        }
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        enrollmentID = try container.decode(String.self, forKey: .enrollmentID)
        deviceID = try container.decode(String.self, forKey: .deviceID)
        credentialID = try container.decode(String.self, forKey: .credentialID)
        observedServerID = try container.decode(String.self, forKey: .observedServerID)
        audienceOrigin = try container.decode(String.self, forKey: .audienceOrigin)
        state = try container.decode(String.self, forKey: .state)
        enrolledAtEpoch = try container.decode(Int64.self, forKey: .enrolledAtEpoch)
        authenticationKeyFingerprint = try container.decode(
            String.self,
            forKey: .authenticationKeyFingerprint
        )
        approvalKeyFingerprint = try container.decode(
            String.self,
            forKey: .approvalKeyFingerprint
        )
        authorizationNotice = try container.decode(String.self, forKey: .authorizationNotice)
    }
}

private struct EnrollmentLocalAnyCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int? = nil
    init?(stringValue: String) { self.stringValue = stringValue }
    init?(intValue: Int) { return nil }
}
