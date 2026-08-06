import Foundation

public enum AgentAction: String, Codable, CaseIterable, Sendable {
    case executeTool = "execute_tool"
    case mutateRepository = "mutate_repository"
    case deploy
    case gitPush = "git_push"
    case databaseMigration = "database_migration"
    case credentialChange = "credential_change"
}

public struct RetryPolicy: Equatable, Sendable {
    public var maxAttempts: Int
    public var delaySeconds: TimeInterval

    public init(maxAttempts: Int = 2, delaySeconds: TimeInterval = 0.1) {
        self.maxAttempts = maxAttempts
        self.delaySeconds = delaySeconds
    }
}

public struct ApprovalPolicy: Equatable, Sendable {
    public var requiresApprovalFor: Set<AgentAction>

    public init(requiresApprovalFor: Set<AgentAction> = [.deploy, .gitPush, .databaseMigration, .credentialChange]) {
        self.requiresApprovalFor = requiresApprovalFor
    }

    public func requiresApproval(for action: AgentAction) -> Bool {
        requiresApprovalFor.contains(action)
    }
}

public struct AgentRun: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let objective: String
    public var status: AgentStatus
    public var startedAt: Date
    public var finishedAt: Date?
    public var result: String?
    public var error: String?

    public init(
        id: UUID = UUID(),
        objective: String,
        status: AgentStatus = .provisioning,
        startedAt: Date = Date(),
        finishedAt: Date? = nil,
        result: String? = nil,
        error: String? = nil
    ) {
        self.id = id
        self.objective = objective
        self.status = status
        self.startedAt = startedAt
        self.finishedAt = finishedAt
        self.result = result
        self.error = error
    }
}

public struct ArtifactRef: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var name: String
    public var kind: String
    public var registeredAt: Date

    public init(id: UUID = UUID(), name: String, kind: String, registeredAt: Date = Date()) {
        self.id = id
        self.name = name
        self.kind = kind
        self.registeredAt = registeredAt
    }
}

/// A production agent in the Agent Fabric.
public struct Agent: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var identity: String
    public var role: AgentRole
    public var permissions: Set<String>
    public var objectives: [String]
    public var status: AgentStatus
    public var capabilities: Set<ModelCapability>
    public var assignedProvider: String?
    public var assignedModel: String?
    public var assignedTools: [String]
    public var retryPolicy: RetryPolicy
    public var approvalPolicy: ApprovalPolicy
    public var runHistory: [AgentRun]
    public var artifacts: [ArtifactRef]

    public init(
        id: UUID = UUID(),
        identity: String,
        role: AgentRole,
        permissions: Set<String> = [],
        objectives: [String] = [],
        status: AgentStatus = .idle,
        capabilities: Set<ModelCapability> = [],
        assignedProvider: String? = nil,
        assignedModel: String? = nil,
        assignedTools: [String] = [],
        retryPolicy: RetryPolicy = RetryPolicy(),
        approvalPolicy: ApprovalPolicy = ApprovalPolicy(),
        runHistory: [AgentRun] = [],
        artifacts: [ArtifactRef] = []
    ) {
        self.id = id
        self.identity = identity
        self.role = role
        self.permissions = permissions
        self.objectives = objectives
        self.status = status
        self.capabilities = capabilities
        self.assignedProvider = assignedProvider
        self.assignedModel = assignedModel
        self.assignedTools = assignedTools
        self.retryPolicy = retryPolicy
        self.approvalPolicy = approvalPolicy
        self.runHistory = runHistory
        self.artifacts = artifacts
    }

    public func allows(_ permission: String) -> Bool {
        permissions.contains(permission)
    }
}

/// Durable agent-run persistence so runs survive a restart.
public protocol AgentRunPersisting: Sendable {
    func loadRuns(agentID: UUID) async -> [AgentRun]
    func saveRun(_ run: AgentRun, agentID: UUID) async throws
}

public actor InMemoryAgentRunStore: AgentRunPersisting {
    private var runs: [UUID: [AgentRun]] = [:]

    public init() {}

    public func loadRuns(agentID: UUID) async -> [AgentRun] {
        runs[agentID] ?? []
    }

    public func saveRun(_ run: AgentRun, agentID: UUID) async throws {
        var stored = runs[agentID] ?? []
        if let index = stored.firstIndex(where: { $0.id == run.id }) {
            stored[index] = run
        } else {
            stored.append(run)
        }
        runs[agentID] = stored
    }
}

