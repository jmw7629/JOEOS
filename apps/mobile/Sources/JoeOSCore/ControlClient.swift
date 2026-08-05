import Foundation
#if canImport(LocalAuthentication)
import LocalAuthentication
#endif

// MARK: - Typed control-plane models

public struct BackendProvider: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let key: String
    public let displayName: String
    public let providerType: String
    public let location: String
    public let status: String
    public let health: String
    public let streaming: Bool
    public let toolCalling: Bool
    public let structuredOutput: Bool
    public let contextWindow: Int
    public let privacyClass: String
    public let revision: Int

    enum CodingKeys: String, CodingKey {
        case id, key
        case displayName = "display_name"
        case providerType = "provider_type"
        case location, status, health, streaming
        case toolCalling = "tool_calling"
        case structuredOutput = "structured_output"
        case contextWindow = "context_window"
        case privacyClass = "privacy_class"
        case revision
    }
}

public struct BackendModel: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let providerID: UUID
    public let key: String
    public let displayName: String
    public let status: String
    public let streaming: Bool
    public let toolCalling: Bool
    public let vision: Bool
    public let reasoning: Bool
    public let contextLimit: Int
    public let privacyClass: String
    public let revision: Int

    enum CodingKeys: String, CodingKey {
        case id, key
        case providerID = "provider_id"
        case displayName = "display_name"
        case status, streaming
        case toolCalling = "tool_calling"
        case vision, reasoning
        case contextLimit = "context_limit"
        case privacyClass = "privacy_class"
        case revision
    }
}

public struct BackendAgent: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let key: String
    public let displayName: String
    public let status: String
    public let allowedTools: String
    public let deniedTools: String
    public let maxDelegationDepth: Int
    public let revision: Int
    public let latestVersionID: UUID

    enum CodingKeys: String, CodingKey {
        case id, key
        case displayName = "display_name"
        case status
        case allowedTools = "allowed_tools"
        case deniedTools = "denied_tools"
        case maxDelegationDepth = "max_delegation_depth"
        case revision
        case latestVersionID = "latest_version_id"
    }
}

public struct BackendAgentRun: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let conversationID: UUID
    public let agentID: UUID
    public let agentVersionID: UUID
    public let status: String
    public let failure: String
    public let traceID: String

    enum CodingKeys: String, CodingKey {
        case id
        case conversationID = "conversation_id"
        case agentID = "agent_id"
        case agentVersionID = "agent_version_id"
        case status, failure
        case traceID = "trace_id"
    }
}

public struct BackendTool: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let key: String
    public let displayName: String
    public let version: String
    public let category: String
    public let risk: String
    public let sideEffect: String
    public let executionAvailability: String
    public let status: String
    public let revision: Int

    enum CodingKeys: String, CodingKey {
        case id, key
        case displayName = "display_name"
        case version, category, risk
        case sideEffect = "side_effect"
        case executionAvailability = "execution_availability"
        case status, revision
    }
}

public struct BackendActionProposal: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let workspaceID: UUID
    public let conversationID: UUID?
    public let agentRunID: UUID?
    public let toolID: UUID
    public let actionType: String
    public let parameters: String
    public let canonicalTarget: String
    public let summary: String
    public let reversibility: String
    public let risk: String
    public let state: String
    public let payloadDigest: String
    public let expiresAt: Int
    public let traceID: String

    enum CodingKeys: String, CodingKey {
        case id
        case workspaceID = "workspace_id"
        case conversationID = "conversation_id"
        case agentRunID = "agent_run_id"
        case toolID = "tool_id"
        case actionType = "action_type"
        case parameters
        case canonicalTarget = "canonical_target"
        case summary, reversibility, risk, state
        case payloadDigest = "payload_digest"
        case expiresAt = "expires_at"
        case traceID = "trace_id"
    }

    public var isAwaitingExecutor: Bool { state == "approved_awaiting_executor" }
    public var isExecutionUnavailable: Bool { state == "execution_unavailable" }
}

public struct BackendPolicyDecision: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let proposalID: UUID
    public let result: String
    public let reasonCodes: String
    public let requiredCapabilities: String
    public let requiredApprovalCount: Int
    public let separationOfDuties: Bool
    public let stepUpRequired: String
    public let policyVersion: String

    enum CodingKeys: String, CodingKey {
        case id
        case proposalID = "proposal_id"
        case result
        case reasonCodes = "reason_codes"
        case requiredCapabilities = "required_capabilities"
        case requiredApprovalCount = "required_approval_count"
        case separationOfDuties = "separation_of_duties"
        case stepUpRequired = "step_up_required"
        case policyVersion = "policy_version"
    }
}

