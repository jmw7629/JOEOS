import Foundation
import CryptoKit
#if canImport(Security)
import Security
#endif

public protocol EnrollmentSigningKey: Sendable {
    func publicKeySPKIDER() async throws -> Data
    func signature(for message: Data) async throws -> Data
}

public struct EnrollmentKeySet: Sendable {
    public let deviceAuthentication: any EnrollmentSigningKey
    public let approval: any EnrollmentSigningKey

    public init(
        deviceAuthentication: any EnrollmentSigningKey,
        approval: any EnrollmentSigningKey
    ) {
        self.deviceAuthentication = deviceAuthentication
        self.approval = approval
    }
}

public protocol EnrollmentKeyProviding: Sendable {
    func loadOrCreateKeys() async throws -> EnrollmentKeySet
}

public protocol EnrollmentRandomSource: Sendable {
    func bytes(count: Int) throws -> Data
}

public struct SecureEnrollmentRandomSource: EnrollmentRandomSource, Sendable {
    public init() {}

    public func bytes(count: Int) throws -> Data {
        guard count > 0 && count <= 4_096 else { throw DeviceEnrollmentError.keyGenerationFailed }
        var data = Data(count: count)
        #if canImport(Security)
        let status = data.withUnsafeMutableBytes { buffer in
            SecRandomCopyBytes(kSecRandomDefault, count, buffer.baseAddress!)
        }
        guard status == errSecSuccess else { throw DeviceEnrollmentError.keyGenerationFailed }
        #else
        var generator = SystemRandomNumberGenerator()
        for index in data.indices { data[index] = UInt8.random(in: .min ... .max, using: &generator) }
        #endif
        return data
    }
}

/// An in-memory P-256 key for interoperability tests and explicit development use.
/// Production iPhone enrollment should inject `SecureEnclaveEnrollmentKeyProvider`.
public actor SoftwareP256EnrollmentSigningKey: EnrollmentSigningKey {
    private let privateKey: P256.Signing.PrivateKey

    public init() {
        privateKey = P256.Signing.PrivateKey()
    }

    public init(rawRepresentation: Data) throws {
        do {
            privateKey = try P256.Signing.PrivateKey(rawRepresentation: rawRepresentation)
        } catch {
            throw DeviceEnrollmentError.keyGenerationFailed
        }
    }

    public func publicKeySPKIDER() throws -> Data {
        try DeviceEnrollmentProtocol.subjectPublicKeyInfo(
            x963Representation: privateKey.publicKey.x963Representation
        )
    }

    public func signature(for message: Data) throws -> Data {
        guard !message.isEmpty, message.count <= 4_096 else {
            throw DeviceEnrollmentError.signingFailed
        }
        do {
            return try privateKey.signature(for: message).derRepresentation
        } catch {
            throw DeviceEnrollmentError.signingFailed
        }
    }
}

public actor SoftwareP256EnrollmentKeyProvider: EnrollmentKeyProviding {
    private let keys: EnrollmentKeySet

    public init() {
        keys = EnrollmentKeySet(
            deviceAuthentication: SoftwareP256EnrollmentSigningKey(),
            approval: SoftwareP256EnrollmentSigningKey()
        )
    }

    public func loadOrCreateKeys() -> EnrollmentKeySet { keys }
}

