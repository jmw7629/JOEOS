import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import JoeOSCore

final class BootstrapDiscoveryTests: XCTestCase {
    func testStrictContractDecodesAndValidatesReportedLimitations() throws {
        let document = try JSONDecoder().decode(BootstrapDocument.self, from: Self.validPayload)
        let validated = try BootstrapContractValidator.validate(document)

        XCTAssertEqual(validated.displayName, "JoeOS Local Command Center")
        XCTAssertEqual(validated.serverVersion, "2.0.0")
        XCTAssertEqual(validated.observedServerID.uuidString.lowercased(), "12345678-1234-4abc-8def-1234567890ab")
        XCTAssertFalse(validated.hasApplicationAuthentication)
        XCTAssertTrue(validated.supportsLocalConsolePairing)
        XCTAssertFalse(validated.hasRoleBasedAccess)
        XCTAssertFalse(validated.hasPrivilegedActions)
        XCTAssertFalse(validated.document.deviceEnrollment.grantsAuthority)
    }

    func testStrictDecodingRejectsUnknownServerFields() {
        let payload = Self.validJSON.replacingOccurrences(
            of: "\"server_version\":\"2.0.0\"",
            with: "\"server_version\":\"2.0.0\",\"hostname\":\"do-not-trust\""
        )
        XCTAssertThrowsError(try JSONDecoder().decode(BootstrapDocument.self, from: Data(payload.utf8)))
    }

    func testValidatorRejectsNonV4ServerIdentifierWithoutTreatingUUIDAsTrust() throws {
        let payload = Self.validJSON.replacingOccurrences(of: "4abc", with: "1abc")
        let document = try JSONDecoder().decode(BootstrapDocument.self, from: Data(payload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(document)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .invalidServerID)
        }
    }

    func testValidatorRejectsUnsupportedSchemaAndUnsafePosture() throws {
        let schemaPayload = Self.validJSON.replacingOccurrences(of: "\"schema_version\":2", with: "\"schema_version\":1")
        let schemaDocument = try JSONDecoder().decode(BootstrapDocument.self, from: Data(schemaPayload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(schemaDocument)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .unsupportedSchemaVersion(1))
        }

        let posturePayload = Self.validJSON.replacingOccurrences(
            of: "\"public_internet_ready\":false",
            with: "\"public_internet_ready\":true"
        )
        let postureDocument = try JSONDecoder().decode(BootstrapDocument.self, from: Data(posturePayload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(postureDocument)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .unsafeSecurityPosture)
        }
    }

    func testStrictDecodingAndValidationRejectEnrollmentProfileDrift() throws {
        let unknownFieldPayload = Self.validJSON.replacingOccurrences(
            of: "\"grants_authority\":false",
            with: "\"grants_authority\":false,\"pairing_action\":\"none\""
        )
        XCTAssertThrowsError(
            try JSONDecoder().decode(BootstrapDocument.self, from: Data(unknownFieldPayload.utf8))
        )

        for (expected, replacement) in [
            ("\"challenge_ttl_seconds\":120", "\"challenge_ttl_seconds\":121"),
            ("\"required_key_purposes\":[\"device_authentication\",\"approval\"]", "\"required_key_purposes\":[\"approval\",\"device_authentication\"]"),
            ("\"grants_authority\":false", "\"grants_authority\":true"),
        ] {
            let payload = Self.validJSON.replacingOccurrences(of: expected, with: replacement)
            let document = try JSONDecoder().decode(BootstrapDocument.self, from: Data(payload.utf8))
            XCTAssertThrowsError(try BootstrapContractValidator.validate(document)) { error in
                XCTAssertEqual(error as? BootstrapContractViolation, .invalidDeviceEnrollmentProfile)
            }
        }
    }

    func testValidatorRejectsUnknownCapabilityRouteReference() throws {
        let payload = Self.validJSON.replacingOccurrences(
            of: "\"route_ids\":[\"bootstrap.discovery\"]",
            with: "\"route_ids\":[\"missing.route\"]"
        )
        let document = try JSONDecoder().decode(BootstrapDocument.self, from: Data(payload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(document)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .unknownRouteReference)
        }
    }

