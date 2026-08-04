import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum EnrollmentPlatform: String, Sendable {
    case iOS = "ios"
    case iPadOS = "ipados"
    case macOS = "macos"
}

/// Device metadata submitted during enrollment.
public struct EnrollmentDeviceMetadata: Equatable, Sendable {
    public let clientInstanceID: UUID
    public let displayName: String
    public let platform: EnrollmentPlatform
    public let osVersion: String
    public let appVersion: String

    public init(
        clientInstanceID: UUID,
        displayName: String,
        platform: EnrollmentPlatform,
        osVersion: String,
        appVersion: String
    ) throws {
        let name = displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name.count <= 80 else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        guard osVersion.count <= 40, appVersion.count <= 40 else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        self.clientInstanceID = clientInstanceID
        self.displayName = name
        self.platform = platform
        self.osVersion = osVersion
        self.appVersion = appVersion
    }
}

public protocol EnrollmentClock: Sendable {
    func now() -> Date
}

public struct SystemEnrollmentClock: EnrollmentClock {
    public init() {}
    public func now() -> Date { Date() }
}

public protocol EnrollmentUUIDSource: Sendable {
    func next() -> UUID
}

public struct SystemUUIDSource: EnrollmentUUIDSource {
    public init() {}
    public func next() -> UUID { UUID() }
}

public struct EnrollmentHTTPResponse: Sendable {
    public let finalURL: URL
    public let statusCode: Int
    public let headers: [String: String]
    public let body: Data

    public init(finalURL: URL, statusCode: Int, headers: [String: String], body: Data) {
        self.finalURL = finalURL
        self.statusCode = statusCode
        self.headers = headers
        self.body = body
    }
}

public protocol EnrollmentHTTPTransport: Sendable {
    func send(_ request: URLRequest, maximumResponseBytes: Int) async throws -> EnrollmentHTTPResponse
}

/// Ephemeral, cookie-free, redirect-refusing transport for enrollment traffic.
public final class URLSessionEnrollmentTransport: EnrollmentHTTPTransport, @unchecked Sendable {
    private let session: URLSession
    private let delegate: NoRedirectTaskDelegate

    public init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.waitsForConnectivity = false
        let delegate = NoRedirectTaskDelegate()
        self.delegate = delegate
        session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
    }

    public func send(_ request: URLRequest, maximumResponseBytes: Int) async throws -> EnrollmentHTTPResponse {
        let (bytes, rawResponse) = try await session.bytes(for: request)
        guard let response = rawResponse as? HTTPURLResponse,
              let finalURL = response.url
        else {
            throw DeviceEnrollmentError.networkUnavailable
        }
        var body = Data()
        for try await byte in bytes {
            guard body.count < maximumResponseBytes else {
                throw DeviceEnrollmentError.invalidServerResponse
            }
            body.append(byte)
        }
        let headers = response.allHeaderFields.reduce(into: [String: String]()) { result, item in
            guard let key = item.key as? String else { return }
            result[key] = String(describing: item.value)
        }
        return EnrollmentHTTPResponse(
            finalURL: finalURL,
            statusCode: response.statusCode,
            headers: headers,
            body: body
        )
    }
}

private final class NoRedirectTaskDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

/// The reviewed, not-yet-signed enrollment state returned by `prepare`.
public struct PreparedDeviceEnrollment: Sendable {
    public let review: DeviceEnrollmentReview
    let authenticationPayload: Data
    let approvalPayload: Data
    let pairingKey: Data
    let transcriptDigest: Data
    let keys: EnrollmentKeySet
}

/// The verified server challenge summary shown for explicit review before any
/// key signs anything.
public struct DeviceEnrollmentReview: Equatable, Sendable {
    public let observedServerID: UUID
    public let audienceOrigin: String
    public let offerID: UUID
    public let requestID: UUID
    public let challengeID: UUID
    public let deviceID: UUID
    public let issuedAt: Date
    public let expiresAt: Date
    public let authenticationKeyFingerprint: String
    public let approvalKeyFingerprint: String

    public var isExpired(now: Date = Date()) -> Bool {
        expiresAt <= now
    }
}

/// The receipt a server returns once a signed completion is accepted.
public struct DeviceEnrollmentReceipt: Equatable, Sendable {
    public let enrollmentID: UUID
    public let deviceID: UUID
    public let credentialID: String
    public let observedServerID: UUID
    public let audienceOrigin: String
    public let state: String
    public let enrolledAt: Date
    public let authenticationKeyFingerprint: String
    public let approvalKeyFingerprint: String
    public let authorizationNotice: String
}

