import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import CryptoKit
import XCTest
@testable import JoeOSCore

final class DeviceEnrollmentProtocolTests: XCTestCase {
    func testPythonFixedVectorMatchesClaimTranscriptAndProofs() throws {
        let pairing = try JoeOSPairingCode(Vector.manualCode)
        let pairingKey = try DeviceEnrollmentProtocol.derivePairingKey(
            secret: pairing.pairingSecret,
            offerID: Vector.offerID
        )
        XCTAssertEqual(
            EnrollmentCoding.base64URLEncode(pairingKey),
            "4BMBh9av90oSy1-rigJK2SFSedFUPSd2jRYG2FBVtDA"
        )

        let claim = try DeviceEnrollmentProtocol.claimProof(
            pairingKey: pairingKey,
            fields: EnrollmentClaimFields(
                observedServerID: Vector.serverID,
                audienceOrigin: Vector.origin,
                offerID: Vector.offerID,
                requestID: Vector.requestID,
                clientInstanceID: Vector.clientID,
                clientNonce: Vector.clientNonce,
                displayName: "Joe's iPhone",
                platform: "ios",
                osVersion: "17.6",
                appVersion: "1.0.0",
                authenticationSPKI: Vector.authenticationSPKI,
                approvalSPKI: Vector.approvalSPKI
            )
        )
        XCTAssertEqual(
            EnrollmentCoding.base64URLEncode(claim),
            "tkdAvAgxk4500N2hjqEpd6hywnYWkAo05cH9x2tV56U"
        )

        let transcript = try Vector.transcript()
        let digest = DeviceEnrollmentProtocol.sha256(transcript)
        XCTAssertEqual(
            EnrollmentCoding.base64URLEncode(digest),
            "ezavtehj02kUCcGsemgqasnBkGSmNxfn3_ucsVhjdco"
        )
        XCTAssertEqual(
            EnrollmentCoding.base64URLEncode(
                DeviceEnrollmentProtocol.serverProof(
                    pairingKey: pairingKey,
                    transcriptDigest: digest
                )
            ),
            "ZTE85lKLX8638pZD_YKlU8tfWY8DJTXfjSbZY3wz-IQ"
        )
        XCTAssertEqual(
            EnrollmentCoding.base64URLEncode(
                DeviceEnrollmentProtocol.clientProof(
                    pairingKey: pairingKey,
                    transcriptDigest: digest
                )
            ),
            "RpTF-gVU1RGDj7CaTqFc6m0-VhYmyd1Ho7EI2vlgtd0"
        )
        let envelope = try DeviceEnrollmentProtocol.signingEnvelope(
            serverID: Vector.serverID,
            audienceOrigin: Vector.origin,
            offerID: Vector.offerID,
            requestID: Vector.requestID,
            challengeID: Vector.challengeID,
            deviceID: Vector.deviceID,
            purpose: .deviceAuthentication,
            keyFingerprint: Vector.authenticationFingerprint,
            transcriptDigest: digest,
            timestamp: Vector.issuedAt,
            nonce: Vector.serverNonce
        )
        XCTAssertEqual(
            EnrollmentCoding.base64URLEncode(envelope),
            Vector.authenticationEnvelope
        )
    }

    func testManualCodeAndCanonicalOriginRejectMalleability() throws {
        XCTAssertThrowsError(try JoeOSPairingCode(Vector.manualCode.lowercased()))
        XCTAssertThrowsError(
            try JoeOSPairingCode(
                Vector.manualCode.replacingOccurrences(
                    of: "https://joeos.example.com",
                    with: "https://joeos.example.com:443"
                )
            )
        )
        XCTAssertThrowsError(
            try JoeOSPairingCode(
                Vector.manualCode.replacingOccurrences(
                    of: "https://joeos.example.com",
                    with: "http://192.168.1.20"
                )
            )
        )
        XCTAssertNoThrow(
            try EnrollmentAudienceOrigin("http://100.98.25.26:8080")
        )
        XCTAssertThrowsError(try EnrollmentAudienceOrigin("HTTPS://joeos.example.com"))
        XCTAssertThrowsError(try EnrollmentAudienceOrigin("https://[::ffff:c000:201]"))
    }