    func testValidatorMatchesSDKRouteAccessAndSecurityGateInvariants() throws {
        let accessPayload = Self.validJSON.replacingOccurrences(
            of: "\"id\":\"discovery.bootstrap\",\n          \"status\":\"available\",\n          \"access\":\"read_only\"",
            with: "\"id\":\"discovery.bootstrap\",\n          \"status\":\"available\",\n          \"access\":\"configuration\""
        )
        let accessDocument = try JSONDecoder().decode(BootstrapDocument.self, from: Data(accessPayload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(accessDocument)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .routeAccessMismatch)
        }

        let pathPayload = Self.validJSON.replacingOccurrences(
            of: "\"path\":\"/api/v1/bootstrap\"",
            with: "\"path\":\"//evil.example/bootstrap\""
        )
        let pathDocument = try JSONDecoder().decode(BootstrapDocument.self, from: Data(pathPayload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(pathDocument)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .invalidDescriptor)
        }

        let gatePayload = Self.validJSON.replacingOccurrences(
            of: "\"id\":\"secrets.management\"",
            with: "\"id\":\"secrets.missing\""
        )
        let gateDocument = try JSONDecoder().decode(BootstrapDocument.self, from: Data(gatePayload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(gateDocument)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .missingSecurityGate("secrets.management"))
        }
    }

    func testValidatorRequiresExactDiscoveryRouteAndCapability() throws {
        let payload = Self.validJSON.replacingOccurrences(
            of: "\"path\":\"/api/v1/bootstrap\"",
            with: "\"path\":\"/api/v1/bootstrap-v2\""
        )
        let document = try JSONDecoder().decode(BootstrapDocument.self, from: Data(payload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(document)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .missingDiscoveryContract)
        }
    }

    func testValidatorRequiresExactDeviceEnrollmentRoutesAndCapability() throws {
        let pathPayload = Self.validJSON.replacingOccurrences(
            of: "\"path\":\"/api/v1/device-enrollment/challenges\"",
            with: "\"path\":\"/api/v1/device-enrollment/challenge\""
        )
        let pathDocument = try JSONDecoder().decode(BootstrapDocument.self, from: Data(pathPayload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(pathDocument)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .missingDeviceEnrollmentContract)
        }

        let capabilityPayload = Self.validJSON.replacingOccurrences(
            of: "\"route_ids\":[\"device-enrollment.challenge\",\"device-enrollment.complete\"]",
            with: "\"route_ids\":[\"device-enrollment.complete\",\"device-enrollment.challenge\"]"
        )
        let capabilityDocument = try JSONDecoder().decode(BootstrapDocument.self, from: Data(capabilityPayload.utf8))
        XCTAssertThrowsError(try BootstrapContractValidator.validate(capabilityDocument)) { error in
            XCTAssertEqual(error as? BootstrapContractViolation, .missingDeviceEnrollmentContract)
        }
    }

    func testBootstrapURLIsExactAndSameOriginForTailscaleProfile() throws {
        let endpoint = try EndpointPolicy.validate("http://100.121.165.22:8080/some/base/path").get()
        let url = try BootstrapDiscoveryClient.bootstrapURL(from: endpoint)
        XCTAssertEqual(url.absoluteString, "http://100.121.165.22:8080/api/v1/bootstrap")
        XCTAssertEqual(EndpointOrigin(url: url), endpoint.origin)
    }

