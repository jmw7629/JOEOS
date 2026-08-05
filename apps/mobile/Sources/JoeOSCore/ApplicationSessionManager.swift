import Combine
import Foundation

/// The application session lifecycle states. The client never shows an
/// authenticated state based solely on cached credentials: while offline it may
/// display bounded cached identity, but authority stays unverified until the
/// backend confirms the session.
public enum ApplicationSessionState: Equatable, Sendable {
    case disconnected
    case discovering
    case enrollmentRequired
    case activeUnassigned
    case authenticating
    case authenticated
    case refreshing
    case offlineAuthenticatedCache
    case sessionExpired
    case assignmentRevoked
    case deviceRevoked
    case userDisabled
    case organizationDisabled
    case workspaceDisabled
    case backendIncompatible
    case transportRejected
    case authenticationFailed

    public var isAuthoritative: Bool { self == .authenticated || self == .refreshing }
    public var isUnverifiedCache: Bool { self == .offlineAuthenticatedCache }
}

/// Structured authentication errors. A 401/403 is never collapsed into a
/// generic network error; the client distinguishes the authoritative reason.
public enum AuthenticationError: Error, Equatable, LocalizedError, Sendable {
    case transportRejected(EndpointValidationError)
    case backendIncompatible
    case enrollmentRequired
    case deviceNotAssigned
    case deviceRevoked
    case userDisabled
    case organizationDisabled
    case workspaceDisabled
    case sessionExpired
    case sessionRevoked
    case refreshRevoked
    case authenticationFailed(reason: String)
    case refreshLoopPrevented
    case networkUnavailable

    public var errorDescription: String? {
        switch self {
        case .transportRejected(let error):
            error.localizedDescription
        case .backendIncompatible:
            "This backend does not match the JoeOS contract."
        case .enrollmentRequired:
            "This iPhone is not enrolled. Pair a device first."
        case .deviceNotAssigned:
            "This device is enrolled but not assigned to a principal."
        case .deviceRevoked:
            "This device has been revoked."
        case .userDisabled:
            "The signed-in user is disabled."
        case .organizationDisabled:
            "The organization is disabled."
        case .workspaceDisabled:
            "The workspace is disabled."
        case .sessionExpired:
            "The application session has expired."
        case .sessionRevoked:
            "The application session has been revoked."
        case .refreshRevoked:
            "The refresh credential family has been revoked."
        case .authenticationFailed(let reason):
            "Authentication failed: \(reason)"
        case .refreshLoopPrevented:
            "Refresh was attempted repeatedly without success."
        case .networkUnavailable:
            "JoeOS is not reachable."
        }
    }
}

/// Supplies the enrolled device identity and its device-authentication key.
/// The app wires this to the existing enrollment receipt + Secure Enclave keys.
public protocol EnrolledDeviceProviding: Sendable {
    func enrolledDeviceID() async throws -> UUID
    func deviceAuthenticationKey() async throws -> any EnrollmentSigningKey
}

/// The authoritative application-session manager (Phase P3A).
///
/// Owns the full client flow: validate profile, discover backend, load the
/// enrollment receipt, request and sign the authentication challenge, establish
/// the short-lived session, store credentials only in ThisDeviceOnly Keychain,
/// load the principal, and refresh with serialized rotation. Credentials are
/// cleared on logout, refresh-family revocation, device or assignment
/// revocation, user disablement, and any authoritative rejection.
@MainActor
public final class ApplicationSessionManager: ObservableObject {

    @Published public private(set) var state: ApplicationSessionState = .disconnected
    @Published public private(set) var principal: AuthenticatedPrincipal?
    @Published public private(set) var session: ApplicationSession?
    @Published public private(set) var lastError: AuthenticationError?
    @Published public private(set) var lastVerifiedAt: Date?

    private let connections: ConnectionManager
    private let sessionClient: ApplicationSessionClient
    private let deviceProvider: any EnrolledDeviceProviding
    private let store: any SessionCredentialStoring
    private let clock: () -> Date
    private let now: () -> Int

    private var refreshLock = NSLock()
    private var isRefreshing = false
    private static let maximumRefreshAttempts = 2
    private static let refreshAheadSeconds: Int = 120

    public init(
        connections: ConnectionManager,
        sessionClient: ApplicationSessionClient,
        deviceProvider: any EnrolledDeviceProviding,
        store: (any SessionCredentialStoring)? = nil,
        clock: @escaping () -> Date = Date.init,
        now: @escaping () -> Int = { Int(Date().timeIntervalSince1970) }
    ) {
        self.connections = connections
        self.sessionClient = sessionClient
        self.deviceProvider = deviceProvider
        self.store = store ?? Self.defaultSessionStore()
        self.clock = clock
        self.now = now
    }

    private static func defaultSessionStore() -> any SessionCredentialStoring {
        #if canImport(Security)
        KeychainSessionStore()
        #else
        InMemorySessionStore()
        #endif
    }

    // MARK: - Connect / discover