/// A signed enrollment completion that can be resumed byte-for-byte with the
/// same idempotency key after a timeout or restart.
public struct SignedDeviceEnrollmentCompletion: Equatable, Sendable {
    public static let maximumResumeDocumentBytes = 16_384
    public static let authorizationNotice =
        "Paired device has no role, session, approval, or execution authority."

    public let review: DeviceEnrollmentReview
    public let idempotencyKey: UUID
    public let completionURL: URL
    public let requestBody: Data
    public let transcriptDigest: Data
    public let clientProof: Data

    init(
        review: DeviceEnrollmentReview,
        idempotencyKey: UUID,
        completionURL: URL,
        requestBody: Data,
        transcriptDigest: Data,
        clientProof: Data
    ) {
        self.review = review
        self.idempotencyKey = idempotencyKey
        self.completionURL = completionURL
        self.requestBody = requestBody
        self.transcriptDigest = transcriptDigest
        self.clientProof = clientProof
    }

    public func resumeData() throws -> Data {
        let document: [String: Any] = [
            "schema_version": 1,
            "idempotency_key": idempotencyKey.uuidString.lowercased(),
            "challenge_id": review.challengeID.uuidString.lowercased(),
            "completion_url": completionURL.absoluteString,
            "request_body_base64url": EnrollmentCoding.base64URLEncode(requestBody),
            "review": review.makeDocument(),
        ]
        let data = try JSONSerialization.data(withJSONObject: document, options: [.sortedKeys])
        guard !data.isEmpty, data.count <= maximumResumeDocumentBytes else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        return data
    }

    public static func resume(from data: Data) throws -> SignedDeviceEnrollmentCompletion {
        guard !data.isEmpty, data.count <= maximumResumeDocumentBytes else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        guard let schema = object["schema_version"] as? Int, schema == 1 else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        guard let rawID = object["idempotency_key"] as? String,
              let idempotencyKey = UUID(uuidString: rawID),
              EnrollmentCoding.isVersion4(idempotencyKey),
              rawID == idempotencyKey.uuidString.lowercased()
        else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        guard let reviewValue = object["review"] as? [String: Any],
              let review = try? DeviceEnrollmentReview(document: reviewValue)
        else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        guard let rawURL = object["completion_url"] as? String,
              let completionURL = URL(string: rawURL),
              Self.isCanonicalCompletionURL(completionURL, review: review)
        else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        guard let encodedBody = object["request_body_base64url"] as? String,
              let requestBody = EnrollmentCoding.base64URLDecode(encodedBody),
              EnrollmentCoding.base64URLEncode(requestBody) == encodedBody,
              Self.requestBodyMatches(requestBody, idempotencyKey: idempotencyKey, challengeID: review.challengeID)
        else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        guard let objectJSON = try? JSONSerialization.jsonObject(with: requestBody) as? [String: Any],
              let transcriptRaw = objectJSON["transcript_sha256"] as? String,
              let digestData = EnrollmentCoding.base64URLDecode(transcriptRaw),
              let clientProofRaw = objectJSON["client_proof"] as? String,
              let clientProof = EnrollmentCoding.base64URLDecode(clientProofRaw)
        else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        return SignedDeviceEnrollmentCompletion(
            review: review,
            idempotencyKey: idempotencyKey,
            completionURL: completionURL,
            requestBody: requestBody,
            transcriptDigest: digestData,
            clientProof: clientProof
        )
    }

    private static func isCanonicalCompletionURL(
        _ url: URL,
        review: DeviceEnrollmentReview
    ) -> Bool {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.scheme == "http" || components.scheme == "https",
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              let host = components.host?.lowercased(),
              host == review.audienceOriginHost
        else {
            return false
        }
        let expectedPath = "/api/v1/device-enrollment/challenges/\(review.challengeID.uuidString.lowercased())/complete"
        return components.path == expectedPath
    }

    private static func requestBodyMatches(
        _ body: Data,
        idempotencyKey: UUID,
        challengeID: UUID
    ) -> Bool {
        guard let object = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
              object["idempotency_key"] as? String == idempotencyKey.uuidString.lowercased(),
              object["challenge_id"] as? String == challengeID.uuidString.lowercased(),
              object["schema_version"] as? Int == 1,
              object["transcript_sha256"] as? String != nil,
              object["client_proof"] as? String != nil,
              object["device_authentication_signature"] as? String != nil,
              object["approval_signature"] as? String != nil
        else {
            return false
        }
        return true
    }
}