/// The Agent Fabric: a registry of agents with identity, roles, permissions,
/// memory references, assigned provider/model/tools, retry and approval policy.
@MainActor
public final class AgentFabric: ObservableObject {

    @Published public private(set) var agents: [Agent]
    @Published public private(set) var lastError: String?

    private let runStore: any AgentRunPersisting

    public init(runStore: any AgentRunPersisting = InMemoryAgentRunStore(), agents: [Agent] = []) {
        self.runStore = runStore
        self.agents = agents
    }

    public func register(_ agent: Agent) {
        if let index = agents.firstIndex(where: { $0.id == agent.id }) {
            agents[index] = agent
        } else {
            agents.append(agent)
        }
    }

    public func agent(id: UUID) -> Agent? {
        agents.first(where: { $0.id == id })
    }

    public func agents(role: AgentRole) -> [Agent] {
        agents.filter { $0.role == role }
    }

    public func restoreRuns() async {
        for agent in agents {
            let runs = await runStore.loadRuns(agentID: agent.id)
            if let index = agents.firstIndex(where: { $0.id == agent.id }) {
                agents[index].runHistory = runs
            }
        }
    }

    @discardableResult
    public func startRun(agentID: UUID, objective: String) async -> AgentRun {
        let run = AgentRun(objective: objective, status: .running)
        update(agentID: agentID) { agent in
            var updated = agent
            updated.status = .running
            updated.runHistory.insert(run, at: 0)
            return updated
        }
        await persist(run, agentID: agentID)
        return run
    }

    public func finishRun(runID: UUID, agentID: UUID, result: String) async {
        await mutateRun(runID: runID, agentID: agentID) { run in
            var updated = run
            updated.status = .completed
            updated.finishedAt = Date()
            updated.result = result
            return updated
        }
    }

    public func failRun(runID: UUID, agentID: UUID, error: String) async {
        await mutateRun(runID: runID, agentID: agentID) { run in
            var updated = run
            updated.status = .failed
            updated.finishedAt = Date()
            updated.error = error
            return updated
        }
    }

    public func cancelRun(runID: UUID, agentID: UUID) async {
        await mutateRun(runID: runID, agentID: agentID) { run in
            var updated = run
            updated.status = .cancelled
            updated.finishedAt = Date()
            return updated
        }
    }

    public func registerArtifact(_ artifact: ArtifactRef, agentID: UUID) {
        update(agentID: agentID) { agent in
            var updated = agent
            updated.artifacts.append(artifact)
            return updated
        }
    }

    // MARK: - Tool Broker enforcement

    /// Requests an action. Registered safe tools pass; unregistered or
    /// high-risk actions are denied by the broker unless approved.
    public func requestAction(
        _ action: AgentAction,
        agent: Agent,
        requiresApproval: Bool = false
    ) -> ActionVerdict {
        guard agent.allows("tools.\(action.rawValue)") else {
            return .denied(reason: "The agent is not granted the \(action.rawValue) permission.")
        }
        if agent.approvalPolicy.requiresApproval(for: action) || requiresApproval {
            return .requiresApproval
        }
        return .allowed
    }

    private func update(agentID: UUID, transform: (Agent) -> Agent) {
        guard let index = agents.firstIndex(where: { $0.id == agentID }) else { return }
        agents[index] = transform(agents[index])
    }

    private func mutateRun(runID: UUID, agentID: UUID, transform: (AgentRun) -> AgentRun) async {
        guard let index = agents.firstIndex(where: { $0.id == agentID }),
              let runIndex = agents[index].runHistory.firstIndex(where: { $0.id == runID })
        else {
            return
        }
        let mutated = transform(agents[index].runHistory[runIndex])
        agents[index].runHistory[runIndex] = mutated
        if mutated.status == .completed || mutated.status == .failed || mutated.status == .cancelled {
            agents[index].status = .idle
        }
        try? await runStore.saveRun(mutated, agentID: agentID)
    }

    private func persist(_ run: AgentRun, agentID: UUID) async {
        try? await runStore.saveRun(run, agentID: agentID)
    }
}

public enum ActionVerdict: Equatable, Sendable {
    case allowed
    case denied(reason: String)
    case requiresApproval
}