#if canImport(Security)
/// Stores two non-exportable P-256 private keys in the Secure Enclave. The approval
/// key is bound to the current biometric enrollment and requires biometric presence.
public actor SecureEnclaveEnrollmentKeyProvider: EnrollmentKeyProviding {
    private let authenticationTag: Data
    private let approvalTag: Data

    public init(applicationTagPrefix: String = "com.joeos.mobile.enrollment") throws {
        guard !applicationTagPrefix.isEmpty,
              applicationTagPrefix.count <= 180,
              applicationTagPrefix.unicodeScalars.allSatisfy({ $0.isASCII })
        else {
            throw DeviceEnrollmentError.keyGenerationFailed
        }
        authenticationTag = Data("\(applicationTagPrefix).device-authentication.v1".utf8)
        approvalTag = Data("\(applicationTagPrefix).approval.v1".utf8)
        guard authenticationTag != approvalTag else { throw DeviceEnrollmentError.keysAreNotDistinct }
    }

    public func loadOrCreateKeys() throws -> EnrollmentKeySet {
        let authentication = try loadOrCreate(
            tag: authenticationTag,
            accessFlags: [.privateKeyUsage]
        )
        let approval = try loadOrCreate(
            tag: approvalTag,
            accessFlags: [.privateKeyUsage, .biometryCurrentSet]
        )
        return EnrollmentKeySet(
            deviceAuthentication: SecurityP256EnrollmentSigningKey(
                handle: SendableSecKeyHandle(authentication)
            ),
            approval: SecurityP256EnrollmentSigningKey(
                handle: SendableSecKeyHandle(approval)
            )
        )
    }

    private func loadOrCreate(tag: Data, accessFlags: SecAccessControlCreateFlags) throws -> SecKey {
        var accessError: Unmanaged<CFError>?
        guard let expectedAccess = SecAccessControlCreateWithFlags(
            nil,
            kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
            accessFlags,
            &accessError
        ) else {
            throw DeviceEnrollmentError.keyGenerationFailed
        }
        let query: [CFString: Any] = [
            kSecClass: kSecClassKey,
            kSecAttrApplicationTag: tag,
            kSecAttrKeyType: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeyClass: kSecAttrKeyClassPrivate,
            kSecAttrKeySizeInBits: 256,
            kSecAttrTokenID: kSecAttrTokenIDSecureEnclave,
            kSecReturnRef: true,
            kSecMatchLimit: kSecMatchLimitOne,
        ]
        var existing: CFTypeRef?
        let existingStatus = SecItemCopyMatching(query as CFDictionary, &existing)
        if existingStatus == errSecSuccess {
            guard let value = existing, CFGetTypeID(value) == SecKeyGetTypeID() else {
                throw DeviceEnrollmentError.keyGenerationFailed
            }
            let key = unsafeBitCast(value, to: SecKey.self)
            try validate(
                key: key,
                tag: tag,
                expectedAccess: expectedAccess
            )
            return key
        }
        guard existingStatus == errSecItemNotFound else {
            throw DeviceEnrollmentError.keyGenerationFailed
        }

        let privateAttributes: [CFString: Any] = [
            kSecAttrIsPermanent: true,
            kSecAttrApplicationTag: tag,
            kSecAttrAccessControl: expectedAccess,
        ]
        let attributes: [CFString: Any] = [
            kSecAttrKeyType: kSecAttrKeyTypeECSECPrimeRandom,
            kSecAttrKeySizeInBits: 256,
            kSecAttrTokenID: kSecAttrTokenIDSecureEnclave,
            kSecPrivateKeyAttrs: privateAttributes,
        ]
        var creationError: Unmanaged<CFError>?
        guard let key = SecKeyCreateRandomKey(attributes as CFDictionary, &creationError) else {
            throw DeviceEnrollmentError.keyGenerationFailed
        }
        try validate(key: key, tag: tag, expectedAccess: expectedAccess)
        return key
    }

    private func validate(
        key: SecKey,
        tag: Data,
        expectedAccess: SecAccessControl
    ) throws {
        guard let rawAttributes = SecKeyCopyAttributes(key) as NSDictionary?,
              Self.cfEqual(rawAttributes[kSecAttrKeyType], kSecAttrKeyTypeECSECPrimeRandom),
              Self.cfEqual(rawAttributes[kSecAttrKeyClass], kSecAttrKeyClassPrivate),
              (rawAttributes[kSecAttrKeySizeInBits] as? NSNumber)?.intValue == 256,
              Self.cfEqual(rawAttributes[kSecAttrTokenID], kSecAttrTokenIDSecureEnclave),
              rawAttributes[kSecAttrApplicationTag] as? Data == tag,
              Self.cfEqual(rawAttributes[kSecAttrAccessControl], expectedAccess)
        else {
            // Security does not expose a trustworthy access policy for this key.
            // Fail closed; key deletion/replacement requires a separate explicit reset flow.
            throw DeviceEnrollmentError.keyGenerationFailed
        }
    }

    private static func cfEqual(_ value: Any?, _ expected: CFTypeRef) -> Bool {
        guard let value else { return false }
        return CFEqual(value as CFTypeRef, expected)
    }
}

