import Foundation

// MARK: - Shared intelligence types

public enum ProviderKind: String, Codable, Sendable {
    case local
    case cloud
}

public enum ModelCapability: String, Codable, CaseIterable, Sendable, Comparable {
    case vision
    case reasoning
    case coding
    case planning
    case memory
    case toolUse = "tool_use"
    case streaming
    case offline

    public static func < (lhs: ModelCapability, rhs: ModelCapability) -> Bool {
        lhs.rawValue < rhs.rawValue
    }
}

public enum ModelUseCase: String, Codable, CaseIterable, Sendable {
    case general
    case coding
    case reasoning
    case creative
    case vision
    case agentic
}

public enum AgentRole: String, Codable, CaseIterable, Sendable {
    case planner
    case architect
    case developer
    case reviewer
    case security
    case qa
    case performance
    case documentation
    case releaseManager = "release_manager"
    case productManager = "product_manager"
    case ux
    case infrastructure
}

public enum AgentStatus: String, Codable, Equatable, Sendable {
    case idle
    case provisioning
    case running
    case waiting
    case cancelled
    case failed
    case completed
}

public enum ConversationRole: String, Codable, Sendable {
    case system
    case user
    case assistant
    case tool
}

/// A provider as reported by the JoeOS backend (`/api/v1/ai/providers`).
/// The client never talks to a provider directly; this mirrors the backend's
/// authoritative availability so the router can plan honestly.
public struct ProviderRecord: Codable, Equatable, Sendable {
    public let providerID: String
    public let name: String
    public let kind: ProviderKind
    public let available: Bool
    public let reason: String
    public let model: String?
    public let embeddingModel: String?
    public let baseURL: String
    public let privacyClass: String
    public let cloudApproved: Bool

    enum CodingKeys: String, CodingKey {
        case providerID = "provider_id"
        case name
        case kind
        case available
        case reason
        case model
        case embeddingModel = "embedding_model"
        case baseURL = "base_url"
        case privacyClass = "privacy_class"
        case cloudApproved = "cloud_approved"
    }
}

/// The backend's AI overview (`/api/v1/ai/overview`).
public struct AIOverview: Codable, Equatable, Sendable {
    public let providerAvailable: Bool
    public let providerReason: String
    public let model: String?
    public let embeddingAvailable: Bool
    public let embeddingModel: String?
    public let interpretationCount: Int
    public let generatedAt: String
    public let message: String

    enum CodingKeys: String, CodingKey {
        case providerAvailable = "provider_available"
        case providerReason = "provider_reason"
        case model
        case embeddingAvailable = "embedding_available"
        case embeddingModel = "embedding_model"
        case interpretationCount = "interpretation_count"
        case generatedAt = "generated_at"
        case message
    }
}

/// A model entry in the client-side Model Registry.
public struct ModelRecord: Identifiable, Equatable, Sendable {
    public var id: String { "\(provider):\(modelID)" }
    public let provider: String
    public let modelID: String
    public let displayName: String
    public let capabilities: Set<ModelCapability>
    public let contextLength: Int
    public let averageLatencyMs: Int
    public let estimatedCostPer1KTokens: Double
    public let streamingSupported: Bool
    public let offlineSupported: Bool
    public let availability: Bool
    public let safetyRating: Int
    public let preferredUseCases: [ModelUseCase]

    public init(
        provider: String,
        modelID: String,
        displayName: String,
        capabilities: Set<ModelCapability>,
        contextLength: Int,
        averageLatencyMs: Int,
        estimatedCostPer1KTokens: Double,
        streamingSupported: Bool,
        offlineSupported: Bool,
        availability: Bool,
        safetyRating: Int,
        preferredUseCases: [ModelUseCase]
    ) {
        self.provider = provider
        self.modelID = modelID
        self.displayName = displayName
        self.capabilities = capabilities
        self.contextLength = contextLength
        self.averageLatencyMs = averageLatencyMs
        self.estimatedCostPer1KTokens = estimatedCostPer1KTokens
        self.streamingSupported = streamingSupported
        self.offlineSupported = offlineSupported
        self.availability = availability
        self.safetyRating = safetyRating
        self.preferredUseCases = preferredUseCases
    }

    public func canHandle(_ capability: ModelCapability) -> Bool {
        capabilities.contains(capability)
    }
}

/// One message in a persistent conversation.
public struct ConversationMessage: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var role: ConversationRole
    public var content: String
    public var createdAt: Date
    public var providerID: String?
    public var modelID: String?
    public var tokenCount: Int?
    public var isPartial: Bool

    public init(
        id: UUID = UUID(),
        role: ConversationRole,
        content: String,
        createdAt: Date = Date(),
        providerID: String? = nil,
        modelID: String? = nil,
        tokenCount: Int? = nil,
        isPartial: Bool = false
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.createdAt = createdAt
        self.providerID = providerID
        self.modelID = modelID
        self.tokenCount = tokenCount
        self.isPartial = isPartial
    }
}

/// A canonical conversation with persistent identity and ordered messages.
public struct Conversation: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var title: String
    public var createdAt: Date
    public var updatedAt: Date
    public var messages: [ConversationMessage]

    public init(
        id: UUID = UUID(),
        title: String,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        messages: [ConversationMessage] = []
    ) {
        self.id = id
        self.title = title
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.messages = messages
    }
}