public struct BackendApprovalRequest: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let proposalID: UUID
    public let proposalDigest: String
    public let requiredCapability: String
    public let requiredApprovalCount: Int
    public let separationOfDuties: Bool
    public let stepUpRequired: String
    public let status: String
    public let expiresAt: Int

    enum CodingKeys: String, CodingKey {
        case id
        case proposalID = "proposal_id"
        case proposalDigest = "proposal_digest"
        case requiredCapability = "required_capability"
        case requiredApprovalCount = "required_approval_count"
        case separationOfDuties = "separation_of_duties"
        case stepUpRequired = "step_up_required"
        case status
        case expiresAt = "expires_at"
    }
}

public struct ApprovalChallenge: Codable, Equatable, Sendable {
    public let challengeID: UUID
    public let message: String
    public let expiresAt: Int

    enum CodingKeys: String, CodingKey {
        case challengeID = "challenge_id"
        case message
        case expiresAt = "expires_at"
    }
}

// MARK: - Approval UI state

public enum ApprovalFlowState: Equatable, Sendable {
    case loading
    case proposalDenied
    case proposalExpired
    case approvalRequired
    case approvalInProgress
    case approvedAwaitingExecutor
    case executionUnavailable
    case failed(reason: String)
}

public enum ApprovalClientError: Error, Equatable, LocalizedError, Sendable {
    case notFound
    case invalidSession
    case capabilityDenied
    case stepUpRequired
    case challengeExpired
    case challengeReplay
    case selfApprovalDenied
    case crossWorkspaceDenied
    case digestChanged

    public var errorDescription: String? {
        switch self {
        case .notFound: "The proposal or approval was not found."
        case .invalidSession: "The application session is invalid or revoked."
        case .capabilityDenied: "This principal lacks the required approval capability."
        case .stepUpRequired: "A one-time approval challenge signature is required."
        case .challengeExpired: "The approval challenge has expired."
        case .challengeReplay: "The approval challenge was already used."
        case .selfApprovalDenied: "Self-approval is denied under separation of duties."
        case .crossWorkspaceDenied: "Cross-workspace approval is denied."
        case .digestChanged: "The proposal changed; a new approval is required."
        }
    }
}

// MARK: - Control-plane client

/// Native Swift control-plane integration (Phase P3B).
///
/// The backend remains authoritative for providers, models, agents, tools,
/// proposals, policy, approvals, and council state. This client only requests
/// and renders; it never grants authority and never executes tools.
public struct ControlClient: Sendable {
    private static let sessionHeader = "X-JoeOS-Session"

    private let backend: JoeOSBackendClient
    private let endpoint: ValidatedEndpoint

    public init(backend: JoeOSBackendClient, endpoint: ValidatedEndpoint) {
        self.backend = backend
        self.endpoint = endpoint
    }

    private static func headers(_ sessionID: UUID) -> [String: String] {
        [sessionHeader: sessionID.uuidString.lowercased()]
    }

    // MARK: Providers / models

    public func providers(sessionID: UUID) async throws -> [BackendProvider] {
        struct Envelope: Decodable, Sendable {
            let providers: [BackendProvider]
        }
        return try await backend.get(Envelope.self, path: "/api/v1/control/providers",
                                     endpoint: endpoint, headers: Self.headers(sessionID)).providers
    }

    public func models(sessionID: UUID) async throws -> [BackendModel] {
        struct Envelope: Decodable, Sendable {
            let models: [BackendModel]
        }
        return try await backend.get(Envelope.self, path: "/api/v1/control/models",
                                     endpoint: endpoint, headers: Self.headers(sessionID)).models
    }

    // MARK: Agents / runs

    public func agents(sessionID: UUID) async throws -> [BackendAgent] {
        struct Envelope: Decodable, Sendable {
            let agents: [BackendAgent]
        }
        return try await backend.get(Envelope.self, path: "/api/v1/control/agents",
                                     endpoint: endpoint, headers: Self.headers(sessionID)).agents
    }