// MARK: - Client

/// Phased `prepare` → review → `confirm` → `complete` enrollment client.
public struct DeviceEnrollmentClient: Sendable {
    private static let maximumResponseBytes = 65_536
    private static let challengePath = "/api/v1/device-enrollment/challenges"

    private let transport: any EnrollmentHTTPTransport
    private let keyProvider: any EnrollmentKeyProviding
    private let randomSource: any EnrollmentRandomSource
    private let clock: any EnrollmentClock
    private let uuidSource: any EnrollmentUUIDSource

    public init(
        transport: any EnrollmentHTTPTransport = URLSessionEnrollmentTransport(),
        keyProvider: any EnrollmentKeyProviding,
        randomSource: any EnrollmentRandomSource = SecureEnrollmentRandomSource(),
        clock: any EnrollmentClock = SystemEnrollmentClock(),
        uuidSource: any EnrollmentUUIDSource = SystemUUIDSource()
    ) {
        self.transport = transport
        self.keyProvider = keyProvider
        self.randomSource = randomSource
        self.clock = clock
        self.uuidSource = uuidSource
    }

    /// Verifies the pairing code, claims the offer, and verifies the full server
    /// transcript. No key signs anything until `confirm` is called after review.
    public func prepare(
        manualCode: String,
        observedServerID: UUID,
        device: EnrollmentDeviceMetadata
    ) async throws -> PreparedDeviceEnrollment {
        let pairing = try JoeOSPairingCode(manualCode)
        let pairingKey = try DeviceEnrollmentProtocol.derivePairingKey(
            secret: pairing.pairingSecret,
            offerID: pairing.offerID
        )
        let clientNonce = try randomSource.bytes(count: 32)
        let keys = try await keyProvider.loadOrCreateKeys()
        let authenticationSPKI = try await keys.deviceAuthentication.publicKeySPKIDER()
        let approvalSPKI = try await keys.approval.publicKeySPKIDER()
        let requestID = uuidSource.next()
        let claimProof = try DeviceEnrollmentProtocol.claimProof(
            pairingKey: pairingKey,
            fields: EnrollmentClaimFields(
                observedServerID: observedServerID,
                audienceOrigin: pairing.audienceOrigin.value,
                offerID: pairing.offerID,
                requestID: requestID,
                clientInstanceID: device.clientInstanceID,
                clientNonce: clientNonce,
                displayName: device.displayName,
                platform: device.platform.rawValue,
                osVersion: device.osVersion,
                appVersion: device.appVersion,
                authenticationSPKI: authenticationSPKI,
                approvalSPKI: approvalSPKI
            )
        )
        guard let challengeURL = URL(string: pairing.audienceOrigin.value + Self.challengePath) else {
            throw DeviceEnrollmentError.invalidOrigin
        }
        let request = try Self.makeChallengeRequest(
            url: challengeURL,
            requestID: requestID,
            observedServerID: observedServerID,
            claimProof: claimProof,
            device: device
        )
        let response = try await transport.send(request, maximumResponseBytes: Self.maximumResponseBytes)
        guard response.statusCode == 201, response.finalURL == challengeURL else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        guard let document = try? JSONSerialization.jsonObject(with: response.body) as? [String: Any] else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        return try Self.verifyChallenge(
            document,
            pairing: pairing,
            pairingKey: pairingKey,
            observedServerID: observedServerID,
            requestID: requestID,
            device: device,
            authenticationSPKI: authenticationSPKI,
            approvalSPKI: approvalSPKI,
            clientNonce: clientNonce,
            keys: keys,
            now: clock.now()
        )
    }