    func testClientUsesInjectedTransportWithBoundedGETRequest() async throws {
        let endpoint = try EndpointPolicy.validate(ConnectionProfile.defaultHalo.endpoint).get()
        let response = BootstrapHTTPResponse(
            finalURL: URL(string: "http://100.121.165.22:8080/api/v1/bootstrap")!,
            statusCode: 200,
            headers: ["Content-Type": "application/json", "Cache-Control": "no-store"],
            body: Self.validPayload
        )
        let transport = RecordingBootstrapTransport(response: response)
        let client = BootstrapDiscoveryClient(transport: transport)

        let result = try await client.discover(from: endpoint)
        let requests = await transport.recordedRequests()

        XCTAssertEqual(result.serverVersion, "2.0.0")
        XCTAssertEqual(requests.count, 1)
        XCTAssertEqual(requests[0].url?.absoluteString, "http://100.121.165.22:8080/api/v1/bootstrap")
        XCTAssertEqual(requests[0].httpMethod, "GET")
        XCTAssertEqual(requests[0].value(forHTTPHeaderField: "Accept"), "application/json")
        XCTAssertNil(requests[0].httpBody)
    }

    func testClientRejectsCrossOriginFinalResponse() async throws {
        let endpoint = try EndpointPolicy.validate(ConnectionProfile.defaultHalo.endpoint).get()
        let response = BootstrapHTTPResponse(
            finalURL: URL(string: "https://attacker.example/api/v1/bootstrap")!,
            statusCode: 200,
            headers: ["Content-Type": "application/json"],
            body: Self.validPayload
        )
        let client = BootstrapDiscoveryClient(transport: RecordingBootstrapTransport(response: response))
        await assertDiscoveryError(.unexpectedResponseURL) {
            try await client.discover(from: endpoint)
        }
    }

    func testClientRejectsSameOriginWrongFinalPath() async throws {
        let endpoint = try EndpointPolicy.validate(ConnectionProfile.defaultHalo.endpoint).get()
        let response = BootstrapHTTPResponse(
            finalURL: URL(string: "http://100.121.165.22:8080/api/v1/not-bootstrap")!,
            statusCode: 200,
            headers: ["Content-Type": "application/json"],
            body: Self.validPayload
        )
        let client = BootstrapDiscoveryClient(transport: RecordingBootstrapTransport(response: response))
        await assertDiscoveryError(.unexpectedResponseURL) {
            try await client.discover(from: endpoint)
        }
    }

    func testClientRejectsBadStatusContentTypeAndOversizedBody() async throws {
        let endpoint = try EndpointPolicy.validate(ConnectionProfile.defaultHalo.endpoint).get()
        let finalURL = URL(string: "http://100.121.165.22:8080/api/v1/bootstrap")!

        let notFound = BootstrapDiscoveryClient(
            transport: RecordingBootstrapTransport(
                response: .init(finalURL: finalURL, statusCode: 404, headers: ["Content-Type": "application/json"], body: Self.validPayload)
            )
        )
        await assertDiscoveryError(.unexpectedStatus(404)) {
            try await notFound.discover(from: endpoint)
        }

        let wrongType = BootstrapDiscoveryClient(
            transport: RecordingBootstrapTransport(
                response: .init(finalURL: finalURL, statusCode: 200, headers: ["Content-Type": "text/html"], body: Self.validPayload)
            )
        )
        await assertDiscoveryError(.invalidContentType("text/html")) {
            try await wrongType.discover(from: endpoint)
        }

        let oversized = BootstrapDiscoveryClient(
            transport: RecordingBootstrapTransport(
                response: .init(
                    finalURL: finalURL,
                    statusCode: 200,
                    headers: ["Content-Type": "application/json"],
                    body: Data(repeating: 0x20, count: 1_025)
                )
            ),
            maximumResponseBytes: 1_024
        )
        await assertDiscoveryError(.responseTooLarge(1_024)) {
            try await oversized.discover(from: endpoint)
        }
    }

    private func assertDiscoveryError(
        _ expected: BootstrapDiscoveryError,
        operation: () async throws -> ValidatedBootstrapContract
    ) async {
        do {
            _ = try await operation()
            XCTFail("Expected \(expected)")
        } catch {
            XCTAssertEqual(error as? BootstrapDiscoveryError, expected)
        }
    }

    private static let validPayload = Data(validJSON.utf8)

