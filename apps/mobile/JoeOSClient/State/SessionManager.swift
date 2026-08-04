import Combine
import Foundation
import JoeOSCore

public enum SessionPhase: Equatable, Sendable {
    case idle
    case connecting
    case active
    case offline
    case revoked
    case incompatibleBackend
    case error
}

/// Owns one application session against the selected JoeOS host: creation,
/// expiration, renewal, logout, and revocation. The session only ever binds to
/// the exact validated origin; it never stores tokens or credentials.
@MainActor
public final class SessionManager: ObservableObject {

    @Published public private(set) var phase: SessionPhase = .idle
    @Published public private(set) var activeEndpoint: ValidatedEndpoint?
    @Published public private(set) var validatedContract: ValidatedBootstrapContract?
    @Published public private(set) var sessionStartedAt: Date?
    @Published public private(set) var lastError: String?

    private let bootstrapDiscovery: any BootstrapDiscovering
    private let clock: () -> Date
    private let sessionLifetime: TimeInterval

    public init(
        bootstrapDiscovery: any BootstrapDiscovering = BootstrapDiscoveryClient(),
        clock: @escaping () -> Date = Date.init,
        sessionLifetime: TimeInterval = 12 * 60 * 60
    ) {
        self.bootstrapDiscovery = bootstrapDiscovery
        self.clock = clock
        self.sessionLifetime = sessionLifetime
    }

    public var isActive: Bool { phase == .active }

    /// Establishes a session for the profile. The profile is validated before
    /// any network activity; the backend contract is discovered and verified.
    public func connect(profile: ConnectionProfile) async {
        guard case .success(let endpoint) = EndpointPolicy.validate(profile.endpoint) else {
            phase = .error
            lastError = "The selected connection does not pass transport policy."
            return
        }
        activeEndpoint = endpoint
        phase = .connecting
        lastError = nil
        do {
            let contract = try await bootstrapDiscovery.discover(from: endpoint)
            try Task.checkCancellation()
            validatedContract = contract
            if contract.hasApplicationAuthentication {
                phase = .active
            } else {
                phase = .active
            }
            sessionStartedAt = clock()
        } catch is CancellationError {
            return
        } catch let error as BootstrapDiscoveryError {
            switch error {
            case .unexpectedStatus(404), .unexpectedStatus(405):
                // Older JoeOS servers: web command center remains available but
                // native trust posture is unverified.
                validatedContract = nil
                phase = .active
            case .unexpectedResponseURL, .invalidPayload, .invalidContract,
                 .invalidContentType, .responseTooLarge, .emptyResponse,
                 .cannotDeriveSameOriginURL:
                phase = .incompatibleBackend
                lastError = error.localizedDescription
            case .unexpectedStatus:
                phase = .offline
                lastError = error.localizedDescription
            }
        } catch {
            phase = .offline
            lastError = error.localizedDescription
        }
    }

    public func disconnect() {
        phase = .idle
        activeEndpoint = nil
        validatedContract = nil
        sessionStartedAt = nil
        lastError = nil
    }

    /// Revokes the session (for example on explicit logout or remote revocation).
    public func revoke() {
        phase = .revoked
        activeEndpoint = nil
        validatedContract = nil
        sessionStartedAt = nil
        lastError = "This session has been revoked. Reconnect to the JoeOS host to continue."
    }

    public func markOffline() {
        if phase == .active {
            phase = .offline
            lastError = "JoeOS is not reachable from this device."
        }
    }

    public var hasExpired: Bool {
        guard let sessionStartedAt else { return false }
        return clock().timeIntervalSince(sessionStartedAt) > sessionLifetime
    }

    /// Renews the session lifetime after confirmed activity.
    public func renew() {
        sessionStartedAt = clock()
    }
}
