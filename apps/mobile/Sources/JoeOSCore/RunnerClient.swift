import Foundation

// MARK: - Runner plane typed models

public struct BackendRunner: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let installationID: UUID
    public let organizationID: UUID
    public let workspaceID: UUID
    public let displayName: String
    public let machineFingerprint: String
    public let runnerVersion: String
    public let protocolVersion: Int
    public let status: String
    public let health: String
    public let lastSeenAt: Int

    enum CodingKeys: String, CodingKey {
        case id
        case installationID = "installation_id"
        case organizationID = "organization_id"
        case workspaceID = "workspace_id"
        case displayName = "display_name"
        case machineFingerprint = "machine_fingerprint"
        case runnerVersion = "runner_version"
        case protocolVersion = "protocol_version"
        case status, health
        case lastSeenAt = "last_seen_at"
    }
}

public struct BackendRunnerHealth: Codable, Equatable, Sendable {
    public let runnerID: UUID
    public let status: String
    public let health: String
    public let lastSeenAt: Int

    enum CodingKeys: String, CodingKey {
        case runnerID = "runner_id"
        case status, health
        case lastSeenAt = "last_seen_at"
    }
}

public struct BackendExecutorDefinition: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let key: String
    public let displayName: String
    public let version: String
    public let acceptedTools: String
    public let riskFloor: String
    public let environmentPolicy: String
    public let networkPolicy: String
    public let filesystemPolicy: String
    public let secretPolicy: String
    public let status: String
    public let implementationDigest: String

    enum CodingKeys: String, CodingKey {
        case id, key
        case displayName = "display_name"
        case version
        case acceptedTools = "accepted_tools"
        case riskFloor = "risk_floor"
        case environmentPolicy = "environment_policy"
        case networkPolicy = "network_policy"
        case filesystemPolicy = "filesystem_policy"
        case secretPolicy = "secret_policy"
        case status
        case implementationDigest = "implementation_digest"
    }
}

public struct BackendExecutionJob: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let workspaceID: UUID
    public let proposalID: UUID
    public let proposalDigest: String
    public let policyDigest: String
    public let executorID: UUID
    public let runnerID: UUID
    public let target: String
    public let payloadDigest: String
    public let idempotencyKey: String
    public let state: String
    public let leaseGeneration: Int
    public let startedAt: Int?
    public let completedAt: Int?
    public let terminalClassification: String
    public let exitClassification: String
    public let resultSummary: String

    enum CodingKeys: String, CodingKey {
        case id
        case workspaceID = "workspace_id"
        case proposalID = "proposal_id"
        case proposalDigest = "proposal_digest"
        case policyDigest = "policy_digest"
        case executorID = "executor_id"
        case runnerID = "runner_id"
        case target
        case payloadDigest = "payload_digest"
        case idempotencyKey = "idempotency_key"
        case state
        case leaseGeneration = "lease_generation"
        case startedAt = "started_at"
        case completedAt = "completed_at"
        case terminalClassification = "terminal_classification"
        case exitClassification = "exit_classification"
        case resultSummary = "result_summary"
    }
}

public struct BackendArtifactRecord: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let jobID: UUID
    public let artifactType: String
    public let mediaType: String
    public let filename: String
    public let byteSize: Int
    public let sha256: String
    public let sensitivity: String

    enum CodingKeys: String, CodingKey {
        case id
        case jobID = "job_id"
        case artifactType = "artifact_type"
        case mediaType = "media_type"
        case filename
        case byteSize = "byte_size"
        case sha256
        case sensitivity
    }
}

public struct BackendSecretReferenceMetadata: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public let workspaceID: UUID
    public let key: String
    public let providerType: String
    public let purpose: String
    public let status: String

    enum CodingKeys: String, CodingKey {
        case id
        case workspaceID = "workspace_id"
        case key
        case providerType = "provider_type"
        case purpose, status
    }
}

// MARK: - Execution UI states

public enum ExecutionState: Equatable, Sendable {
    case approvedAwaitingExecutor
    case executionQueued
    case executionStarting
    case executionRunning
    case cancellationRequested
    case executionCancelled
    case executionSucceeded
    case executionFailed
    case executionTimedOut
    case executionInterrupted
    case runnerOffline
    case runnerRevoked
    case resultValidationFailed
    case artifactAvailable

    public static func from(_ jobState: String) -> ExecutionState {
        switch jobState {
        case "approved_awaiting_executor": .approvedAwaitingExecutor
        case "queued": .executionQueued
        case "pending_revalidation": .executionQueued
        case "leased", "acknowledged": .executionStarting
        case "running": .executionRunning
        case "cancellation_requested": .cancellationRequested
        case "cancelled": .executionCancelled
        case "succeeded": .executionSucceeded
        case "failed": .executionFailed
        case "timed_out": .executionTimedOut
        case "interrupted": .executionInterrupted
        case "runner_offline": .runnerOffline
        case "runner_revoked": .runnerRevoked
        case "result_validation_failed": .resultValidationFailed
        default: .executionQueued
        }
    }
}