    func testRequestIDAndMetadataTamperingChangeBothBindings() throws {
        let pairing = try JoeOSPairingCode(Vector.manualCode)
        let key = try DeviceEnrollmentProtocol.derivePairingKey(
            secret: pairing.pairingSecret,
            offerID: pairing.offerID
        )
        let originalClaim = try Vector.claimProof(pairingKey: key, requestID: Vector.requestID)
        let changedID = UUID(uuidString: "44444444-5555-4666-8777-888888888888")!
        let changedClaim = try Vector.claimProof(pairingKey: key, requestID: changedID)
        XCTAssertNotEqual(originalClaim, changedClaim)

        let originalDigest = DeviceEnrollmentProtocol.sha256(try Vector.transcript())
        let changedDigest = DeviceEnrollmentProtocol.sha256(
            try Vector.transcript(requestID: changedID)
        )
        XCTAssertNotEqual(originalDigest, changedDigest)
    }

    func testTwoPhaseClientDoesNotSignUntilConfirmationAndRetryIsStable() async throws {
        let fixture = try ClientFixture()
        let prepared = try await fixture.client.prepare(
            manualCode: Vector.manualCode,
            observedServerID: Vector.serverID,
            device: Vector.metadata
        )
        XCTAssertEqual(prepared.review.observedServerID, Vector.serverID)
        let authenticationCountBefore = await fixture.authenticationKey.signatureCount()
        let approvalCountBefore = await fixture.approvalKey.signatureCount()
        XCTAssertEqual(authenticationCountBefore, 0)
        XCTAssertEqual(approvalCountBefore, 0)

        let signed = try await fixture.client.confirm(prepared)
        let authenticationCountAfter = await fixture.authenticationKey.signatureCount()
        let approvalCountAfter = await fixture.approvalKey.signatureCount()
        XCTAssertEqual(authenticationCountAfter, 1)
        XCTAssertEqual(approvalCountAfter, 1)

        let restored = try SignedDeviceEnrollmentCompletion.resume(from: signed.resumeData())
        XCTAssertEqual(restored.idempotencyKey, signed.idempotencyKey)
        let first = try await fixture.client.complete(restored)
        let second = try await fixture.client.complete(restored)
        XCTAssertEqual(first, second)
        let bodies = await fixture.transport.completionBodies()
        XCTAssertEqual(bodies.count, 2)
        XCTAssertEqual(bodies[0], bodies[1])
        let authenticationCountFinal = await fixture.authenticationKey.signatureCount()
        let approvalCountFinal = await fixture.approvalKey.signatureCount()
        XCTAssertEqual(authenticationCountFinal, 1)
        XCTAssertEqual(approvalCountFinal, 1)
    }

    func testClientRejectsTamperedRequestEchoAndServerProofBeforeSigning() async throws {
        for tamper in [VectorEnrollmentTransport.Tamper.requestID, .serverProof, .signingPayload] {
            let fixture = try ClientFixture(tamper: tamper)
            do {
                _ = try await fixture.client.prepare(
                    manualCode: Vector.manualCode,
                    observedServerID: Vector.serverID,
                    device: Vector.metadata
                )
                XCTFail("Expected tampered challenge to fail")
            } catch {
                XCTAssertTrue(error is DeviceEnrollmentError)
            }
            let authenticationCount = await fixture.authenticationKey.signatureCount()
            let approvalCount = await fixture.approvalKey.signatureCount()
            XCTAssertEqual(authenticationCount, 0)
            XCTAssertEqual(approvalCount, 0)
        }
    }

    func testResumeDocumentRejectsUnknownFieldsURLAndNoncanonicalBody() async throws {
        let fixture = try ClientFixture()
        let prepared = try await fixture.client.prepare(
            manualCode: Vector.manualCode,
            observedServerID: Vector.serverID,
            device: Vector.metadata
        )
        let signed = try await fixture.client.confirm(prepared)
        let original = try signed.resumeData()

        var unknown = try XCTUnwrap(
            JSONSerialization.jsonObject(with: original) as? [String: Any]
        )
        unknown["unexpected"] = true
        XCTAssertThrowsError(
            try SignedDeviceEnrollmentCompletion.resume(
                from: JSONSerialization.data(withJSONObject: unknown)
            )
        )

        var changedURL = try XCTUnwrap(
            JSONSerialization.jsonObject(with: original) as? [String: Any]
        )
        changedURL["completion_url"] = "https://attacker.example/api/v1/device-enrollment/challenges/\(Vector.challengeID)/complete"
        XCTAssertThrowsError(
            try SignedDeviceEnrollmentCompletion.resume(
                from: JSONSerialization.data(withJSONObject: changedURL)
            )
        )

        var changedBody = try XCTUnwrap(
            JSONSerialization.jsonObject(with: original) as? [String: Any]
        )
        let bodyValue = try XCTUnwrap(changedBody["request_body_base64url"] as? String)
        var body = try XCTUnwrap(EnrollmentCoding.base64URLDecode(bodyValue))
        body.append(0x20)
        changedBody["request_body_base64url"] = EnrollmentCoding.base64URLEncode(body)
        XCTAssertThrowsError(
            try SignedDeviceEnrollmentCompletion.resume(
                from: JSONSerialization.data(withJSONObject: changedBody)
            )
        )

        var extremeEpoch = try XCTUnwrap(
            JSONSerialization.jsonObject(with: original) as? [String: Any]
        )
        var review = try XCTUnwrap(extremeEpoch["review"] as? [String: Any])
        review["issued_at_epoch"] = Int64.max - 120
        review["expires_at_epoch"] = Int64.max
        extremeEpoch["review"] = review
        XCTAssertThrowsError(
            try SignedDeviceEnrollmentCompletion.resume(
                from: JSONSerialization.data(withJSONObject: extremeEpoch)
            )
        )
    }
}