/// `SecKey` is an immutable, retained Security.framework reference. Security's key
/// operations are thread-safe; the wrapper only transfers ownership into one actor.
private final class SendableSecKeyHandle: @unchecked Sendable {
    let key: SecKey
    init(_ key: SecKey) { self.key = key }
}

private actor SecurityP256EnrollmentSigningKey: EnrollmentSigningKey {
    private let handle: SendableSecKeyHandle

    init(handle: SendableSecKeyHandle) {
        self.handle = handle
    }

    func publicKeySPKIDER() throws -> Data {
        guard let publicKey = SecKeyCopyPublicKey(handle.key) else {
            throw DeviceEnrollmentError.keyGenerationFailed
        }
        var error: Unmanaged<CFError>?
        guard let raw = SecKeyCopyExternalRepresentation(publicKey, &error) as Data? else {
            throw DeviceEnrollmentError.keyGenerationFailed
        }
        return try DeviceEnrollmentProtocol.subjectPublicKeyInfo(x963Representation: raw)
    }

    func signature(for message: Data) throws -> Data {
        guard !message.isEmpty, message.count <= 4_096 else {
            throw DeviceEnrollmentError.signingFailed
        }
        var error: Unmanaged<CFError>?
        guard let signature = SecKeyCreateSignature(
            handle.key,
            .ecdsaSignatureMessageX962SHA256,
            message as CFData,
            &error
        ) as Data? else {
            throw DeviceEnrollmentError.signingFailed
        }
        guard DeviceEnrollmentProtocol.isCanonicalP256DERSignature(signature) else {
            throw DeviceEnrollmentError.signingFailed
        }
        return signature
    }
}
#endif

struct EnrollmentTranscriptFields: Sendable {
    let serverID: UUID
    let audienceOrigin: String
    let offerID: UUID
    let requestID: UUID
    let challengeID: UUID
    let deviceID: UUID
    let clientInstanceID: UUID
    let clientNonce: Data
    let serverNonce: Data
    let displayName: String
    let platform: String
    let osVersion: String
    let appVersion: String
    let authenticationSPKI: Data
    let approvalSPKI: Data
    let issuedAt: Int64
    let expiresAt: Int64
}

struct EnrollmentClaimFields: Sendable {
    let observedServerID: UUID
    let audienceOrigin: String
    let offerID: UUID
    let requestID: UUID
    let clientInstanceID: UUID
    let clientNonce: Data
    let displayName: String
    let platform: String
    let osVersion: String
    let appVersion: String
    let authenticationSPKI: Data
    let approvalSPKI: Data
}

enum EnrollmentKeyPurpose: String, Sendable {
    case deviceAuthentication = "device_authentication"
    case approval

    var signingDomain: String {
        switch self {
        case .deviceAuthentication:
            "JOEOS-DEVICE-ENROLLMENT-PROOF-V1:DEVICE-AUTHENTICATION"
        case .approval:
            "JOEOS-DEVICE-ENROLLMENT-PROOF-V1:APPROVAL"
        }
    }
}

enum DeviceEnrollmentProtocol {
    static let pairingKeyInfo = Data("joeos.device-enrollment.pairing-key.v1".utf8)
    static let transcriptDomain = Data("JOEOS-DEVICE-ENROLLMENT-TRANSCRIPT-V1\0".utf8)
    static let claimTranscriptDomain = Data("JOEOS-DEVICE-ENROLLMENT-CLAIM-V1\0".utf8)
    static let serverProofDomain = Data("JOEOS-DEVICE-ENROLLMENT-SERVER-PROOF-V1\0".utf8)
    static let clientProofDomain = Data("JOEOS-DEVICE-ENROLLMENT-CLIENT-PROOF-V1\0".utf8)
    private static let p256SPKIPrefix = Data([
        0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce,
        0x3d, 0x02, 0x01, 0x06, 0x08, 0x2a, 0x86, 0x48, 0xce, 0x3d,
        0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
    ])

    static func subjectPublicKeyInfo(x963Representation: Data) throws -> Data {
        guard x963Representation.count == 65, x963Representation.first == 0x04 else {
            throw DeviceEnrollmentError.keyGenerationFailed
        }
        return p256SPKIPrefix + x963Representation
    }

