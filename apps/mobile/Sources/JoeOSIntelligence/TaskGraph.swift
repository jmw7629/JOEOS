import Foundation

public enum TaskStatus: String, Codable, Equatable, Sendable {
    case pending
    case running
    case completed
    case failed
    case cancelled
}

public struct TaskNode: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var title: String
    public var capability: ModelCapability
    public var status: TaskStatus
    public var result: String?
    public var error: String?

    public init(
        id: UUID = UUID(),
        title: String,
        capability: ModelCapability,
        status: TaskStatus = .pending,
        result: String? = nil,
        error: String? = nil
    ) {
        self.id = id
        self.title = title
        self.capability = capability
        self.status = status
        self.result = result
        self.error = error
    }
}

/// A task graph: nodes that may execute independently and are grouped into
/// parallel waves by their dependencies. Large requests become graphs.
public struct TaskGraph: Equatable, Sendable {
    public let nodes: [TaskNode]
    public let dependencies: [UUID: Set<UUID>]

    public init(nodes: [TaskNode], dependencies: [UUID: Set<UUID>] = [:]) {
        self.nodes = nodes
        self.dependencies = dependencies
    }

    /// Validates the graph: every dependency must reference a known node and
    /// the graph must be acyclic.
    public var isValid: Bool {
        let ids = Set(nodes.map(\.id))
        guard dependencies.keys.allSatisfy(ids.contains),
              dependencies.values.allSatisfy({ $0.allSatisfy(ids.contains) })
        else {
            return false
        }
        return !hasCycle
    }

    public var hasCycle: Bool {
        var visited: Set<UUID> = []
        var stack: Set<UUID> = []
        func visit(_ id: UUID) -> Bool {
            if stack.contains(id) { return true }
            if visited.contains(id) { return false }
            visited.insert(id)
            stack.insert(id)
            for dependent in dependencies[id] ?? [] {
                if visit(dependent) { return true }
            }
            stack.remove(id)
            return false
        }
        for node in nodes {
            if visit(node.id) { return true }
        }
        return false
    }

    /// Nodes whose dependencies are all satisfied.
    public func readyNodes(done: Set<UUID>) -> [TaskNode] {
        nodes.filter { node in
            let remaining = (dependencies[node.id] ?? []).subtracting(done)
            return remaining.isEmpty && !done.contains(node.id)
        }
    }

    /// Successive parallel waves, each independently executable.
    public func parallelWaves() -> [[TaskNode]] {
        var waves: [[TaskNode]] = []
        var done: Set<UUID> = []
        while done.count < nodes.count {
            let ready = readyNodes(done: done)
            guard !ready.isEmpty else { return [] }
            waves.append(ready)
            done.formUnion(ready.map(\.id))
        }
        return waves
    }
}

public protocol TaskGraphExecuting: Sendable {
    func execute(_ node: TaskNode) async throws -> String
}

/// Executes a task graph wave by wave; nodes in a wave run in parallel and each
/// node may execute independently.
public enum TaskGraphRunner {

    public static func run(
        graph: TaskGraph,
        executor: any TaskGraphExecuting
    ) async -> TaskGraph {
        guard graph.isValid else {
            return invalidated(graph)
        }
        var result = graph
        var done: Set<UUID> = []
        for wave in graph.parallelWaves() {
            if Task.isCancelled {
                for node in result.nodes where node.status == .pending {
                    result = mark(node.id, status: .cancelled, in: result)
                }
                return result
            }
            await withTaskGroup(of: (UUID, TaskStatus, String?, String?).self) { group in
                for node in wave {
                    group.addTask {
                        do {
                            let output = try await executor.execute(node)
                            return (node.id, .completed, output, nil)
                        } catch is CancellationError {
                            return (node.id, .cancelled, nil, "Cancelled")
                        } catch {
                            return (node.id, .failed, nil, error.localizedDescription)
                        }
                    }
                }
                for await (id, status, value, error) in group {
                    result = apply(id, status: status, value: value, error: error, to: result)
                }
            }
            done.formUnion(wave.map(\.id))
            if result.nodes.contains(where: { $0.status == .failed }) {
                break
            }
        }
        return result
    }

    private static func apply(
        _ id: UUID,
        status: TaskStatus,
        value: String?,
        error: String?,
        to graph: TaskGraph
    ) -> TaskGraph {
        mark(id, status: status, value: value, error: error, in: graph)
    }

    private static func mark(
        _ id: UUID,
        status: TaskStatus,
        value: String? = nil,
        error: String? = nil,
        in graph: TaskGraph
    ) -> TaskGraph {
        let nodes = graph.nodes.map { node -> TaskNode in
            guard node.id == id else { return node }
            return TaskNode(
                id: node.id,
                title: node.title,
                capability: node.capability,
                status: status,
                result: status == .completed ? (value ?? node.result) : node.result,
                error: status == .failed ? (error ?? node.error) : node.error
            )
        }
        return TaskGraph(nodes: nodes, dependencies: graph.dependencies)
    }

    private static func invalidated(_ graph: TaskGraph) -> TaskGraph {
        let nodes = graph.nodes.map { node in
            TaskNode(id: node.id, title: node.title, capability: node.capability, status: .failed, error: "Invalid task graph.")
        }
        return TaskGraph(nodes: nodes, dependencies: graph.dependencies)
    }
}