private enum Vector {
    static let origin = "https://joeos.example.com"
    static let serverID = UUID(uuidString: "12345678-1234-4abc-8def-1234567890ab")!
    static let offerID = UUID(uuidString: "11111111-2222-4333-8444-555555555555")!
    static let requestID = UUID(uuidString: "33333333-4444-4555-8666-777777777777")!
    static let challengeID = UUID(uuidString: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")!
    static let deviceID = UUID(uuidString: "87654321-4321-4cba-8fed-ba0987654321")!
    static let clientID = UUID(uuidString: "01234567-89ab-4cde-8f01-234567890abc")!
    static let idempotencyID = UUID(uuidString: "99999999-8888-4777-8666-555555555555")!
    static let enrollmentID = UUID(uuidString: "77777777-6666-4555-8444-333333333333")!
    static let issuedAt: Int64 = 1_785_346_200
    static let expiresAt: Int64 = 1_785_346_320
    static let clientNonce = Data(UInt8(0x20)...UInt8(0x3f))
    static let serverNonce = Data(UInt8(0x40)...UInt8(0x5f))
    static let manualCode = "JOEOS1|https://joeos.example.com|11111111-2222-4333-8444-555555555555|AAAQEAYEAUDAOCAJBIFQYDIOB4IBCEQTCQKRMFYYDENBWHA5DYPQ"
    static let authenticationSPKI = EnrollmentCoding.base64URLDecode(
        "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEaxfR8uEsQkf4vOblY6RA8ncDfYEt6zOg9KE5RdiYwpZP40Li_hp_m47n60p8D54WK84zV2sxXs7LtkBoN79R9Q"
    )!
    static let approvalSPKI = EnrollmentCoding.base64URLDecode(
        "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEfPJ7GI0DT36KUjgDBLUaw8CJaeJ38hs1pgtI_EdmmXgHd1UQ247QQCk9msafdDDbun2t5jzpgimeBLedInhz0Q"
    )!
    static let authenticationFingerprint = "XNJS-wzokyQ2-vjM0QQJgbie5K1rn-niorfnGqyyfNM"
    static let approvalFingerprint = "3AzmM9vMkT2vr6S4msRNjOaD_fw_YMi98hITufK1NLo"
    static let authenticationEnvelope = "Sk9FT1MtREVWSUNFLUVOUk9MTE1FTlQtUFJPT0YtVjE6REVWSUNFLUFVVEhFTlRJQ0FUSU9OCnNlcnZlcl9pZDoxMjM0NTY3OC0xMjM0LTRhYmMtOGRlZi0xMjM0NTY3ODkwYWIKYXVkaWVuY2Vfb3JpZ2luOmh0dHBzOi8vam9lb3MuZXhhbXBsZS5jb20Kb2ZmZXJfaWQ6MTExMTExMTEtMjIyMi00MzMzLTg0NDQtNTU1NTU1NTU1NTU1CnJlcXVlc3RfaWQ6MzMzMzMzMzMtNDQ0NC00NTU1LTg2NjYtNzc3Nzc3Nzc3Nzc3CmNoYWxsZW5nZV9pZDphYWFhYWFhYS1iYmJiLTRjY2MtOGRkZC1lZWVlZWVlZWVlZWUKZGV2aWNlX2lkOjg3NjU0MzIxLTQzMjEtNGNiYS04ZmVkLWJhMDk4NzY1NDMyMQprZXlfcHVycG9zZTpkZXZpY2VfYXV0aGVudGljYXRpb24Ka2V5X2ZpbmdlcnByaW50OlhOSlMtd3pva3lRMi12ak0wUVFKZ2JpZTVLMXJuLW5pb3JmbkdxeXlmTk0KdHJhbnNjcmlwdF9zaGEyNTY6ZXphdnRlaGowMmtVQ2NHc2VtZ3Fhc25Ca0dTbU54Zm4zX3Vjc1ZoamRjbwp0aW1lc3RhbXA6MTc4NTM0NjIwMApub25jZTpRRUZDUTBSRlJrZElTVXBMVEUxT1QxQlJVbE5VVlZaWFdGbGFXMXhkWGw4Cg"

    static var metadata: EnrollmentDeviceMetadata {
        get throws {
            try EnrollmentDeviceMetadata(
                clientInstanceID: clientID,
                displayName: "Joe's iPhone",
                platform: .iOS,
                osVersion: "17.6",
                appVersion: "1.0.0"
            )
        }
    }

    static func transcript(requestID: UUID = requestID) throws -> Data {
        try DeviceEnrollmentProtocol.buildTranscript(
            EnrollmentTranscriptFields(
                serverID: serverID,
                audienceOrigin: origin,
                offerID: offerID,
                requestID: requestID,
                challengeID: challengeID,
                deviceID: deviceID,
                clientInstanceID: clientID,
                clientNonce: clientNonce,
                serverNonce: serverNonce,
                displayName: "Joe's iPhone",
                platform: "ios",
                osVersion: "17.6",
                appVersion: "1.0.0",
                authenticationSPKI: authenticationSPKI,
                approvalSPKI: approvalSPKI,
                issuedAt: issuedAt,
                expiresAt: expiresAt
            )
        )
    }

    static func claimProof(pairingKey: Data, requestID: UUID) throws -> Data {
        try DeviceEnrollmentProtocol.claimProof(
            pairingKey: pairingKey,
            fields: EnrollmentClaimFields(
                observedServerID: serverID,
                audienceOrigin: origin,
                offerID: offerID,
                requestID: requestID,
                clientInstanceID: clientID,
                clientNonce: clientNonce,
                displayName: "Joe's iPhone",
                platform: "ios",
                osVersion: "17.6",
                appVersion: "1.0.0",
                authenticationSPKI: authenticationSPKI,
                approvalSPKI: approvalSPKI
            )
        )
    }
}

private actor CountingSigningKey: EnrollmentSigningKey {
    private let key: P256.Signing.PrivateKey
    private var signatures = 0

    init(scalar: UInt8) throws {
        var raw = Data(repeating: 0, count: 32)
        raw[31] = scalar
        key = try P256.Signing.PrivateKey(rawRepresentation: raw)
    }

    func publicKeySPKIDER() throws -> Data {
        try DeviceEnrollmentProtocol.subjectPublicKeyInfo(x963Representation: key.publicKey.x963Representation)
    }

    func signature(for message: Data) throws -> Data {
        signatures += 1
        return try key.signature(for: message).derRepresentation
    }

    func signatureCount() -> Int { signatures }
}

private struct FixedKeyProvider: EnrollmentKeyProviding {
    let authentication: CountingSigningKey
    let approval: CountingSigningKey