    static func isCanonicalP256SPKI(_ data: Data) -> Bool {
        guard data.count == p256SPKIPrefix.count + 65,
              data.prefix(p256SPKIPrefix.count) == p256SPKIPrefix
        else { return false }
        let x963 = Data(data.dropFirst(p256SPKIPrefix.count))
        guard let key = try? P256.Signing.PublicKey(x963Representation: x963),
              key.x963Representation == x963
        else { return false }
        return true
    }

    static func derivePairingKey(secret: Data, offerID: UUID) throws -> Data {
        guard secret.count == 32, EnrollmentCoding.isVersion4(offerID) else {
            throw DeviceEnrollmentError.invalidPairingCode
        }
        let extracted = hmacSHA256(key: uuidBytes(offerID), message: secret)
        return hmacSHA256(key: extracted, message: pairingKeyInfo + Data([0x01]))
    }

    static func buildTranscript(_ fields: EnrollmentTranscriptFields) throws -> Data {
        guard EnrollmentCoding.isVersion4(fields.serverID),
              EnrollmentCoding.isVersion4(fields.offerID),
              EnrollmentCoding.isVersion4(fields.requestID),
              EnrollmentCoding.isVersion4(fields.challengeID),
              EnrollmentCoding.isVersion4(fields.deviceID),
              EnrollmentCoding.isVersion4(fields.clientInstanceID),
              fields.clientNonce.count == 32,
              fields.serverNonce.count == 32,
              fields.issuedAt >= 0,
              fields.expiresAt > fields.issuedAt
        else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        let values: [Data] = [
            Data(fields.serverID.uuidString.lowercased().utf8),
            Data(fields.audienceOrigin.utf8),
            Data(fields.offerID.uuidString.lowercased().utf8),
            Data(fields.requestID.uuidString.lowercased().utf8),
            Data(fields.challengeID.uuidString.lowercased().utf8),
            Data(fields.deviceID.uuidString.lowercased().utf8),
            Data(fields.clientInstanceID.uuidString.lowercased().utf8),
            fields.clientNonce,
            fields.serverNonce,
            Data(fields.displayName.utf8),
            Data(fields.platform.utf8),
            Data(fields.osVersion.utf8),
            Data(fields.appVersion.utf8),
            fields.authenticationSPKI,
            fields.approvalSPKI,
            Data(String(fields.issuedAt).utf8),
            Data(String(fields.expiresAt).utf8),
        ]
        var result = transcriptDomain
        for value in values {
            guard value.count <= Int(UInt32.max) else {
                throw DeviceEnrollmentError.invalidServerResponse
            }
            var length = UInt32(value.count).bigEndian
            withUnsafeBytes(of: &length) { result.append(contentsOf: $0) }
            result.append(value)
        }
        return result
    }

    static func claimProof(pairingKey: Data, fields: EnrollmentClaimFields) throws -> Data {
        guard pairingKey.count == 32,
              EnrollmentCoding.isVersion4(fields.observedServerID),
              EnrollmentCoding.isVersion4(fields.offerID),
              EnrollmentCoding.isVersion4(fields.requestID),
              EnrollmentCoding.isVersion4(fields.clientInstanceID),
              fields.clientNonce.count == 32,
              isCanonicalP256SPKI(fields.authenticationSPKI),
              isCanonicalP256SPKI(fields.approvalSPKI)
        else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        let values: [Data] = [
            Data(fields.observedServerID.uuidString.lowercased().utf8),
            Data(fields.audienceOrigin.utf8),
            Data(fields.offerID.uuidString.lowercased().utf8),
            Data(fields.requestID.uuidString.lowercased().utf8),
            Data(fields.clientInstanceID.uuidString.lowercased().utf8),
            fields.clientNonce,
            Data(fields.displayName.utf8),
            Data(fields.platform.utf8),
            Data(fields.osVersion.utf8),
            Data(fields.appVersion.utf8),
            fields.authenticationSPKI,
            fields.approvalSPKI,
        ]
        var transcript = claimTranscriptDomain
        for value in values {
            guard value.count <= Int(UInt32.max) else {
                throw DeviceEnrollmentError.invalidServerResponse
            }
            var length = UInt32(value.count).bigEndian
            withUnsafeBytes(of: &length) { transcript.append(contentsOf: $0) }
            transcript.append(value)
        }
        return hmacSHA256(key: pairingKey, message: transcript)
    }

