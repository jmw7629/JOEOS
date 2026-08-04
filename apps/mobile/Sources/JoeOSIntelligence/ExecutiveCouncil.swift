import Foundation

public enum CouncilStatus: String, Codable, Equatable, Sendable {
    case idle
    case running
    case completed
    case failed
    case cancelled
}

public struct RoleResult: Equatable, Sendable {
    public let role: AgentRole
    public let result: String
    public let durationMs: Int
}

public struct CouncilRun: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let objective: String
    public var status: CouncilStatus
    public var roleResults: [RoleResult]
    public var coherentResult: String?
    public var startedAt: Date
    public var finishedAt: Date?

    public init(
        id: UUID = UUID(),
        objective: String,
        status: CouncilStatus = .idle,
        roleResults: [RoleResult] = [],
        coherentResult: String? = nil,
        startedAt: Date = Date(),
        finishedAt: Date? = nil
    ) {
        self.id = id
        self.objective = objective
        self.status = status
        self.roleResults = roleResults
        self.coherentResult = coherentResult
        self.startedAt = startedAt
        self.finishedAt = finishedAt
    }
}

/// Executes one supervisory role for the council (backend-backed in production).
public protocol CouncilExecuting: Sendable {
    func execute(role: AgentRole, objective: String) async throws -> String
}

/// The Executive Council: supervisory agents collaborate on an objective. The
/// council never executes commands itself; it plans, reviews, and reports.
public enum ExecutiveCouncil {

    /// The fixed supervisory roles that collaborate on every council run.
    public static let defaultRoles: [AgentRole] = [
        .planner,
        .architect,
        .developer,
        .reviewer,
        .security,
        .qa,
        .documentation,
        .releaseManager,
    ]

    /// Runs the council over the given roles. Roles run sequentially so each
    /// can see the prior results; cancellation is checked between roles.
    public static func run(
        objective: String,
        executor: any CouncilExecuting,
        roles: [AgentRole] = defaultRoles,
        startedAt: Date = Date()
    ) async -> CouncilRun {
        var run = CouncilRun(objective: objective, status: .running, startedAt: startedAt)
        for role in roles {
            if Task.isCancelled {
                run.status = .cancelled
                run.finishedAt = Date()
                return run
            }
            let start = Date()
            do {
                let result = try await executor.execute(role: role, objective: objective)
                let duration = Int(Date().timeIntervalSince(start) * 1_000)
                run.roleResults.append(RoleResult(role: role, result: result, durationMs: duration))
            } catch is CancellationError {
                run.status = .cancelled
                run.finishedAt = Date()
                return run
            } catch {
                run.status = .failed
                run.finishedAt = Date()
                return run
            }
        }
        run.coherentResult = Self.coherentResult(from: run.roleResults)
        run.status = .completed
        run.finishedAt = Date()
        return run
    }

    /// Combines role outputs into one coherent result. If any role reported an
    /// empty or unavailable result, the council reports it honestly.
    public static func coherentResult(from results: [RoleResult]) -> String {
        guard !results.isEmpty else { return "The council produced no results." }
        let lines = results.map { "- \($0.role.rawValue.replacingOccurrences(of: "_", with: " ")): \($0.result)" }
        return "Executive Council summary\n" + lines.joined(separator: "\n")
    }
}