    /// Signs both enrollment envelopes. In production the approval signature is
    /// protected by the current biometric enrollment.
    public func confirm(_ prepared: PreparedDeviceEnrollment) async throws -> SignedDeviceEnrollmentCompletion {
        let authenticationSignature = try await prepared.keys.deviceAuthentication.signature(
            for: prepared.authenticationPayload
        )
        let approvalSignature = try await prepared.keys.approval.signature(
            for: prepared.approvalPayload
        )
        let idempotencyKey = uuidSource.next()
        let clientProof = DeviceEnrollmentProtocol.clientProof(
            pairingKey: prepared.pairingKey,
            transcriptDigest: prepared.transcriptDigest
        )
        let body: [String: Any] = [
            "schema_version": 1,
            "idempotency_key": idempotencyKey.uuidString.lowercased(),
            "challenge_id": prepared.review.challengeID.uuidString.lowercased(),
            "transcript_sha256": EnrollmentCoding.base64URLEncode(prepared.transcriptDigest),
            "client_proof": EnrollmentCoding.base64URLEncode(clientProof),
            "device_authentication_signature": EnrollmentCoding.base64URLEncode(authenticationSignature),
            "approval_signature": EnrollmentCoding.base64URLEncode(approvalSignature),
        ]
        let requestBody = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        let completionURL = prepared.review.audienceOrigin +
            "/api/v1/device-enrollment/challenges/\(prepared.review.challengeID.uuidString.lowercased())/complete"
        guard let url = URL(string: completionURL) else {
            throw DeviceEnrollmentError.invalidOrigin
        }
        return SignedDeviceEnrollmentCompletion(
            review: prepared.review,
            idempotencyKey: idempotencyKey,
            completionURL: url,
            requestBody: requestBody,
            transcriptDigest: prepared.transcriptDigest,
            clientProof: clientProof
        )
    }

    /// Posts the exact signed completion. Idempotent: the same completion may
    /// be retried byte-for-byte.
    public func complete(_ signed: SignedDeviceEnrollmentCompletion) async throws -> DeviceEnrollmentReceipt {
        var request = URLRequest(
            url: signed.completionURL,
            cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
            timeoutInterval: 15
        )
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = signed.requestBody
        let response = try await transport.send(request, maximumResponseBytes: Self.maximumResponseBytes)
        guard response.statusCode == 200, response.finalURL == signed.completionURL else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        guard let document = try? JSONSerialization.jsonObject(with: response.body) as? [String: Any] else {
            throw DeviceEnrollmentError.invalidReceipt
        }
        return try Self.verifyReceipt(document, signed: signed)
    }

    // MARK: - Request / response verification

