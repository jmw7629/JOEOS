import Combine
import Foundation
import JoeOSCore
import JoeOSIntelligence

/// The top-level application coordinator. Owns the connection manager, the
/// session, and the executive intelligence layer, and exposes one honest app
/// phase so SwiftUI renders only truthful states.
@MainActor
final class ApplicationCoordinator: ObservableObject {

    enum AppPhase: Equatable {
        case firstLaunch
        case selectingConnection
        case connecting
        case active
        case offline
        case revoked
        case incompatibleBackend
        case error
    }

    @Published private(set) var phase: AppPhase = .firstLaunch
    @Published private(set) var statusMessage: String?

    let connections: ConnectionManager
    let session: SessionManager
    let fabric: AgentFabric
    let memory: MemoryStore
    let diagnostics: DiagnosticsStore
    let intelligence: IntelligenceBootstrap
    let offline: OfflineCache
    let sync: SynchronizationEngine

    private(set) var conversation: ConversationEngine?

    public init(
        connections: ConnectionManager,
        session: SessionManager
    ) {
        self.connections = connections
        self.session = session
        self.fabric = AgentFabric()
        self.memory = MemoryStore()
        self.diagnostics = DiagnosticsStore()
        self.intelligence = IntelligenceBootstrap()
        self.offline = OfflineCache()
        self.sync = SynchronizationEngine(send: { _ in true })
    }

    public func start() async {
        if connections.profiles.isEmpty {
            phase = .firstLaunch
            return
        }
        phase = .selectingConnection
        await connectToActiveProfile()
    }

    /// Connects to the currently selected profile and builds the intelligence
    /// layer from the authoritative backend contract.
    public func connectToActiveProfile() async {
        guard let profile = connections.activeProfile else {
            phase = .selectingConnection
            return
        }
        phase = .connecting
        statusMessage = "Connecting to \(profile.displayName)"
        await session.connect(profile: profile)
        switch session.phase {
        case .active:
            guard let endpoint = session.activeEndpoint else {
                phase = .error
                statusMessage = "The connection could not be validated."
                return
            }
            connections.recordConnected(profile)
            connections.recordSucceeded(profile)
            await buildIntelligence(endpoint: endpoint)
            phase = .active
            statusMessage = "Connected to \(profile.displayName)"
        case .offline:
            phase = .offline
            statusMessage = session.lastError ?? "JoeOS is not reachable."
        case .revoked:
            phase = .revoked
            statusMessage = session.lastError ?? "This session has been revoked."
        case .incompatibleBackend:
            phase = .incompatibleBackend
            statusMessage = session.lastError ?? "This backend does not match the JoeOS contract."
        default:
            phase = .error
            statusMessage = session.lastError
        }
    }

    /// Reconnects to the last profile that succeeded (reconnect state).
    public func reconnect() async {
        if case .success(let profile) = connections.reconnectToLastSuccessful() {
            await session.connect(profile: profile)
            await connectToActiveProfile()
        } else {
            await connectToActiveProfile()
        }
    }

    public func disconnect() {
        session.disconnect()
        conversation = nil
        phase = .selectingConnection
        statusMessage = nil
    }

    public func revokeSession() {
        session.revoke()
        conversation = nil
        phase = .revoked
        statusMessage = "Session revoked."
    }

    public func markOffline() {
        session.markOffline()
        if phase == .active {
            phase = .offline
            statusMessage = "JoeOS is not reachable."
        }
    }

    /// Switches to another saved profile (host switching).
    public func switchToProfile(id: UUID) async {
        if case .success = connections.selectProfile(id: id) {
            await connectToActiveProfile()
        }
    }

    // MARK: - Intelligence wiring

    private func buildIntelligence(endpoint: ValidatedEndpoint) async {
        let backendClient = JoeOSBackendClient()
        let backend = JoeOSIntelligenceBackendClient(
            backend: backendClient,
            endpoint: endpoint
        )
        do {
            try await intelligence.refresh(backend: backend)
        } catch {
            // Registries stay empty; the router reports no available provider.
            statusMessage = "Connected, but the AI platform is unavailable."
        }
        let router = ExecutionRouter(
            providers: intelligence.providers,
            models: intelligence.models
        )
        let executor = BackendConversationExecutor(backend: backend)
        let engine = ConversationEngine(
            store: InMemoryConversationStore(),
            executor: executor,
            router: router,
            localOnly: { true }
        )
        await engine.load()
        conversation = engine
    }
}
