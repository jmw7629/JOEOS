import Foundation
import XCTest
@testable import JoeOSCore

final class EndpointPolicyTests: XCTestCase {
    func testDefaultHaloTailscaleEndpointIsAccepted() throws {
        let validated = try EndpointPolicy.validate(ConnectionProfile.defaultHalo.endpoint).get()
        XCTAssertEqual(validated.origin.scheme, "http")
        XCTAssertEqual(validated.origin.host, "100.121.165.22")
        XCTAssertEqual(validated.origin.port, 8080)
    }

    func testHTTPSIsAcceptedForPublicAndPrivateHosts() throws {
        for address in [
            "https://joeos.example.com",
            "https://203.0.113.10:9443/control",
            "https://halo.local",
        ] {
            XCTAssertNoThrow(try EndpointPolicy.validate(address).get(), address)
        }
    }

    func testPrivateIPv4HTTPIsAccepted() throws {
        for address in [
            "http://10.0.0.1",
            "http://172.16.0.1",
            "http://172.31.255.254",
            "http://192.168.50.10:8080",
            "http://169.254.1.2",
            "http://127.0.0.1:8080",
            "http://100.64.0.1",
            "http://100.127.255.254",
        ] {
            XCTAssertNoThrow(try EndpointPolicy.validate(address).get(), address)
        }
    }

    func testLocalNamesAndPrivateIPv6HTTPAreAccepted() throws {
        for address in [
            "http://localhost:8080",
            "http://joeos.local:8080",
            "http://[::1]:8080",
            "http://[fd12:3456:789a::1]:8080",
            "http://[fe80::1]:8080",
        ] {
            XCTAssertNoThrow(try EndpointPolicy.validate(address).get(), address)
        }
    }

    func testPublicOrOutOfRangeHTTPIsRejected() {
        for address in [
            "http://example.com",
            "http://8.8.8.8",
            "http://172.15.255.255",
            "http://172.32.0.1",
            "http://100.63.255.255",
            "http://100.128.0.1",
            "http://0.0.0.0",
        ] {
            guard case .failure(.insecurePublicHost) = EndpointPolicy.validate(address) else {
                XCTFail("Expected insecure public HTTP rejection for \(address)")
                continue
            }
        }
    }

    func testCredentialsQueriesFragmentsAndUnsupportedSchemesAreRejected() {
        XCTAssertEqual(failure("https://user:password@halo.example.com"), .credentialsNotAllowed)
        XCTAssertEqual(failure("https://halo.example.com?token=secret"), .queryNotAllowed)
        XCTAssertEqual(failure("https://halo.example.com#fragment"), .fragmentNotAllowed)
        XCTAssertEqual(failure("file:///tmp/joeos"), .unsupportedScheme)
        XCTAssertEqual(failure("   "), .empty)
    }

    func testDefaultPortsAreNormalizedForSameOriginNavigation() {
        let endpoint = URL(string: "https://halo.example.com")!
        let candidate = URL(string: "https://halo.example.com:443/mission")!
        XCTAssertEqual(
            EndpointPolicy.navigationDisposition(
                for: candidate,
                relativeTo: endpoint,
                userInitiated: false
            ),
            .allowSameOrigin
        )
    }

    func testUserInitiatedExternalWebLinksAreHandedOff() {
        let endpoint = URL(string: "https://halo.example.com")!
        let external = URL(string: "https://docs.example.com/joeos")!
        XCTAssertEqual(
            EndpointPolicy.navigationDisposition(
                for: external,
                relativeTo: endpoint,
                userInitiated: true
            ),
            .openExternally
        )
    }

    func testExternalRedirectsAndNonWebSchemesAreBlocked() {
        let endpoint = URL(string: "https://halo.example.com")!
        let external = URL(string: "https://docs.example.com/joeos")!
        let custom = URL(string: "joeos://execute")!

        XCTAssertEqual(
            EndpointPolicy.navigationDisposition(
                for: external,
                relativeTo: endpoint,
                userInitiated: false
            ),
            .block
        )
        XCTAssertEqual(
            EndpointPolicy.navigationDisposition(
                for: custom,
                relativeTo: endpoint,
                userInitiated: true
            ),
            .block
        )
    }

    func testProfilePayloadRoundTripsAndMalformedStorageFallsBackSafely() throws {
        let profiles = [
            ConnectionProfile.defaultHalo,
            ConnectionProfile(name: "Production", endpoint: "https://halo.example.com"),
        ]
        let encoded = try ConnectionProfileStorage.encode(profiles)
        XCTAssertEqual(ConnectionProfileStorage.decode(encoded), profiles)
        XCTAssertEqual(ConnectionProfileStorage.decode("not-json"), [.defaultHalo])
        XCTAssertEqual(ConnectionProfileStorage.decode("[]"), [.defaultHalo])
    }

    private func failure(_ address: String) -> EndpointValidationError? {
        guard case .failure(let error) = EndpointPolicy.validate(address) else { return nil }
        return error
    }
}