    public func startRun(agentID: UUID, conversationID: UUID, messageID: UUID, sessionID: UUID) async throws -> BackendAgentRun {
        struct Body: Encodable, Sendable {
            let conversationID: UUID
            let messageID: UUID
            enum CodingKeys: String, CodingKey {
                case conversationID = "conversation_id"
                case messageID = "message_id"
            }
        }
        return try await backend.post(
            Body(conversationID: conversationID, messageID: messageID),
            to: "/api/v1/control/agents/\(agentID.uuidString.lowercased())/runs",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    public func cancelRun(runID: UUID, sessionID: UUID) async throws {
        struct Empty: Encodable, Sendable {}
        _ = try await backend.post(
            Empty(),
            to: "/api/v1/control/runs/\(runID.uuidString.lowercased())/cancel",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        ) as EmptyResponse
    }

    // MARK: Tools

    public func tools(sessionID: UUID) async throws -> [BackendTool] {
        struct Envelope: Decodable, Sendable {
            let tools: [BackendTool]
        }
        return try await backend.get(Envelope.self, path: "/api/v1/control/tools",
                                     endpoint: endpoint, headers: Self.headers(sessionID)).tools
    }

    // MARK: Proposals

    public func proposals(sessionID: UUID) async throws -> [BackendActionProposal] {
        struct Envelope: Decodable, Sendable {
            let proposals: [BackendActionProposal]
        }
        return try await backend.get(Envelope.self, path: "/api/v1/control/proposals",
                                     endpoint: endpoint, headers: Self.headers(sessionID)).proposals
    }

    public func proposal(_ id: UUID, sessionID: UUID) async throws -> BackendActionProposal {
        try await backend.get(BackendActionProposal.self,
                              path: "/api/v1/control/proposals/\(id.uuidString.lowercased())",
                              endpoint: endpoint, headers: Self.headers(sessionID))
    }

    // MARK: Approvals

    public func approval(_ id: UUID, sessionID: UUID) async throws -> BackendApprovalRequest {
        try await backend.get(BackendApprovalRequest.self,
                              path: "/api/v1/control/approvals/\(id.uuidString.lowercased())",
                              endpoint: endpoint, headers: Self.headers(sessionID))
    }

    /// Requests a one-time approval challenge for a step-up-required decision.
    public func requestApprovalChallenge(
        proposalID: UUID,
        approvalRequestID: UUID,
        policyDecisionID: UUID,
        decision: String,
        deviceID: UUID,
        sessionID: UUID
    ) async throws -> ApprovalChallenge {
        struct Body: Encodable, Sendable {
            let proposalID: UUID
            let approvalRequestID: UUID
            let policyDecisionID: UUID
            let decision: String
            let deviceID: UUID
            enum CodingKeys: String, CodingKey {
                case proposalID = "proposal_id"
                case approvalRequestID = "approval_request_id"
                case policyDecisionID = "policy_decision_id"
                case decision
                case deviceID = "device_id"
            }
        }
        return try await backend.post(
            Body(proposalID: proposalID, approvalRequestID: approvalRequestID,
                 policyDecisionID: policyDecisionID, decision: decision, deviceID: deviceID),
            to: "/api/v1/control/approvals/challenge",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    /// Submits an approval decision. For step-up approvals, `signatureB64URL` is
    /// the DER ECDSA/SHA-256 signature over the exact challenge message made with
    /// the enrolled approval key (never the ordinary device-authentication key).
    public func decideApproval(
        approvalRequestID: UUID,
        proposalID: UUID,
        decision: String,
        reason: String,
        signatureB64URL: String?,
        challengeID: UUID?,
        deviceID: UUID?,
        sessionID: UUID
    ) async throws -> BackendActionProposal {
        struct Body: Encodable, Sendable {
            let proposalID: UUID
            let decision: String
            let reason: String
            let signature: String?
            let challengeID: UUID?
            let deviceID: UUID?
            enum CodingKeys: String, CodingKey {
                case proposalID = "proposal_id"
                case decision, reason, signature
                case challengeID = "challenge_id"
                case deviceID = "device_id"
            }
        }
        struct Response: Decodable, Sendable {
            let proposal: BackendActionProposal
        }
        let response: Response = try await backend.post(
            Body(proposalID: proposalID, decision: decision, reason: reason,
                 signature: signatureB64URL, challengeID: challengeID, deviceID: deviceID),
            to: "/api/v1/control/approvals/\(approvalRequestID.uuidString.lowercased())/decide",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
        return response.proposal
    }

    // MARK: Council

    public func councils(sessionID: UUID) async throws -> [BackendCouncil] {
        struct Envelope: Decodable, Sendable {
            let councils: [BackendCouncil]
        }
        return try await backend.get(Envelope.self, path: "/api/v1/control/councils",
                                     endpoint: endpoint, headers: Self.headers(sessionID)).councils
    }
}

public struct BackendCouncil: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let name: String
    public let purpose: String
    public let memberAgents: String
    public let quorumRule: String
    public let maximumRounds: Int
    public let status: String

    enum CodingKeys: String, CodingKey {
        case id, name, purpose
        case memberAgents = "member_agents"
        case quorumRule = "quorum_rule"
        case maximumRounds = "maximum_rounds"
        case status
    }
}

private struct EmptyResponse: Decodable, Sendable {}