    private static func makeChallengeRequest(
        url: URL,
        requestID: UUID,
        observedServerID: UUID,
        claimProof: Data,
        device: EnrollmentDeviceMetadata
    ) throws -> URLRequest {
        var request = URLRequest(
            url: url,
            cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
            timeoutInterval: 15
        )
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let body: [String: Any] = [
            "schema_version": 1,
            "protocol": "joeos-device-enrollment-v1",
            "request_id": requestID.uuidString.lowercased(),
            "observed_server_id": observedServerID.uuidString.lowercased(),
            "claim_proof": EnrollmentCoding.base64URLEncode(claimProof),
            "device": [
                "client_instance_id": device.clientInstanceID.uuidString.lowercased(),
                "display_name": device.displayName,
                "platform": device.platform.rawValue,
                "os_version": device.osVersion,
                "app_version": device.appVersion,
            ],
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        return request
    }

    private static func verifyChallenge(
        _ document: [String: Any],
        pairing: JoeOSPairingCode,
        pairingKey: Data,
        observedServerID: UUID,
        requestID: UUID,
        device: EnrollmentDeviceMetadata,
        authenticationSPKI: Data,
        approvalSPKI: Data,
        clientNonce: Data,
        keys: EnrollmentKeySet,
        now: Date
    ) throws -> PreparedDeviceEnrollment {
        guard document["schema_version"] as? Int == 1,
              document["protocol"] as? String == "joeos-device-enrollment-v1",
              document["request_id"] as? String == requestID.uuidString.lowercased(),
              document["observed_server_id"] as? String == observedServerID.uuidString.lowercased(),
              document["audience_origin"] as? String == pairing.audienceOrigin.value,
              let offerID = uuid(document["offer_id"], isVersion4: true),
              offerID == pairing.offerID,
              let challengeID = uuid(document["challenge_id"], isVersion4: true),
              let deviceID = uuid(document["device_id"], isVersion4: true)
        else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        guard let issuedAt = isoDate(document["issued_at"]),
              let expiresAt = isoDate(document["expires_at"]),
              expiresAt > issuedAt
        else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        if expiresAt <= now {
            throw DeviceEnrollmentError.challengeExpired
        }
        guard let serverNonce = base64url32(document["server_nonce"]) else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        let issuedEpoch = Int64(issuedAt.timeIntervalSince1970)
        let expiresEpoch = Int64(expiresAt.timeIntervalSince1970)
        let transcript: Data
        do {
            transcript = try DeviceEnrollmentProtocol.buildTranscript(
                EnrollmentTranscriptFields(
                    serverID: observedServerID,
                    audienceOrigin: pairing.audienceOrigin.value,
                    offerID: pairing.offerID,
                    requestID: requestID,
                    challengeID: challengeID,
                    deviceID: deviceID,
                    clientInstanceID: device.clientInstanceID,
                    clientNonce: clientNonce,
                    serverNonce: serverNonce,
                    displayName: device.displayName,
                    platform: device.platform.rawValue,
                    osVersion: device.osVersion,
                    appVersion: device.appVersion,
                    authenticationSPKI: authenticationSPKI,
                    approvalSPKI: approvalSPKI,
                    issuedAt: issuedEpoch,
                    expiresAt: expiresEpoch
                )
            )
        } catch {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        let digest = DeviceEnrollmentProtocol.sha256(transcript)
        guard document["transcript_sha256"] as? String == EnrollmentCoding.base64URLEncode(digest),
              document["server_proof"] as? String == EnrollmentCoding.base64URLEncode(
                  DeviceEnrollmentProtocol.serverProof(pairingKey: pairingKey, transcriptDigest: digest)
              )
        else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        let authenticationFingerprint = DeviceEnrollmentProtocol.keyFingerprint(authenticationSPKI)
        let approvalFingerprint = DeviceEnrollmentProtocol.keyFingerprint(approvalSPKI)
        let authenticationEnvelope = try DeviceEnrollmentProtocol.signingEnvelope(
            serverID: observedServerID,
            audienceOrigin: pairing.audienceOrigin.value,
            offerID: pairing.offerID,
            requestID: requestID,
            challengeID: challengeID,
            deviceID: deviceID,
            purpose: .deviceAuthentication,
            keyFingerprint: authenticationFingerprint,
            transcriptDigest: digest,
            timestamp: issuedEpoch,
            nonce: serverNonce
        )
        let approvalEnvelope = try DeviceEnrollmentProtocol.signingEnvelope(
            serverID: observedServerID,
            audienceOrigin: pairing.audienceOrigin.value,
            offerID: pairing.offerID,
            requestID: requestID,
            challengeID: challengeID,
            deviceID: deviceID,
            purpose: .approval,
            keyFingerprint: approvalFingerprint,
            transcriptDigest: digest,
            timestamp: issuedEpoch,
            nonce: serverNonce
        )
        guard document["device_authentication_payload"] as? String == EnrollmentCoding.base64URLEncode(authenticationEnvelope),
              document["approval_payload"] as? String == EnrollmentCoding.base64URLEncode(approvalEnvelope)
        else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        let review = DeviceEnrollmentReview(
            observedServerID: observedServerID,
            audienceOrigin: pairing.audienceOrigin.value,
            offerID: pairing.offerID,
            requestID: requestID,
            challengeID: challengeID,
            deviceID: deviceID,
            issuedAt: issuedAt,
            expiresAt: expiresAt,
            authenticationKeyFingerprint: authenticationFingerprint,
            approvalKeyFingerprint: approvalFingerprint
        )
        return PreparedDeviceEnrollment(
            review: review,
            authenticationPayload: authenticationEnvelope,
            approvalPayload: approvalEnvelope,
            pairingKey: pairingKey,
            transcriptDigest: digest,
            keys: keys
        )
    }

    private static func verifyReceipt(
        _ document: [String: Any],
        signed: SignedDeviceEnrollmentCompletion
    ) throws -> DeviceEnrollmentReceipt {
        guard document["schema_version"] as? Int == 1,
              document["state"] as? String == "active_unassigned",
              document["observed_server_id"] as? String == signed.review.observedServerID.uuidString.lowercased(),
              document["audience_origin"] as? String == signed.review.audienceOrigin,
              document["authentication_key_fingerprint"] as? String == signed.review.authenticationKeyFingerprint,
              document["approval_key_fingerprint"] as? String == signed.review.approvalKeyFingerprint,
              document["authorization_notice"] as? String == SignedDeviceEnrollmentCompletion.authorizationNotice,
              let enrollmentID = uuid(document["enrollment_id"], isVersion4: true),
              let deviceID = uuid(document["device_id"], isVersion4: true),
              let credentialID = document["credential_id"] as? String,
              EnrollmentCoding.isCanonicalBase64URL32(credentialID),
              let enrolledAt = isoDate(document["enrolled_at"])
        else {
            throw DeviceEnrollmentError.invalidReceipt
        }
        return DeviceEnrollmentReceipt(
            enrollmentID: enrollmentID,
            deviceID: deviceID,
            credentialID: credentialID,
            observedServerID: signed.review.observedServerID,
            audienceOrigin: signed.review.audienceOrigin,
            state: "active_unassigned",
            enrolledAt: enrolledAt,
            authenticationKeyFingerprint: signed.review.authenticationKeyFingerprint,
            approvalKeyFingerprint: signed.review.approvalKeyFingerprint,
            authorizationNotice: SignedDeviceEnrollmentCompletion.authorizationNotice
        )
    }

    private static func uuid(_ value: Any?, isVersion4: Bool) -> UUID? {
        guard let raw = value as? String, let identifier = UUID(uuidString: raw) else {
            return nil
        }
        guard raw == identifier.uuidString.lowercased() else { return nil }
        guard !isVersion4 || EnrollmentCoding.isVersion4(identifier) else { return nil }
        return identifier
    }

    private static func base64url32(_ value: Any?) -> Data? {
        guard let raw = value as? String,
              let data = EnrollmentCoding.base64URLDecode(raw),
              data.count == 32,
              EnrollmentCoding.base64URLEncode(data) == raw
        else {
            return nil
        }
        return data
    }

    private static func isoDate(_ value: Any?) -> Date? {
        guard let raw = value as? String else { return nil }
        return ISO8601DateFormatter().date(from: raw)
    }
}

// MARK: - Review document (resume format)

extension DeviceEnrollmentReview {
    func makeDocument() -> [String: Any] {
        [
            "observed_server_id": observedServerID.uuidString.lowercased(),
            "audience_origin": audienceOrigin,
            "offer_id": offerID.uuidString.lowercased(),
            "request_id": requestID.uuidString.lowercased(),
            "challenge_id": challengeID.uuidString.lowercased(),
            "device_id": deviceID.uuidString.lowercased(),
            "issued_at_epoch": Int64(issuedAt.timeIntervalSince1970),
            "expires_at_epoch": Int64(expiresAt.timeIntervalSince1970),
            "authentication_key_fingerprint": authenticationKeyFingerprint,
            "approval_key_fingerprint": approvalKeyFingerprint,
        ]
    }

    init?(document: [String: Any]) {
        guard document["observed_server_id"] as? String != nil,
              let observedServerID = try? Self.uuid(document["observed_server_id"]),
              let audienceOrigin = document["audience_origin"] as? String,
              let canonicalOrigin = try? EnrollmentAudienceOrigin(audienceOrigin),
              canonicalOrigin.value == audienceOrigin,
              let offerID = try? Self.uuid(document["offer_id"]),
              let requestID = try? Self.uuid(document["request_id"]),
              let challengeID = try? Self.uuid(document["challenge_id"]),
              let deviceID = try? Self.uuid(document["device_id"]),
              let issuedEpoch = document["issued_at_epoch"] as? Int64,
              let expiresEpoch = document["expires_at_epoch"] as? Int64,
              (0...4_102_444_800).contains(issuedEpoch),
              expiresEpoch > issuedEpoch,
              (issuedEpoch...4_102_444_800).contains(expiresEpoch),
              let authenticationKeyFingerprint = document["authentication_key_fingerprint"] as? String,
              EnrollmentCoding.isCanonicalBase64URL32(authenticationKeyFingerprint),
              let approvalKeyFingerprint = document["approval_key_fingerprint"] as? String,
              EnrollmentCoding.isCanonicalBase64URL32(approvalKeyFingerprint),
              authenticationKeyFingerprint != approvalKeyFingerprint
        else {
            return nil
        }
        self.init(
            observedServerID: observedServerID,
            audienceOrigin: audienceOrigin,
            offerID: offerID,
            requestID: requestID,
            challengeID: challengeID,
            deviceID: deviceID,
            issuedAt: Date(timeIntervalSince1970: TimeInterval(issuedEpoch)),
            expiresAt: Date(timeIntervalSince1970: TimeInterval(expiresEpoch)),
            authenticationKeyFingerprint: authenticationKeyFingerprint,
            approvalKeyFingerprint: approvalKeyFingerprint
        )
    }

    private static func uuid(_ value: Any?) throws -> UUID {
        guard let raw = value as? String, let identifier = UUID(uuidString: raw) else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        guard raw == identifier.uuidString.lowercased(), EnrollmentCoding.isVersion4(identifier) else {
            throw DeviceEnrollmentError.malformedCompletionDocument
        }
        return identifier
    }
}

extension DeviceEnrollmentReview {
    var audienceOriginHost: String? {
        guard let url = URL(string: audienceOrigin) else { return nil }
        return url.host?.lowercased()
    }
}
