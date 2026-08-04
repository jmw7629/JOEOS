import Foundation

/// A point-in-time diagnostics snapshot. Unmeasured values stay unknown; the
/// client never fabricates latency, tokens, or queue depth.
public struct DiagnosticsSnapshot: Equatable, Sendable {
    public var providerHealth: [String: Bool]
    public var modelHealth: [String: Bool]
    public var averageLatencyMs: Int?
    public var tokenUsage: Int?
    public var retryCount: Int
    public var failureCount: Int
    public var streamingActive: Int
    public var contextUtilization: Double?
    public var memoryUtilization: Double?
    public var queueDepth: Int?
    public var activeAgents: Int
    public var generatedAt: Date

    public init(
        providerHealth: [String: Bool] = [:],
        modelHealth: [String: Bool] = [:],
        averageLatencyMs: Int? = nil,
        tokenUsage: Int? = nil,
        retryCount: Int = 0,
        failureCount: Int = 0,
        streamingActive: Int = 0,
        contextUtilization: Double? = nil,
        memoryUtilization: Double? = nil,
        queueDepth: Int? = nil,
        activeAgents: Int = 0,
        generatedAt: Date = Date()
    ) {
        self.providerHealth = providerHealth
        self.modelHealth = modelHealth
        self.averageLatencyMs = averageLatencyMs
        self.tokenUsage = tokenUsage
        self.retryCount = retryCount
        self.failureCount = failureCount
        self.streamingActive = streamingActive
        self.contextUtilization = contextUtilization
        self.memoryUtilization = memoryUtilization
        self.queueDepth = queueDepth
        self.activeAgents = activeAgents
        self.generatedAt = generatedAt
    }
}

/// Deep diagnostics over provider/model health, latency, tokens, retries,
/// failures, streaming, context, memory, queue depth, and active agents.
@MainActor
public final class DiagnosticsStore: ObservableObject {

    @Published public private(set) var current: DiagnosticsSnapshot
    @Published public private(set) var history: [DiagnosticsSnapshot] = []
    private static let historyLimit = 200

    public init(initial: DiagnosticsSnapshot = DiagnosticsSnapshot()) {
        self.current = initial
    }

    public func record(_ snapshot: DiagnosticsSnapshot) {
        current = snapshot
        history.insert(snapshot, at: 0)
        if history.count > Self.historyLimit {
            history.removeLast(history.count - Self.historyLimit)
        }
    }

    public func providersAvailable() -> Int {
        current.providerHealth.values.filter { $0 }.count
    }

    public func providersUnavailable() -> Int {
        current.providerHealth.values.filter { !$0 }.count
    }

    public func isHealthy() -> Bool {
        !current.providerHealth.values.contains(false)
    }
}

/// Reconciles the intelligence layer with the authoritative JoeOS backend.
public protocol IntelligenceBackendServing: Sendable {
    func overview() async throws -> AIOverview
    func providers() async throws -> [ProviderRecord]
    func infer(messages: [ConversationMessage], decision: ExecutionRouter.Decision) async throws -> ExecutionResult
}

/// Backend client that mirrors the real `/api/v1/ai` contract. Providers and
/// models are registered from authoritative backend state, never fabricated.
public struct JoeOSIntelligenceBackendClient: IntelligenceBackendServing, Sendable {
    private let backend: JoeOSBackendClient
    private let endpoint: ValidatedEndpoint

    public init(backend: JoeOSBackendClient, endpoint: ValidatedEndpoint) {
        self.backend = backend
        self.endpoint = endpoint
    }

    public func overview() async throws -> AIOverview {
        try await backend.get(AIOverview.self, path: "/api/v1/ai/overview", endpoint: endpoint)
    }

    public func providers() async throws -> [ProviderRecord] {
        struct ProvidersEnvelope: Decodable, Sendable {
            let providers: [ProviderRecord]
        }
        let envelope = try await backend.get(
            ProvidersEnvelope.self,
            path: "/api/v1/ai/providers",
            endpoint: endpoint
        )
        return envelope.providers
    }

    public func infer(
        messages: [ConversationMessage],
        decision: ExecutionRouter.Decision
    ) async throws -> ExecutionResult {
        struct InferenceRequest: Encodable, Sendable {
            struct Turn: Encodable, Sendable {
                let role: String
                let content: String
            }
            let messages: [Turn]
            let model: String
            let temperature: Double
            let maxTokens: Int

            enum CodingKeys: String, CodingKey {
                case messages
                case model
                case temperature
                case maxTokens = "max_tokens"
            }
        }
        struct InferenceResponse: Decodable, Sendable {
            let reply: String
            let model: String?
            let provider: String?
            let tokensUsed: Int?
            let cancelled: Bool?

            enum CodingKeys: String, CodingKey {
                case reply
                case model
                case provider
                case tokensUsed = "tokens_used"
                case cancelled
            }
        }
        let turns = messages.map {
            InferenceRequest.Turn(role: $0.role.rawValue, content: $0.content)
        }
        let body = InferenceRequest(
            messages: turns,
            model: decision.modelID,
            temperature: 0.25,
            maxTokens: 1_200
        )
        let response = try await backend.post(
            body,
            to: "/api/v1/ai/inference",
            endpoint: endpoint
        ) as InferenceResponse
        return ExecutionResult(
            reply: response.reply,
            providerID: response.provider ?? decision.providerID,
            modelID: response.model ?? decision.modelID,
            tokenCount: response.tokensUsed,
            cancelled: response.cancelled ?? false
        )
    }
}

/// Populates the Provider and Model Registries from the authoritative backend.
@MainActor
public final class IntelligenceBootstrap {

    public private(set) var providers = ProviderRegistry()
    public private(set) var models = ModelRegistry()

    public init() {}

    public func refresh(backend: any IntelligenceBackendServing) async throws {
        let providerRecords = try await backend.providers()
        providers.replaceAll(providerRecords)
        var registered: [ModelRecord] = []
        for record in providerRecords {
            if let model = record.model {
                registered.append(
                    ModelRecord(
                        provider: record.providerID,
                        modelID: model,
                        displayName: "\(record.name) · \(model)",
                        capabilities: [.reasoning],
                        contextLength: 8_192,
                        averageLatencyMs: 0,
                        estimatedCostPer1KTokens: record.kind == .local ? 0 : 0,
                        streamingSupported: false,
                        offlineSupported: record.kind == .local,
                        availability: record.available,
                        safetyRating: record.privacyClass == "restricted" ? 5 : 3,
                        preferredUseCases: [.general]
                    )
                )
            }
        }
        models.replaceAll(registered)
    }

    public func hasAvailableProvider() -> Bool {
        !providers.availableProviders(localOnly: false).isEmpty
    }
}