    static func signingEnvelope(
        serverID: UUID,
        audienceOrigin: String,
        offerID: UUID,
        requestID: UUID,
        challengeID: UUID,
        deviceID: UUID,
        purpose: EnrollmentKeyPurpose,
        keyFingerprint: String,
        transcriptDigest: Data,
        timestamp: Int64,
        nonce: Data
    ) throws -> Data {
        guard EnrollmentCoding.isVersion4(serverID),
              EnrollmentCoding.isVersion4(offerID),
              EnrollmentCoding.isVersion4(requestID),
              EnrollmentCoding.isVersion4(challengeID),
              EnrollmentCoding.isVersion4(deviceID),
              EnrollmentCoding.base64URLDecode(keyFingerprint)?.count == 32,
              transcriptDigest.count == 32,
              nonce.count == 32,
              timestamp >= 0
        else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        let text = [
            purpose.signingDomain,
            "server_id:\(serverID.uuidString.lowercased())",
            "audience_origin:\(audienceOrigin)",
            "offer_id:\(offerID.uuidString.lowercased())",
            "request_id:\(requestID.uuidString.lowercased())",
            "challenge_id:\(challengeID.uuidString.lowercased())",
            "device_id:\(deviceID.uuidString.lowercased())",
            "key_purpose:\(purpose.rawValue)",
            "key_fingerprint:\(keyFingerprint)",
            "transcript_sha256:\(EnrollmentCoding.base64URLEncode(transcriptDigest))",
            "timestamp:\(timestamp)",
            "nonce:\(EnrollmentCoding.base64URLEncode(nonce))",
            "",
        ].joined(separator: "\n")
        return Data(text.utf8)
    }

    static func sha256(_ data: Data) -> Data {
        Data(SHA256.hash(data: data))
    }

    static func hmacSHA256(key: Data, message: Data) -> Data {
        let authentication = HMAC<SHA256>.authenticationCode(
            for: message,
            using: SymmetricKey(data: key)
        )
        return Data(authentication)
    }

    static func keyFingerprint(_ spki: Data) -> String {
        EnrollmentCoding.base64URLEncode(sha256(spki))
    }

    static func serverProof(pairingKey: Data, transcriptDigest: Data) -> Data {
        hmacSHA256(key: pairingKey, message: serverProofDomain + transcriptDigest)
    }

    static func clientProof(pairingKey: Data, transcriptDigest: Data) -> Data {
        hmacSHA256(key: pairingKey, message: clientProofDomain + transcriptDigest)
    }

    static func uuidBytes(_ value: UUID) -> Data {
        var uuid = value.uuid
        return withUnsafeBytes(of: &uuid) { Data($0) }
    }

    static func isCanonicalP256DERSignature(_ data: Data) -> Bool {
        let bytes = [UInt8](data)
        guard (8...72).contains(bytes.count), bytes[0] == 0x30,
              Int(bytes[1]) == bytes.count - 2, bytes[2] == 0x02
        else { return false }
        let rLength = Int(bytes[3])
        let rStart = 4
        let sTag = rStart + rLength
        guard (1...33).contains(rLength), sTag + 2 < bytes.count,
              bytes[sTag] == 0x02
        else { return false }
        let sLength = Int(bytes[sTag + 1])
        let sStart = sTag + 2
        guard (1...33).contains(sLength), sStart + sLength == bytes.count,
              canonicalPositiveInteger(Array(bytes[rStart..<sTag])),
              canonicalPositiveInteger(Array(bytes[sStart..<bytes.count]))
        else { return false }
        return true
    }

    private static func canonicalPositiveInteger(_ value: [UInt8]) -> Bool {
        guard !value.isEmpty, value.count <= 33 else { return false }
        if value[0] & 0x80 != 0 { return false }
        if value.count > 1, value[0] == 0, value[1] & 0x80 == 0 { return false }
        if value.count == 33, value[0] != 0 { return false }
        return value.contains(where: { $0 != 0 })
    }
}