    public func connect() async {
        state = .discovering
        guard let profile = connections.activeProfile else {
            state = .disconnected
            return
        }
        switch EndpointPolicy.validate(profile.endpoint) {
        case .failure(let error):
            state = .transportRejected
            lastError = .transportRejected(error)
            return
        case .success:
            break
        }
        do {
            let deviceID = try await deviceProvider.enrolledDeviceID()
            _ = deviceID
        } catch {
            state = .enrollmentRequired
            return
        }
        // Reconnect with any stored session that the backend still accepts.
        if let stored = store.load(), stored.session.expiresAt > now() {
            do {
                let live = try await sessionClient.principal(sessionID: stored.sessionID)
                principal = live
                session = stored.session
                lastVerifiedAt = clock()
                state = .authenticated
                return
            } catch {
                clearCredentials()
                state = .sessionExpired
            }
        }
        state = .activeUnassigned
    }

    // MARK: - Authentication

    public func authenticate() async {
        state = .authenticating
        lastError = nil
        do {
            let deviceID = try await deviceProvider.enrolledDeviceID()
            let key = try await deviceProvider.deviceAuthenticationKey()
            let userID: UUID
            if let stored = store.load() {
                userID = stored.principal.user.id
            } else if let principal {
                userID = principal.user.id
            } else {
                throw AuthenticationError.deviceNotAssigned
            }
            let challenge = try await sessionClient.requestChallenge(deviceID: deviceID, userID: userID)
            let response = try await sessionClient.solve(challenge, signedWith: key)
            try store.save(StoredApplicationSession(response: response))
            session = response.session
            principal = response.principal
            lastVerifiedAt = clock()
            state = .authenticated
        } catch let error as AuthenticationError {
            lastError = error
            state = mappedState(for: error)
        } catch {
            lastError = .authenticationFailed(reason: error.localizedDescription)
            state = .authenticationFailed
        }
    }

    // MARK: - Principal

    public func loadPrincipal() async -> Bool {
        guard let sessionID = session?.sessionID else { return false }
        do {
            let live = try await sessionClient.principal(sessionID: sessionID)
            principal = live
            lastVerifiedAt = clock()
            state = .authenticated
            return true
        } catch {
            await handleAuthoritativeRejection(error)
            return false
        }
    }

    // MARK: - Refresh (serialized, bounded, loop-protected)

    public func refreshIfNeeded() async {
        guard let stored = store.load() else { return }
        let expiresAt = stored.session.expiresAt
        guard expiresAt - now() <= Self.refreshAheadSeconds else { return }
        await refresh()
    }

    public func refresh() async {
        refreshLock.lock()
        if isRefreshing {
            refreshLock.unlock()
            return
        }
        isRefreshing = true
        refreshLock.unlock()
        state = .refreshing
        var attempts = 0
        while attempts < Self.maximumRefreshAttempts, let stored = store.load() {
            attempts += 1
            do {
                let response = try await sessionClient.refresh(
                    refreshID: stored.refreshID,
                    refreshToken: stored.refreshToken
                )
                let rotated = StoredApplicationSession(response: response)
                try store.save(rotated)
                session = response.session
                principal = response.principal
                lastVerifiedAt = clock()
                state = .authenticated
                isRefreshing = false
                return
            } catch {
                // A 401 on refresh means the refresh family is revoked.
                if Self.isAuthoritativeRejection(error) {
                    clearCredentials()
                    lastError = .refreshRevoked
                    state = .sessionRevoked
                    isRefreshing = false
                    return
                }
            }
        }
        isRefreshing = false
        lastError = .refreshLoopPrevented
        state = .sessionExpired
    }

    // MARK: - Logout

    public func logout() async {
        if let sessionID = session?.sessionID {
            try? await sessionClient.logout(sessionID: sessionID)
        }
        clearCredentials()
        state = .disconnected
    }

    // MARK: - Revocation handling

    public func handleAuthoritativeRejection(_ error: Error) async {
        if Self.isAuthoritativeRejection(error) {
            clearCredentials()
        }
        if let authError = error as? AuthenticationError {
            lastError = authError
            state = mappedState(for: authError)
        } else {
            lastError = .authenticationFailed(reason: error.localizedDescription)
            state = .authenticationFailed
        }
    }

    public func markOfflineWithCachedIdentity() {
        if state == .authenticated, principal != nil {
            // Bounded cached identity is displayable, but authority is unverified.
            state = .offlineAuthenticatedCache
        } else {
            state = .disconnected
        }
    }

    public func clearCredentials() {
        store.clear()
        session = nil
        principal = nil
        lastVerifiedAt = nil
    }

    // MARK: - Error classification

    private static func isAuthoritativeRejection(_ error: Error) -> Bool {
        if error is AuthenticationError { return true }
        if let backend = error as? BackendClientError {
            switch backend {
            case .unexpectedStatus(401), .unexpectedStatus(403):
                return true
            default:
                return false
            }
        }
        return false
    }

    private func mappedState(for error: AuthenticationError) -> ApplicationSessionState {
        switch error {
        case .deviceRevoked: .deviceRevoked
        case .deviceNotAssigned: .assignmentRevoked
        case .userDisabled: .userDisabled
        case .organizationDisabled: .organizationDisabled
        case .workspaceDisabled: .workspaceDisabled
        case .sessionExpired: .sessionExpired
        case .sessionRevoked, .refreshRevoked: .sessionRevoked
        case .backendIncompatible: .backendIncompatible
        case .transportRejected: .transportRejected
        case .enrollmentRequired: .enrollmentRequired
        case .refreshLoopPrevented, .authenticationFailed, .networkUnavailable: .authenticationFailed
        }
    }
}
