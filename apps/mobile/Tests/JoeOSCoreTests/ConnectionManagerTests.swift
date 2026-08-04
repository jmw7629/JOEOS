import Foundation
import XCTest
@testable import JoeOSCore

@MainActor
final class ConnectionManagerTests: XCTestCase {

    // MARK: - Default development profile

    func testDefaultDevelopmentProfileIsTheVPS() throws {
        let profile = ConnectionProfile.defaultVPS
        XCTAssertEqual(profile.displayName, "JoeOS VPS")
        XCTAssertEqual(profile.host, "100.98.25.26")
        XCTAssertEqual(profile.transport, .http)
        XCTAssertEqual(profile.environment, .development)
        XCTAssertFalse(profile.httpsRequired)
        XCTAssertTrue(profile.isReachablePerPolicy)
        XCTAssertNil(profile.port)
    }

    func testDefaultProfileEndpointsNeverReferenceTheHalo() {
        let decoded = ConnectionProfileStorage.decode(ConnectionProfileStorage.defaultPayload)
        XCTAssertEqual(decoded, [.defaultVPS])
        XCTAssertFalse(decoded.contains { $0.host == "100.121.165.22" })
    }

    func testHaloRemainsAnExplicitEditableProfileNotTheDefault() {
        let manager = ConnectionManager(store: MemoryProfileStore())
        XCTAssertEqual(manager.activeProfile, .defaultVPS)
        XCTAssertNotEqual(manager.activeProfile?.host, "100.121.165.22")
        let halo = ConnectionProfile.defaultHalo
        XCTAssertEqual(halo.host, "100.121.165.22")
        let result = manager.upsert(halo)
        XCTAssertEqual(result, .success(()))
    }

    // MARK: - Validation

    func testProfileValidationRejectsMalformedHosts() {
        let manager = ConnectionManager(store: MemoryProfileStore())
        XCTAssertNil(manager.validationError(for: .defaultVPS))
        for (name, host) in [
            ("public http", "http://example.com"),
            ("malformed", "http://"),
            ("credentials", "http://user:pass@100.98.25.26"),
            ("query", "https://joeos.example.com?token=x"),
            ("fragment", "https://joeos.example.com#x"),
            ("wildcard", "http://*.example.com"),
            ("empty host", "http://"),
        ] {
            var profile = ConnectionProfile.defaultVPS
            profile.host = host.replacingOccurrences(of: "http://", with: "")
            XCTAssertNotNil(manager.validationError(for: profile), name)
        }
    }

    func testValidationRejectsUnnamedProfile() {
        let manager = ConnectionManager(store: MemoryProfileStore())
        var profile = ConnectionProfile.defaultVPS
        profile.displayName = "   "
        XCTAssertEqual(manager.validationError(for: profile), .empty)
    }

    // MARK: - Switching

    func testHostSwitchingChangesActiveProfileAndPersists() throws {
        let store = MemoryProfileStore()
        let manager = ConnectionManager(store: store)
        var other = ConnectionProfile.defaultVPS
        other.id = UUID()
        other.host = "100.64.0.10"
        other.displayName = "Second host"
        XCTAssertEqual(manager.upsert(other), .success(()))

        XCTAssertEqual(manager.selectProfile(id: other.id), .success(other))
        XCTAssertEqual(manager.activeProfile?.host, "100.64.0.10")
        XCTAssertEqual(store.string(forKey: ConnectionProfileStorage.activeProfileKey),
                       other.id.uuidString)
    }

    // MARK: - Persistence

    func testPersistenceRestoresProfilesAcrossManagerInstances() throws {
        let store = MemoryProfileStore()
        let first = ConnectionManager(store: store)
        var saved = ConnectionProfile.defaultVPS
        saved.id = UUID()
        saved.displayName = "Saved profile"
        saved.host = "192.168.1.50"
        saved.port = 8080
        XCTAssertEqual(first.upsert(saved), .success(()))
        XCTAssertEqual(first.selectProfile(id: saved.id), .success(saved))

        let restored = ConnectionManager(store: store)
        XCTAssertEqual(restored.profiles.count, 2)
        XCTAssertEqual(restored.activeProfile?.id, saved.id)
        XCTAssertEqual(restored.activeProfile?.host, "192.168.1.50")
    }

    func testStorageRoundTripPreservesProfileFields() throws {
        var profile = ConnectionProfile.defaultVPS
        profile.displayName = "Production"
        profile.transport = .https
        profile.host = "joeos.example.com"
        profile.port = 9443
        profile.environment = .production
        profile.notes = "Team instance"
        profile.apiVersion = "v1"
        profile.requiresAuthentication = true
        profile.authenticationMode = .application
        profile.lastConnectedAt = Date(timeIntervalSince1970: 1_700_000_000)
        profile.lastSuccessfulAt = Date(timeIntervalSince1970: 1_700_000_100)

        let encoded = try ConnectionProfileStorage.encode([profile])
        let decoded = ConnectionProfileStorage.decode(encoded)
        XCTAssertEqual(decoded, [profile])
        XCTAssertEqual(decoded[0].transport, .https)
        XCTAssertEqual(decoded[0].httpsRequired, true)
        XCTAssertEqual(decoded[0].environment, .production)
        XCTAssertEqual(decoded[0].notes, "Team instance")
        XCTAssertEqual(decoded[0].apiVersion, "v1")
        XCTAssertEqual(decoded[0].lastConnectedAt, profile.lastConnectedAt)
        XCTAssertEqual(decoded[0].lastSuccessfulAt, profile.lastSuccessfulAt)
    }

