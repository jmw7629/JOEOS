import Combine
import Foundation

public enum ConnectionManagerError: Error, Equatable, LocalizedError, Sendable {
    case validationFailed(EndpointValidationError)
    case duplicateProfile(ConnectionProfile)
    case unknownProfileID(UUID)
    case cannotDeleteLastProfile
    case persistenceFailed

    public var errorDescription: String? {
        switch self {
        case .validationFailed(let error):
            error.localizedDescription
        case .duplicateProfile(let profile):
            "A connection to \(profile.host) already exists (\(profile.displayName))."
        case .unknownProfileID:
            "The selected connection no longer exists."
        case .cannotDeleteLastProfile:
            "At least one connection profile must remain."
        case .persistenceFailed:
            "The connection preferences could not be saved."
        }
    }
}

/// Owns the saved connection profiles, the selected profile, and reconnection
/// to the last profile that succeeded. All profile writes are validated and
/// persisted through an injected `ProfilePersisting` (testable in memory).
@MainActor
public final class ConnectionManager: ObservableObject {

    @Published public private(set) var profiles: [ConnectionProfile]
    @Published public private(set) var activeProfileID: UUID?
    @Published public private(set) var lastSuccessfulProfileID: UUID?
    @Published public private(set) var lastError: ConnectionManagerError?

    private let store: any ProfilePersisting

    public init(store: any ProfilePersisting = UserDefaultsProfilePersisting()) {
        self.store = store
        let stored = store.string(forKey: ConnectionProfileStorage.profilesKey)
        self.profiles = Self.loadProfiles(stored)
        if let migrated = ConnectionProfileStorage.migratedPayload(stored ?? "") {
            try? persistProfiles(migrated)
        }
        let storedActive = store.string(forKey: ConnectionProfileStorage.activeProfileKey)
        self.activeProfileID = Self.resolveActiveID(
            storedActive: storedActive,
            profiles: profiles
        )
        let storedLast = store.string(forKey: ConnectionProfileStorage.lastSuccessfulProfileKey)
        if let storedLast {
            let identifier = UUID(uuidString: storedLast)
            if let identifier, profiles.contains(where: { $0.id == identifier }) {
                self.lastSuccessfulProfileID = identifier
            } else {
                store.set("", forKey: ConnectionProfileStorage.lastSuccessfulProfileKey)
            }
        }
        if let activeProfileID {
            store.set(activeProfileID.uuidString, forKey: ConnectionProfileStorage.activeProfileKey)
        }
    }

    public var activeProfile: ConnectionProfile? {
        guard let activeProfileID else { return profiles.first }
        return profiles.first(where: { $0.id == activeProfileID }) ?? profiles.first
    }

    public var lastSuccessfulProfile: ConnectionProfile? {
        guard let lastSuccessfulProfileID else { return nil }
        return profiles.first(where: { $0.id == lastSuccessfulProfileID })
    }

    // MARK: - Selection

    @discardableResult
    public func select(_ profile: ConnectionProfile) -> Result<ConnectionProfile, ConnectionManagerError> {
        guard profiles.contains(where: { $0.id == profile.id }) else {
            lastError = .unknownProfileID(profile.id)
            return .failure(.unknownProfileID(profile.id))
        }
        guard case .success = validate(profile) else {
            lastError = validationError(for: profile).map(ConnectionManagerError.validationFailed)
            return .failure(lastError ?? .validationFailed(.empty))
        }
        activeProfileID = profile.id
        store.set(profile.id.uuidString, forKey: ConnectionProfileStorage.activeProfileKey)
        lastError = nil
        return .success(profile)
    }

    @discardableResult
    public func selectProfile(id: UUID) -> Result<ConnectionProfile, ConnectionManagerError> {
        guard let profile = profiles.first(where: { $0.id == id }) else {
            lastError = .unknownProfileID(id)
            return .failure(.unknownProfileID(id))
        }
        return select(profile)
    }

    // MARK: - Mutations

    @discardableResult
    public func upsert(_ profile: ConnectionProfile) -> Result<Void, ConnectionManagerError> {
        if let validationError = validationError(for: profile) {
            lastError = .validationFailed(validationError)
            return .failure(.validationFailed(validationError))
        }
        if let duplicate = Self.duplicate(of: profile, in: profiles) {
            lastError = .duplicateProfile(duplicate)
            return .failure(.duplicateProfile(duplicate))
        }
        if let index = profiles.firstIndex(where: { $0.id == profile.id }) {
            profiles[index] = profile
        } else {
            profiles.append(profile)
        }
        do {
            try save()
        } catch {
            lastError = .persistenceFailed
            return .failure(.persistenceFailed)
        }
        lastError = nil
        return .success(())
    }