public enum RunnerClientError: Error, Equatable, LocalizedError, Sendable {
    case invalidSession
    case capabilityDenied
    case notFound
    case crossWorkspaceDenied
    case runnerUnavailable

    public var errorDescription: String? {
        switch self {
        case .invalidSession: "The application session is invalid or revoked."
        case .capabilityDenied: "This principal lacks the required execution capability."
        case .notFound: "The execution record was not found."
        case .crossWorkspaceDenied: "Cross-workspace execution access is denied."
        case .runnerUnavailable: "No active compatible runner is available."
        }
    }
}

// MARK: - Runner client

/// Native Swift integration for the private runner plane (Phase P3C).
///
/// The backend is authoritative for runner state, execution jobs, leases, and
/// results. This client never connects to a runner directly, never creates a raw
/// command, and never retrieves secret values. Execution is requested only with
/// an approved proposal id and an idempotency key.
public struct RunnerClient: Sendable {
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

    public func runners(sessionID: UUID) async throws -> [BackendRunner] {
        struct Envelope: Decodable, Sendable {
            let runners: [BackendRunner]
        }
        return try await backend.get(Envelope.self, path: "/api/v1/control/runners",
                                     endpoint: endpoint, headers: Self.headers(sessionID)).runners
    }

    public func runnerHealth(runnerID: UUID, sessionID: UUID) async throws -> BackendRunnerHealth {
        try await backend.get(BackendRunnerHealth.self,
                              path: "/api/v1/control/runners/\(runnerID.uuidString.lowercased())/health",
                              endpoint: endpoint, headers: Self.headers(sessionID))
    }

    public func executors(sessionID: UUID) async throws -> [BackendExecutorDefinition] {
        struct Envelope: Decodable, Sendable {
            let executors: [BackendExecutorDefinition]
        }
        return try await backend.get(Envelope.self, path: "/api/v1/control/executors",
                                     endpoint: endpoint, headers: Self.headers(sessionID)).executors
    }

    /// Requests execution using only the approved proposal id and an idempotency
    /// key. The backend derives the job from authoritative records.
    public func requestExecution(proposalID: UUID, idempotencyKey: String, sessionID: UUID) async throws -> BackendExecutionJob {
        struct Body: Encodable, Sendable {
            let proposalID: UUID
            let idempotencyKey: String
            enum CodingKeys: String, CodingKey {
                case proposalID = "proposal_id"
                case idempotencyKey = "idempotency_key"
            }
        }
        return try await backend.post(
            Body(proposalID: proposalID, idempotencyKey: idempotencyKey),
            to: "/api/v1/control/executions",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    public func executions(state: String? = nil, sessionID: UUID) async throws -> [BackendExecutionJob] {
        struct Envelope: Decodable, Sendable {
            let executions: [BackendExecutionJob]
        }
        var path = "/api/v1/control/executions"
        if let state {
            path += "?state=\(state)"
        }
        return try await backend.get(Envelope.self, path: path,
                                     endpoint: endpoint, headers: Self.headers(sessionID)).executions
    }

    public func execution(jobID: UUID, sessionID: UUID) async throws -> BackendExecutionJob {
        try await backend.get(BackendExecutionJob.self,
                              path: "/api/v1/control/executions/\(jobID.uuidString.lowercased())",
                              endpoint: endpoint, headers: Self.headers(sessionID))
    }

    public func cancel(jobID: UUID, sessionID: UUID) async throws -> Bool {
        struct Empty: Encodable, Sendable {}
        struct Response: Decodable, Sendable {
            let cancelled: Bool
        }
        let response: Response = try await backend.post(
            Empty(),
            to: "/api/v1/control/executions/\(jobID.uuidString.lowercased())/cancel",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
        return response.cancelled
    }

    public func artifacts(jobID: UUID, sessionID: UUID) async throws -> [BackendArtifactRecord] {
        struct Envelope: Decodable, Sendable {
            let artifacts: [BackendArtifactRecord]
        }
        return try await backend.get(
            Envelope.self,
            path: "/api/v1/control/executions/\(jobID.uuidString.lowercased())/artifacts",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        ).artifacts
    }

    public func secretReferences(sessionID: UUID) async throws -> [BackendSecretReferenceMetadata] {
        struct Envelope: Decodable, Sendable {
            let secrets: [BackendSecretReferenceMetadata]
        }
        return try await backend.get(Envelope.self, path: "/api/v1/control/secrets",
                                     endpoint: endpoint, headers: Self.headers(sessionID)).secrets
    }
}
