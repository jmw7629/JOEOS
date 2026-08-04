import Foundation

public enum ApprovalStatus: String, Codable, Equatable, Sendable {
    case pending
    case approved
    case denied
    case invalidated
}

/// An approval request is bound to the exact action and description at
/// approval time. Changing the action afterwards invalidates the approval.
public struct ApprovalRequest: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let action: AgentAction
    public let description: String
    public let contentHash: String
    public var status: ApprovalStatus
    public var requestedAt: Date
    public var decidedAt: Date?

    public init(
        id: UUID = UUID(),
        action: AgentAction,
        description: String,
        contentHash: String? = nil,
        status: ApprovalStatus = .pending,
        requestedAt: Date = Date(),
        decidedAt: Date? = nil
    ) {
        self.id = id
        self.action = action
        self.description = description
        self.contentHash = contentHash ?? ApprovalGate.hash(action: action, description: description)
        self.status = status
        self.requestedAt = requestedAt
        self.decidedAt = decidedAt
    }
}

/// The Approval Service gate. Potentially destructive operations require
/// approval; approvals are bound to the exact action content.
public actor ApprovalGate {

    public private(set) var requests: [UUID: ApprovalRequest] = [:]

    public init() {}

    public static func hash(action: AgentAction, description: String) -> String {
        let material = "\(action.rawValue)|\(description.trimmingCharacters(in: .whitespacesAndNewlines))"
        var hasher = Hasher()
        hasher.combine(material)
        return String(hasher.finalize(), radix: 16)
    }

    public func evaluate(
        action: AgentAction,
        description: String,
        requiresApproval: Bool
    ) -> ApprovalVerdict {
        if !requiresApproval {
            return .allowed
        }
        return .requiresApproval
    }

    @discardableResult
    public func request(action: AgentAction, description: String) -> ApprovalRequest {
        let request = ApprovalRequest(action: action, description: description)
        requests[request.id] = request
        return request
    }

    /// Approves the exact request content. The approval is bound to the hash.
    public func approve(id: UUID) -> Bool {
        guard var request = requests[id], request.status == .pending else { return false }
        request.status = .approved
        request.decidedAt = Date()
        requests[id] = request
        return true
    }

    public func deny(id: UUID) -> Bool {
        guard var request = requests[id], request.status == .pending else { return false }
        request.status = .denied
        request.decidedAt = Date()
        requests[id] = request
        return true
    }

    /// Verifies an approval against the action that is about to run. If the
    /// action or description changed after approval, the approval is invalid.
    public func verify(id: UUID, action: AgentAction, description: String) -> ApprovalVerdict {
        guard var request = requests[id] else { return .denied(reason: "Unknown approval.") }
        if request.contentHash != ApprovalGate.hash(action: action, description: description) {
            request.status = .invalidated
            requests[id] = request
            return .denied(reason: "The approved action changed and is now invalidated.")
        }
        switch request.status {
        case .approved:
            return .allowed
        case .denied:
            return .denied(reason: "The approval was denied.")
        case .invalidated:
            return .denied(reason: "The approval was invalidated.")
        case .pending:
            return .requiresApproval
        }
    }
}

public enum ApprovalVerdict: Equatable, Sendable {
    case allowed
    case requiresApproval
    case denied(reason: String)
}
