import Foundation

public enum RoutingError: Error, Equatable, LocalizedError, Sendable {
    case noAvailableProvider
    case noSuitableModel(capability: ModelCapability)
    case cloudRoutingBlocked(providerID: String)

    public var errorDescription: String? {
        switch self {
        case .noAvailableProvider:
            "No inference provider is currently available on the JoeOS backend."
        case .noSuitableModel(let capability):
            "No available model supports the required capability: \(capability.rawValue)."
        case .cloudRoutingBlocked(let providerID):
            "Cloud routing to \(providerID) is blocked by local-only mode."
        }
    }
}

/// The JoeOS Execution Router. It decides which provider and model execute a
/// request based on the authoritative registries. The UI never selects a model;
/// the router does, and it reports honestly when nothing is available.
public struct ExecutionRouter: Sendable {

    public struct Decision: Equatable, Sendable {
        public let providerID: String
        public let modelID: String
        public let useStreaming: Bool
        public let useCase: ModelUseCase
        public let localOnly: Bool
        public let rationale: String

        public init(
            providerID: String,
            modelID: String,
            useStreaming: Bool,
            useCase: ModelUseCase,
            localOnly: Bool,
            rationale: String
        ) {
            self.providerID = providerID
            self.modelID = modelID
            self.useStreaming = useStreaming
            self.useCase = useCase
            self.localOnly = localOnly
            self.rationale = rationale
        }
    }

    private let providers: ProviderRegistry
    private let models: ModelRegistry

    public init(providers: ProviderRegistry, models: ModelRegistry) {
        self.providers = providers
        self.models = models
    }

    /// Routes a natural-language request. `capabilities` are the required
    /// model capabilities; the router picks the cheapest available model that
    /// satisfies them, preferring offline providers in local-only mode.
    public func route(
        request: String,
        useCase: ModelUseCase,
        requiredCapabilities: Set<ModelCapability> = [],
        requireStreaming: Bool = false,
        localOnly: Bool
    ) -> Result<Decision, RoutingError> {
        let candidates = providers.availableProviders(localOnly: localOnly)
        guard !candidates.isEmpty else {
            return .failure(.noAvailableProvider)
        }

        let capabilityToCheck = requiredCapabilities.isEmpty
            ? ModelCapability.reasoning
            : requiredCapabilities.sorted().first ?? .reasoning
        guard let model = models.best(
            for: capabilityToCheck,
            localOnly: localOnly,
            requireStreaming: requireStreaming
        ) else {
            return .failure(.noSuitableModel(capability: capabilityToCheck))
        }

        guard let provider = providers.provider(id: model.provider) else {
            return .failure(.noAvailableProvider)
        }
        guard providers.allowsRouting(to: provider, localOnly: localOnly) else {
            return .failure(.cloudRoutingBlocked(providerID: provider.providerID))
        }

        let rationale = "\(request.isEmpty ? "Request" : String(request.prefix(24))) → " +
            "\(model.displayName) (\(provider.name), \(provider.kind.rawValue), " +
            "localOnly: \(localOnly), streaming: \(requireStreaming))"
        return .success(
            Decision(
                providerID: provider.providerID,
                modelID: model.modelID,
                useStreaming: requireStreaming && model.streamingSupported,
                useCase: useCase,
                localOnly: localOnly,
                rationale: rationale
            )
        )
    }
}