    private static let validJSON = #"""
    {
      "schema_version":2,
      "generated_at":"2026-07-29T15:30:00Z",
      "server":{
        "server_id":"12345678-1234-4abc-8def-1234567890ab",
        "product_id":"joeos",
        "display_name":"JoeOS Local Command Center",
        "server_version":"2.0.0",
        "api_version":"v1",
        "deployment_mode":"local_first"
      },
      "security":{
        "ownership_model":"single_owner",
        "network_boundary":"operator_managed_private_tailnet",
        "application_authentication":"unavailable",
        "device_enrollment":"operator_pairing_v1",
        "role_based_access":"unavailable",
        "privileged_actions":"unavailable",
        "public_internet_ready":false,
        "secrets_returned":false,
        "warning":"Local-console device pairing is available, but application authentication, roles, and privileged approvals remain unavailable. JoeOS is not public-internet ready."
      },
      "device_enrollment":{
        "protocol":"joeos-device-enrollment-v1",
        "offer_authority":"local_console_only",
        "pairing_secret_bytes":32,
        "offer_ttl_seconds":300,
        "challenge_ttl_seconds":120,
        "key_algorithm":"ES256",
        "public_key_format":"spki_der_base64url",
        "signature_format":"x962_der_base64url",
        "proof_algorithm":"HKDF-SHA256+HMAC-SHA256+ECDSA-SHA256",
        "required_key_purposes":["device_authentication","approval"],
        "activation_state":"active_unassigned",
        "grants_authority":false
      },
      "capabilities":[
        {
          "id":"discovery.bootstrap",
          "status":"available",
          "access":"read_only",
          "route_ids":["bootstrap.discovery"],
          "description":"Discover the local JoeOS contract."
        },
        {
          "id":"identity.device_enrollment",
          "status":"available",
          "access":"enrollment",
          "route_ids":["device-enrollment.challenge","device-enrollment.complete"],
          "description":"Pair two P-256 device keys through a five-minute local-console offer."
        },
        {
          "id":"identity.authentication",
          "status":"unavailable",
          "access":"unavailable",
          "route_ids":[],
          "description":"Application authentication is unavailable."
        },
        {
          "id":"authorization.roles",
          "status":"unavailable",
          "access":"unavailable",
          "route_ids":[],
          "description":"Role-based access is unavailable."
        },
        {
          "id":"approvals.privileged_actions",
          "status":"unavailable",
          "access":"unavailable",
          "route_ids":[],
          "description":"Privileged approvals are unavailable."
        },
        {
          "id":"agents.execution",
          "status":"unavailable",
          "access":"unavailable",
          "route_ids":[],
          "description":"Remote agent execution is unavailable."
        },
        {
          "id":"secrets.management",
          "status":"unavailable",
          "access":"unavailable",
          "route_ids":[],
          "description":"Native secret management is unavailable."
        }
      ],
      "routes":[
        {
          "id":"bootstrap.discovery",
          "path":"/api/v1/bootstrap",
          "protocol":"http",
          "methods":["GET"],
          "access":"read_only",
          "stability":"stable",
          "description":"Read the strict native bootstrap contract."
        },
        {
          "id":"device-enrollment.challenge",
          "path":"/api/v1/device-enrollment/challenges",
          "protocol":"http",
          "methods":["POST"],
          "access":"enrollment",
          "stability":"stable",
          "description":"Claim a local-console pairing offer and receive a bound P-256 challenge."
        },
        {
          "id":"device-enrollment.complete",
          "path":"/api/v1/device-enrollment/challenges/{challenge_id}/complete",
          "protocol":"http",
          "methods":["POST"],
          "access":"enrollment",
          "stability":"stable",
          "description":"Atomically prove the pairing secret and both device keys without receiving authority."
        }
      ]
    }
    """#
}

private actor RecordingBootstrapTransport: BootstrapHTTPTransport {
    private let response: BootstrapHTTPResponse
    private var requests: [URLRequest] = []

    init(response: BootstrapHTTPResponse) {
        self.response = response
    }

    func send(_ request: URLRequest, maximumResponseBytes: Int) async throws -> BootstrapHTTPResponse {
        requests.append(request)
        return response
    }

    func recordedRequests() -> [URLRequest] {
        requests
    }
}