    @discardableResult
    public func delete(_ id: UUID) -> Result<Void, ConnectionManagerError> {
        guard profiles.count > 1 else {
            lastError = .cannotDeleteLastProfile
            return .failure(.cannotDeleteLastProfile)
        }
        guard let index = profiles.firstIndex(where: { $0.id == id }) else {
            lastError = .unknownProfileID(id)
            return .failure(.unknownProfileID(id))
        }
        profiles.remove(at: index)
        if activeProfileID == id {
            activeProfileID = profiles.first?.id
            if let activeProfileID {
                store.set(activeProfileID.uuidString, forKey: ConnectionProfileStorage.activeProfileKey)
            }
        }
        if lastSuccessfulProfileID == id {
            lastSuccessfulProfileID = nil
            store.set("", forKey: ConnectionProfileStorage.lastSuccessfulProfileKey)
        }
        do {
            try save()
        } catch {
            lastError = .persistenceFailed
            return .failure(.persistenceFailed)
        }
        lastError = nil
        return .success(())
    }

    /// Persists the current profile list.
    public func save() throws {
        let payload = try ConnectionProfileStorage.encode(profiles)
        store.set(payload, forKey: ConnectionProfileStorage.profilesKey)
    }

    /// A profile that duplicates an existing entry's transport/host/port,
    /// excluding the candidate itself.
    private static func duplicate(
        of profile: ConnectionProfile,
        in existing: [ConnectionProfile]
    ) -> ConnectionProfile? {
        let targetPort = profile.port ?? profile.transport.defaultPort
        for candidate in existing {
            if candidate.id != profile.id,
               candidate.transport == profile.transport,
               candidate.host == profile.host,
               candidate.port ?? candidate.transport.defaultPort == targetPort {
                return candidate
            }
        }
        return nil
    }

    // MARK: - Validation

    /// Friendly, human-readable validation message for a profile, or nil when
    /// the profile is valid and can become active.
    public func validationError(for profile: ConnectionProfile) -> EndpointValidationError? {
        let trimmedName = profile.displayName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else {
            return .empty
        }
        guard case .failure(let error) = EndpointPolicy.validate(profile.endpoint) else {
            return nil
        }
        return error
    }

    public func validate(_ profile: ConnectionProfile) -> Result<ValidatedEndpoint, EndpointValidationError> {
        EndpointPolicy.validate(profile.endpoint)
    }

    // MARK: - Reconnect

    /// Selects the last profile that connected successfully, when one exists
    /// and differs from the current selection.
    @discardableResult
    public func reconnectToLastSuccessful() -> Result<ConnectionProfile, ConnectionManagerError> {
        guard let lastSuccessfulProfile else {
            lastError = nil
            return .failure(.unknownProfileID(UUID()))
        }
        if lastSuccessfulProfile.id == activeProfileID {
            return .success(lastSuccessfulProfile)
        }
        return select(lastSuccessfulProfile)
    }

    /// Records a successful connection for the given profile (host switching
    /// and reconnect bookkeeping).
    public func recordConnected(_ profile: ConnectionProfile) {
        update(profile) { $0.connected() }
    }

    /// Records a successful end-to-end connection for the given profile.
    public func recordSucceeded(_ profile: ConnectionProfile) {
        update(profile) { $0.succeeded() }
        lastSuccessfulProfileID = profile.id
        store.set(profile.id.uuidString, forKey: ConnectionProfileStorage.lastSuccessfulProfileKey)
    }

    private func update(_ profile: ConnectionProfile, transform: (ConnectionProfile) -> ConnectionProfile) {
        guard let index = profiles.firstIndex(where: { $0.id == profile.id }) else { return }
        profiles[index] = transform(profile)
        try? save()
    }

    // MARK: - Loading

    private static func loadProfiles(_ stored: String?) -> [ConnectionProfile] {
        guard let stored, !stored.isEmpty else { return [.defaultVPS] }
        return ConnectionProfileStorage.decode(stored)
    }

    private static func resolveActiveID(storedActive: String?, profiles: [ConnectionProfile]) -> UUID? {
        guard let storedActive,
              let identifier = UUID(uuidString: storedActive),
              profiles.contains(where: { $0.id == identifier })
        else {
            return profiles.first?.id
        }
        return identifier
    }

    private func persistProfiles(_ payload: String) throws {
        store.set(payload, forKey: ConnectionProfileStorage.profilesKey)
    }
}