    // MARK: - Migration from the old Halo profile

    func testLegacyHaloProfileMigratesToFieldBasedProfile() {
        let legacy = #"""
        [{"schema_version":1,"id":"6BA7B810-9DAD-4D5A-8000-000000000111","name":"Halo","endpoint":"http://100.121.165.22:8080"}]
        """#
        let profiles = ConnectionProfileStorage.decode(legacy)
        XCTAssertEqual(profiles.count, 1)
        XCTAssertEqual(profiles[0].displayName, "Halo")
        XCTAssertEqual(profiles[0].host, "100.121.165.22")
        XCTAssertEqual(profiles[0].port, 8080)
        XCTAssertEqual(profiles[0].transport, .http)
        XCTAssertEqual(profiles[0].environment, .development)
        XCTAssertNotEqual(profiles[0].id, ConnectionProfile.defaultVPS.id)
    }

    func testMigrationFromLegacyHaloDoesNotReplaceSavedProfiles() {
        let store = MemoryProfileStore()
        let legacy = #"[{"schema_version":1,"id":"6BA7B810-9DAD-4D5A-8000-000000000111","name":"Halo","endpoint":"http://100.121.165.22:8080"}]"#
        store.set(legacy, forKey: ConnectionProfileStorage.profilesKey)
        let manager = ConnectionManager(store: store)
        XCTAssertEqual(manager.profiles.count, 1)
        XCTAssertEqual(manager.profiles[0].host, "100.121.165.22")
        XCTAssertEqual(manager.activeProfile?.host, "100.121.165.22")
        let persisted = store.string(forKey: ConnectionProfileStorage.profilesKey) ?? ""
        XCTAssertNotEqual(persisted, legacy)
        XCTAssertEqual(ConnectionProfileStorage.decode(persisted), manager.profiles)
    }

    func testMalformedStoredPayloadFallsBackToDefaultProfile() {
        let store = MemoryProfileStore()
        store.set("not-json", forKey: ConnectionProfileStorage.profilesKey)
        let manager = ConnectionManager(store: store)
        XCTAssertEqual(manager.profiles, [.defaultVPS])
        XCTAssertEqual(manager.activeProfile, .defaultVPS)
    }

    // MARK: - Reconnect

    func testReconnectToLastSuccessfulProfile() throws {
        let store = MemoryProfileStore()
        let manager = ConnectionManager(store: store)
        var other = ConnectionProfile.defaultVPS
        other.id = UUID()
        other.host = "100.64.0.20"
        other.displayName = "Backup host"
        XCTAssertEqual(manager.upsert(other), .success(()))
        manager.recordSucceeded(other)

        XCTAssertEqual(manager.selectProfile(id: ConnectionProfile.defaultVPS.id),
                       .success(.defaultVPS))
        XCTAssertEqual(manager.activeProfile?.id, ConnectionProfile.defaultVPS.id)

        XCTAssertEqual(manager.reconnectToLastSuccessful(), .success(other))
        XCTAssertEqual(manager.activeProfile?.id, other.id)
    }

    func testReconnectStaysOnActiveWhenItIsLastSuccessful() throws {
        let store = MemoryProfileStore()
        let manager = ConnectionManager(store: store)
        manager.recordSucceeded(.defaultVPS)
        XCTAssertEqual(manager.reconnectToLastSuccessful(), .success(.defaultVPS))
    }

    // MARK: - Session restoration

    func testSessionRestoresPreviouslySelectedProfile() {
        let store = MemoryProfileStore()
        var selected = ConnectionProfile.defaultVPS
        selected.id = UUID()
        selected.host = "100.64.0.30"
        selected.displayName = "Restored host"
        store.set(
            (try? ConnectionProfileStorage.encode([.defaultVPS, selected])) ?? "[]",
            forKey: ConnectionProfileStorage.profilesKey
        )
        store.set(selected.id.uuidString, forKey: ConnectionProfileStorage.activeProfileKey)

        let manager = ConnectionManager(store: store)
        XCTAssertEqual(manager.activeProfile?.id, selected.id)
        XCTAssertEqual(manager.activeProfile?.host, "100.64.0.30")
    }

    // MARK: - Duplicates

    func testDuplicateHostIsRejectedWithFriendlyError() throws {
        let store = MemoryProfileStore()
        let manager = ConnectionManager(store: store)
        var duplicate = ConnectionProfile.defaultVPS
        duplicate.id = UUID()
        duplicate.displayName = "Duplicate VPS"
        let result = manager.upsert(duplicate)
        guard case .failure(.duplicateProfile(let existing)) = result else {
            XCTFail("Expected duplicate rejection")
            return
        }
        XCTAssertEqual(existing.host, "100.98.25.26")
    }

    func testDeleteLastProfileIsRejected() {
        let store = MemoryProfileStore()
        let manager = ConnectionManager(store: store)
        guard case .failure(.cannotDeleteLastProfile) = manager.delete(ConnectionProfile.defaultVPS.id) else {
            XCTFail("Expected cannot-delete-last error")
            return
        }
    }
}

/// Thread-safe in-memory profile persistence for tests.
private final class MemoryProfileStore: ProfilePersisting, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: String] = [:]

    func string(forKey key: String) -> String? {
        lock.lock()
        defer { lock.unlock() }
        return values[key]
    }

    func set(_ value: String, forKey key: String) {
        lock.lock()
        defer { lock.unlock() }
        values[key] = value
    }
}