    func loadOrCreateKeys() -> EnrollmentKeySet {
        EnrollmentKeySet(deviceAuthentication: authentication, approval: approval)
    }
}

private struct FixedRandomSource: EnrollmentRandomSource {
    func bytes(count: Int) throws -> Data {
        guard count == 32 else { throw DeviceEnrollmentError.keyGenerationFailed }
        return Vector.clientNonce
    }
}

private struct FixedClock: EnrollmentClock {
    func now() -> Date { Date(timeIntervalSince1970: TimeInterval(Vector.issuedAt + 10)) }
}

private final class SequenceUUIDSource: EnrollmentUUIDSource, @unchecked Sendable {
    private let lock = NSLock()
    private var values = [Vector.requestID, Vector.idempotencyID]

    func next() -> UUID {
        lock.lock()
        defer { lock.unlock() }
        return values.removeFirst()
    }
}

private struct ClientFixture {
    let authenticationKey: CountingSigningKey
    let approvalKey: CountingSigningKey
    let transport: VectorEnrollmentTransport
    let client: DeviceEnrollmentClient

    init(tamper: VectorEnrollmentTransport.Tamper = .none) throws {
        let authenticationKey = try CountingSigningKey(scalar: 1)
        let approvalKey = try CountingSigningKey(scalar: 2)
        let transport = VectorEnrollmentTransport(tamper: tamper)
        self.authenticationKey = authenticationKey
        self.approvalKey = approvalKey
        self.transport = transport
        client = DeviceEnrollmentClient(
            transport: transport,
            keyProvider: FixedKeyProvider(authentication: authenticationKey, approval: approvalKey),
            randomSource: FixedRandomSource(),
            clock: FixedClock(),
            uuidSource: SequenceUUIDSource()
        )
    }
}

private actor VectorEnrollmentTransport: EnrollmentHTTPTransport {
    enum Tamper { case none, requestID, serverProof, signingPayload }
    private let tamper: Tamper
    private var submittedCompletionBodies: [Data] = []

    init(tamper: Tamper) { self.tamper = tamper }

    func completionBodies() -> [Data] { submittedCompletionBodies }

    func send(_ request: URLRequest, maximumResponseBytes: Int) async throws -> EnrollmentHTTPResponse {
        guard let url = request.url, let body = request.httpBody else {
            throw DeviceEnrollmentError.invalidServerResponse
        }
        if url.path == "/api/v1/device-enrollment/challenges" {
            return try challengeResponse(url: url, requestBody: body)
        }
        submittedCompletionBodies.append(body)
        try verifyCompletion(body)
        let receipt: [String: Any] = [
            "schema_version": 1,
            "enrollment_id": Vector.enrollmentID.uuidString.lowercased(),
            "device_id": Vector.deviceID.uuidString.lowercased(),
            "credential_id": EnrollmentCoding.base64URLEncode(Data(repeating: 0x77, count: 32)),
            "observed_server_id": Vector.serverID.uuidString.lowercased(),
            "audience_origin": Vector.origin,
            "state": "active_unassigned",
            "enrolled_at": "2026-07-29T17:30:30Z",
            "authentication_key_fingerprint": Vector.authenticationFingerprint,
            "approval_key_fingerprint": Vector.approvalFingerprint,
            "authorization_notice": "Paired device has no role, session, approval, or execution authority.",
        ]
        return response(url: url, status: 200, document: receipt)
    }

    private func challengeResponse(url: URL, requestBody: Data) throws -> EnrollmentHTTPResponse {
        let request = try JSONSerialization.jsonObject(with: requestBody) as? [String: Any]
        guard request?["request_id"] as? String == Vector.requestID.uuidString.lowercased(),
              request?["observed_server_id"] as? String == Vector.serverID.uuidString.lowercased(),
              request?["claim_proof"] as? String == "tkdAvAgxk4500N2hjqEpd6hywnYWkAo05cH9x2tV56U"
        else { throw DeviceEnrollmentError.invalidServerResponse }

        let pairing = try JoeOSPairingCode(Vector.manualCode)
        let pairingKey = try DeviceEnrollmentProtocol.derivePairingKey(
            secret: pairing.pairingSecret,
            offerID: Vector.offerID
        )
        let digest = DeviceEnrollmentProtocol.sha256(try Vector.transcript())
        let authPayload = try DeviceEnrollmentProtocol.signingEnvelope(
            serverID: Vector.serverID,
            audienceOrigin: Vector.origin,
            offerID: Vector.offerID,
            requestID: Vector.requestID,
            challengeID: Vector.challengeID,
            deviceID: Vector.deviceID,
            purpose: .deviceAuthentication,
            keyFingerprint: Vector.authenticationFingerprint,
            transcriptDigest: digest,
            timestamp: Vector.issuedAt,
            nonce: Vector.serverNonce
        )
        let approvalPayload = try DeviceEnrollmentProtocol.signingEnvelope(
            serverID: Vector.serverID,
            audienceOrigin: Vector.origin,
            offerID: Vector.offerID,
            requestID: Vector.requestID,
            challengeID: Vector.challengeID,
            deviceID: Vector.deviceID,
            purpose: .approval,
            keyFingerprint: Vector.approvalFingerprint,
            transcriptDigest: digest,
            timestamp: Vector.issuedAt,
            nonce: Vector.serverNonce
        )
        var requestID = Vector.requestID.uuidString.lowercased()
        var proof = DeviceEnrollmentProtocol.serverProof(pairingKey: pairingKey, transcriptDigest: digest)
        var suppliedAuthenticationPayload = authPayload
        if tamper == .requestID { requestID = "44444444-5555-4666-8777-888888888888" }
        if tamper == .serverProof { proof[0] ^= 1 }
        if tamper == .signingPayload { suppliedAuthenticationPayload.append(0x20) }
        let document: [String: Any] = [
            "schema_version": 1,
            "protocol": "joeos-device-enrollment-v1",
            "request_id": requestID,
            "challenge_id": Vector.challengeID.uuidString.lowercased(),
            "offer_id": Vector.offerID.uuidString.lowercased(),
            "device_id": Vector.deviceID.uuidString.lowercased(),
            "observed_server_id": Vector.serverID.uuidString.lowercased(),
            "audience_origin": Vector.origin,
            "issued_at": "2026-07-29T17:30:00Z",
            "expires_at": "2026-07-29T17:32:00Z",
            "server_nonce": EnrollmentCoding.base64URLEncode(Vector.serverNonce),
            "transcript_sha256": EnrollmentCoding.base64URLEncode(digest),
            "server_proof": EnrollmentCoding.base64URLEncode(proof),
            "device_authentication_payload": EnrollmentCoding.base64URLEncode(suppliedAuthenticationPayload),
            "approval_payload": EnrollmentCoding.base64URLEncode(approvalPayload),
        ]
        return response(url: url, status: 201, document: document)
    }

    private func verifyCompletion(_ body: Data) throws {
        let document = try JSONSerialization.jsonObject(with: body) as? [String: Any]
        guard document?["idempotency_key"] as? String == Vector.idempotencyID.uuidString.lowercased(),
              document?["transcript_sha256"] as? String == "ezavtehj02kUCcGsemgqasnBkGSmNxfn3_ucsVhjdco",
              document?["client_proof"] as? String == "RpTF-gVU1RGDj7CaTqFc6m0-VhYmyd1Ho7EI2vlgtd0",
              let authRaw = document?["device_authentication_signature"] as? String,
              let approvalRaw = document?["approval_signature"] as? String,
              let authSignatureData = EnrollmentCoding.base64URLDecode(authRaw),
              let approvalSignatureData = EnrollmentCoding.base64URLDecode(approvalRaw)
        else { throw DeviceEnrollmentError.invalidServerResponse }
        let digest = DeviceEnrollmentProtocol.sha256(try Vector.transcript())
        let authPayload = try DeviceEnrollmentProtocol.signingEnvelope(
            serverID: Vector.serverID,
            audienceOrigin: Vector.origin,
            offerID: Vector.offerID,
            requestID: Vector.requestID,
            challengeID: Vector.challengeID,
            deviceID: Vector.deviceID,
            purpose: .deviceAuthentication,
            keyFingerprint: Vector.authenticationFingerprint,
            transcriptDigest: digest,
            timestamp: Vector.issuedAt,
            nonce: Vector.serverNonce
        )
        let approvalPayload = try DeviceEnrollmentProtocol.signingEnvelope(
            serverID: Vector.serverID,
            audienceOrigin: Vector.origin,
            offerID: Vector.offerID,
            requestID: Vector.requestID,
            challengeID: Vector.challengeID,
            deviceID: Vector.deviceID,
            purpose: .approval,
            keyFingerprint: Vector.approvalFingerprint,
            transcriptDigest: digest,
            timestamp: Vector.issuedAt,
            nonce: Vector.serverNonce
        )
        let authPublic = try P256.Signing.PublicKey(x963Representation: Vector.authenticationSPKI.suffix(65))
        let approvalPublic = try P256.Signing.PublicKey(x963Representation: Vector.approvalSPKI.suffix(65))
        let authSignature = try P256.Signing.ECDSASignature(derRepresentation: authSignatureData)
        let approvalSignature = try P256.Signing.ECDSASignature(derRepresentation: approvalSignatureData)
        guard authPublic.isValidSignature(authSignature, for: authPayload),
              approvalPublic.isValidSignature(approvalSignature, for: approvalPayload)
        else { throw DeviceEnrollmentError.signingFailed }
    }

    private func response(url: URL, status: Int, document: [String: Any]) -> EnrollmentHTTPResponse {
        EnrollmentHTTPResponse(
            finalURL: url,
            statusCode: status,
            headers: ["Content-Type": "application/json"],
            body: try! JSONSerialization.data(withJSONObject: document, options: [.sortedKeys])
        )
    }
}
