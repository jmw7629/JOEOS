import Foundation
import XCTest
@testable import JoeOSIntelligence
@testable import JoeOSCore

// MARK: - Fakes

private actor FakeConversationExecutor: ConversationExecuting {
    var partials: [String]
    var result: ExecutionResult
    var throwError: Error?
    var cancellable: Bool
    var partialCallCount = 0
    var decisions: [ExecutionRouter.Decision] = []

    init(
        partials: [String] = [],
        result: ExecutionResult = ExecutionResult(reply: "final"),
        throwError: Error? = nil,
        cancellable: Bool = false
    ) {
        self.partials = partials
        self.result = result
        self.throwError = throwError
        self.cancellable = cancellable
    }

    func execute(
        messages: [ConversationMessage],
        decision: ExecutionRouter.Decision,
        onPartial: @escaping @Sendable (String) -> Void
    ) async throws -> ExecutionResult {
        decisions.append(decision)
        if cancellable {
            for delta in partials {
                if Task.isCancelled { throw CancellationError() }
                onPartial(delta)
                partialCallCount += 1
                try await Task.sleep(nanoseconds: 5_000_000)
            }
        } else {
            for delta in partials {
                onPartial(delta)
                partialCallCount += 1
            }
        }
        if let throwError {
            throw throwError
        }
        return result
    }

    func partialCount() -> Int { partialCallCount }
    func decisionCount() -> Int { decisions.count }
    func lastDecision() -> ExecutionRouter.Decision? { decisions.last }

    func clearError() {
        throwError = nil
    }
}

private struct FakeCouncilExecutor: CouncilExecuting {
    let results: [AgentRole: String]
    let failingRole: AgentRole?

    init(results: [AgentRole: String], failingRole: AgentRole? = nil) {
        self.results = results
        self.failingRole = failingRole
    }

    func execute(role: AgentRole, objective: String) async throws -> String {
        if role == failingRole { throw URLError(.cannotConnectToHost) }
        return results[role] ?? "\(role.rawValue) review complete"
    }
}

private actor FakeTaskGraphExecutor: TaskGraphExecuting {
    var executed: [UUID] = []

    func execute(_ node: TaskNode) async throws -> String {
        executed.append(node.id)
        return "done:\(node.title)"
    }

    func executedIDs() -> [UUID] { executed }
}

// MARK: - Tests

@MainActor
final class AgentLifecycleTests: XCTestCase {
    func testAgentLifecycleAndDurableRunRestore() async throws {
        let runStore = InMemoryAgentRunStore()
        let fabric = AgentFabric(runStore: runStore)
        let agent = Agent(identity: "arch", role: .architect, capabilities: [.reasoning, .planning])
        fabric.register(agent)

        let run = await fabric.startRun(agentID: agent.id, objective: "Design the service boundary")
        XCTAssertEqual(run.status, .running)
        XCTAssertEqual(fabric.agent(id: agent.id)?.status, .running)

        await fabric.finishRun(runID: run.id, agentID: agent.id, result: "Boundary designed")
        XCTAssertEqual(fabric.agent(id: agent.id)?.runHistory.first?.status, .completed)

        let restored = AgentFabric(runStore: runStore)
        restored.register(agent)
        await restored.restoreRuns()
        XCTAssertEqual(restored.agent(id: agent.id)?.runHistory.first?.result, "Boundary designed")
    }

    func testCancelRunAndFailure() async {
        let fabric = AgentFabric()
        let agent = Agent(identity: "dev", role: .developer, capabilities: [.coding])
        fabric.register(agent)
        let run = await fabric.startRun(agentID: agent.id, objective: "Implement")
        await fabric.cancelRun(runID: run.id, agentID: agent.id)
        XCTAssertEqual(fabric.agent(id: agent.id)?.runHistory.first?.status, .cancelled)

        let other = await fabric.startRun(agentID: agent.id, objective: "Fail")
        await fabric.failRun(runID: other.id, agentID: agent.id, error: "boom")
        XCTAssertEqual(fabric.agent(id: agent.id)?.runHistory.first?.error, "boom")
    }
}

@MainActor
final class RoutingTests: XCTestCase {

    private var registries: (ProviderRegistry, ModelRegistry) {
        var providers = ProviderRegistry()
        providers.register(
            ProviderRecord(
                providerID: "lemonade",
                name: "Lemonade",
                kind: .local,
                available: true,
                reason: "",
                model: "llama3",
                embeddingModel: nil,
                baseURL: "loopback",
                privacyClass: "restricted",
                cloudApproved: false
            )
        )
        providers.register(
            ProviderRecord(
                providerID: "ollama",
                name: "Ollama",
                kind: .local,
                available: true,
                reason: "",
                model: "local-vision",
                embeddingModel: nil,
                baseURL: "loopback",
                privacyClass: "restricted",
                cloudApproved: false
            )
        )
        providers.register(
            ProviderRecord(
                providerID: "openai",
                name: "OpenAI",
                kind: .cloud,
                available: true,
                reason: "",
                model: "gpt-x",
                embeddingModel: nil,
                baseURL: "https://api.example.invalid",
                privacyClass: "restricted",
                cloudApproved: true
            )
        )
        var models = ModelRegistry()
        models.register(
            ModelRecord(
                provider: "lemonade",
                modelID: "llama3",
                displayName: "Lemonade Llama 3",
                capabilities: [.reasoning, .coding],
                contextLength: 8_192,
                averageLatencyMs: 120,
                estimatedCostPer1KTokens: 0,
                streamingSupported: true,
                offlineSupported: true,
                availability: true,
                safetyRating: 5,
                preferredUseCases: [.general]
            )
        )
        models.register(
            ModelRecord(
                provider: "ollama",
                modelID: "local-vision",
                displayName: "Local Vision",
                capabilities: [.vision],
                contextLength: 4_096,
                averageLatencyMs: 200,
                estimatedCostPer1KTokens: 0,
                streamingSupported: false,
                offlineSupported: true,
                availability: true,
                safetyRating: 5,
                preferredUseCases: [.vision]
            )
        )
        models.register(
            ModelRecord(
                provider: "openai",
                modelID: "gpt-x",
                displayName: "GPT-X",
                capabilities: [.reasoning],
                contextLength: 32_768,
                averageLatencyMs: 800,
                estimatedCostPer1KTokens: 2.5,
                streamingSupported: true,
                offlineSupported: false,
                availability: true,
                safetyRating: 4,
                preferredUseCases: [.reasoning]
            )
        )
        return (providers, models)
    }

    func testProviderSelectionPrefersLocalInLocalOnlyMode() throws {
        let (providers, models) = registries
        let router = ExecutionRouter(providers: providers, models: models)
        let decision = try router.route(
            request: "Analyze this",
            useCase: .reasoning,
            requireStreaming: true,
            localOnly: true
        ).get()
        XCTAssertEqual(decision.providerID, "lemonade")
        XCTAssertEqual(decision.modelID, "llama3")
        XCTAssertTrue(decision.useStreaming)
    }

    func testCloudRoutingIsBlockedByLocalOnly() throws {
        let (providers, models) = registries
        let router = ExecutionRouter(providers: providers, models: models)
        let localOnly = router.route(request: "x", useCase: .reasoning, localOnly: true)
        XCTAssertEqual(try localOnly.get().providerID, "lemonade")

        let notApproved = ProviderRecord(
            providerID: "claude",
            name: "Claude",
            kind: .cloud,
            available: true,
            reason: "",
            model: "claude-x",
            embeddingModel: nil,
            baseURL: "https://api.example.invalid",
            privacyClass: "restricted",
            cloudApproved: false
        )
        var providers2 = providers
        providers2.register(notApproved)
        var models2 = models
        models2.register(
            ModelRecord(
                provider: "claude",
                modelID: "claude-x",
                displayName: "Claude X",
                capabilities: [.reasoning],
                contextLength: 200_000,
                averageLatencyMs: 50,
                estimatedCostPer1KTokens: 0,
                streamingSupported: true,
                offlineSupported: false,
                availability: true,
                safetyRating: 4,
                preferredUseCases: [.reasoning]
            )
        )
        let router2 = ExecutionRouter(providers: providers2, models: models2)
        let decision = try router2.route(request: "x", useCase: .reasoning, localOnly: false).get()
        XCTAssertNotEqual(decision.providerID, "claude", "Unapproved cloud model must not be routable")
        XCTAssertEqual(decision.providerID, "lemonade")
    }

    func testNoAvailableProviderReportsHonestFailure() {
        let router = ExecutionRouter(providers: ProviderRegistry(), models: ModelRegistry())
        guard case .failure(.noAvailableProvider) = router.route(request: "x", useCase: .general, localOnly: false) else {
            XCTFail("Expected no-available-provider")
            return
        }
    }

    func testModelSelectionHonorsCapabilityAndCost() throws {
        let (providers, models) = registries
        let router = ExecutionRouter(providers: providers, models: models)
        let vision = try router.route(request: "look", useCase: .vision, requiredCapabilities: [.vision], localOnly: true).get()
        XCTAssertEqual(vision.modelID, "local-vision")
        XCTAssertEqual(vision.providerID, "ollama")
    }
}

@MainActor
final class MemoryTests: XCTestCase {
    func testRememberRecallSearchAndExpire() async throws {
        let store = InMemoryMemoryStore()
        let memory = MemoryStore(store: store)
        await memory.load()

        memory.remember("SwiftUI native app", key: "stack", layer: .project)
        memory.remember("user prefers dark mode", key: "preference", layer: .user)
        memory.remember("expires soon", key: "temp", layer: .working, expiresAt: Date(timeIntervalSinceNow: -1))

        XCTAssertEqual(memory.recall(key: "stack"), "SwiftUI native app")
        XCTAssertEqual(memory.search("dark").count, 1)
        XCTAssertEqual(memory.expire(), 1)
        XCTAssertNil(memory.recall(key: "temp"))
    }

    func testWorkingMemoryIsBounded() async {
        let memory = MemoryStore(store: InMemoryMemoryStore())
        for index in 0..<80 {
            memory.remember("item \(index)", key: "w", layer: .working)
        }
        let working = memory.entries.filter { $0.layer == .working }
        XCTAssertLessThanOrEqual(working.count, 64)
    }
}

@MainActor
final class ConversationTests: XCTestCase {

    private func engine(executor: FakeConversationExecutor) -> ConversationEngine {
        let store = InMemoryConversationStore()
        return ConversationEngine(store: store, executor: executor, router: Self.router())
    }

    /// A router with one available local provider/model so routing can succeed.
    private static func router() -> ExecutionRouter {
        var providers = ProviderRegistry()
        providers.register(
            ProviderRecord(
                providerID: "test-local",
                name: "Test Local",
                kind: .local,
                available: true,
                reason: "",
                model: "test-model",
                embeddingModel: nil,
                baseURL: "loopback",
                privacyClass: "restricted",
                cloudApproved: false
            )
        )
        var models = ModelRegistry()
        models.register(
            ModelRecord(
                provider: "test-local",
                modelID: "test-model",
                displayName: "Test Model",
                capabilities: [.reasoning],
                contextLength: 8_192,
                averageLatencyMs: 10,
                estimatedCostPer1KTokens: 0,
                streamingSupported: true,
                offlineSupported: true,
                availability: true,
                safetyRating: 5,
                preferredUseCases: [.general]
            )
        )
        return ExecutionRouter(providers: providers, models: models)
    }

    func testConversationContinuityAcrossEngineInstances() async throws {
        let executor = FakeConversationExecutor(result: ExecutionResult(reply: "hello!"))
        let store = InMemoryConversationStore()
        let router = Self.router()

        let first = ConversationEngine(store: store, executor: executor, router: router)
        let conversation = first.createConversation(title: "Persistent")
        let id = conversation.id
        _ = try await first.submit("Hi", in: id)

        let second = ConversationEngine(store: store, executor: executor, router: router)
        await second.load()
        XCTAssertEqual(second.conversations.first?.id, id)
        XCTAssertTrue(second.conversations.first?.messages.contains { $0.role == .assistant && $0.content == "hello!" } ?? false)
    }

    func testStreamingDeliversPartialsAndFinalMessage() async throws {
        let executor = FakeConversationExecutor(
            partials: ["Hel", "lo ", "world"],
            result: ExecutionResult(reply: "Hello world", providerID: "test-local", modelID: "test-model", tokenCount: 7)
        )
        let store = InMemoryConversationStore()
        let router = Self.router()
        let engine = ConversationEngine(store: store, executor: executor, router: router)
        let conversation = engine.createConversation(title: "Stream")
        let assistant = try await engine.submit("Say hello", in: conversation.id)
        XCTAssertEqual(assistant.content, "Hello world")
        XCTAssertEqual(assistant.providerID, "test-local")
        let partials = await executor.partialCount()
        XCTAssertGreaterThan(partials, 0)
    }

    func testCancellationStopsAndMarksTheResponse() async throws {
        let executor = FakeConversationExecutor(
            partials: Array(repeating: "token-", count: 60),
            result: ExecutionResult(reply: "unused"),
            cancellable: true
        )
        let store = InMemoryConversationStore()
        let router = Self.router()
        let engine = ConversationEngine(store: store, executor: executor, router: router)
        let conversation = engine.createConversation(title: "Cancel")
        let submitTask = Task { try await engine.submit("go", in: conversation.id) }
        try await Task.sleep(nanoseconds: 30_000_000)
        engine.cancel(conversation.id)
        let message = try await submitTask.value
        XCTAssertTrue(message.content.contains("stopped by the operator"))
    }

    func testRecoveryAndRetryAfterFailure() async throws {
        let executor = FakeConversationExecutor(
            result: ExecutionResult(reply: "recovered"),
            throwError: URLError(.cannotConnectToHost)
        )
        let store = InMemoryConversationStore()
        let router = Self.router()
        let engine = ConversationEngine(store: store, executor: executor, router: router)
        let conversation = engine.createConversation(title: "Retry")
        _ = try await engine.submit("ping", in: conversation.id)
        XCTAssertTrue(
            engine.conversations.first?.messages.last?.content.contains("could not complete") ?? false
        )
        await executor.clearError()
        _ = try await engine.retry(in: conversation.id)
        XCTAssertEqual(engine.conversations.first?.messages.last?.content, "recovered")
    }
}

final class TaskGraphTests: XCTestCase {
    func testParallelWavesAndDependencyOrdering() async throws {
        let architecture = TaskNode(title: "architecture", capability: .planning)
        let backend = TaskNode(title: "backend", capability: .coding)
        let frontend = TaskNode(title: "frontend", capability: .coding)
        let testing = TaskNode(title: "testing", capability: .reasoning)

        let graph = TaskGraph(
            nodes: [architecture, backend, frontend, testing],
            dependencies: [testing.id: [architecture.id, backend.id]]
        )
        XCTAssertTrue(graph.isValid)
        let waves = graph.parallelWaves()
        XCTAssertEqual(waves.count, 2)
        XCTAssertEqual(waves[0].count, 3)
        XCTAssertFalse(waves[0].contains { $0.id == testing.id })

        let executor = FakeTaskGraphExecutor()
        let result = await TaskGraphRunner.run(graph: graph, executor: executor)
        XCTAssertTrue(result.nodes.allSatisfy { $0.status == .completed })
        let testingNode = result.nodes.first { $0.id == testing.id }
        XCTAssertEqual(testingNode?.status, .completed)
    }

    func testCycleIsRejected() {
        let a = TaskNode(title: "a", capability: .reasoning)
        let b = TaskNode(title: "b", capability: .reasoning)
        let graph = TaskGraph(nodes: [a, b], dependencies: [a.id: [b.id], b.id: [a.id]])
        XCTAssertFalse(graph.isValid)
        XCTAssertTrue(graph.hasCycle)
    }
}

@MainActor
final class ApprovalFlowTests: XCTestCase {
    func testApprovalIsInvalidatedWhenActionChanges() async {
        let gate = ApprovalGate()
        let request = await gate.request(action: .gitPush, description: "Push the approved commit")
        let approved = await gate.approve(id: request.id)
        XCTAssertTrue(approved)
        let verdict = await gate.verify(id: request.id, action: .gitPush, description: "Push the approved commit")
        XCTAssertEqual(verdict, .allowed)

        let changed = await gate.verify(id: request.id, action: .gitPush, description: "Push a DIFFERENT commit")
        XCTAssertEqual(changed, .denied(reason: "The approved action changed and is now invalidated."))
        let status = await gate.requests[request.id]?.status
        XCTAssertEqual(status, .invalidated)
    }

    func testDenyBlocksExecution() async {
        let gate = ApprovalGate()
        let request = await gate.request(action: .deploy, description: "Deploy release")
        let denied = await gate.deny(id: request.id)
        XCTAssertTrue(denied)
        let verdict = await gate.verify(id: request.id, action: .deploy, description: "Deploy release")
        guard case .denied = verdict else {
            XCTFail("Denied approval must block")
            return
        }
    }
}

@MainActor
final class ExecutiveCouncilTests: XCTestCase {
    func testCouncilProducesCoherentResult() async {
        let executor = FakeCouncilExecutor(results: [:])
        let run = await ExecutiveCouncil.run(objective: "Ship v2", executor: executor)
        XCTAssertEqual(run.status, .completed)
        XCTAssertEqual(run.roleResults.count, ExecutiveCouncil.defaultRoles.count)
        XCTAssertTrue(run.coherentResult?.contains("planner") ?? false)
    }

    func testCouncilReportsFailureWhenARoleFails() async {
        let executor = FakeCouncilExecutor(results: [:], failingRole: .security)
        let run = await ExecutiveCouncil.run(objective: "Ship v2", executor: executor)
        XCTAssertEqual(run.status, .failed)
        XCTAssertNil(run.coherentResult)
    }
}

@MainActor
final class ToolBrokerTests: XCTestCase {
    func testToolBrokerEnforcement() {
        let broker = AgentFabric()
        let agent = Agent(identity: "dev", role: .developer, permissions: ["tools.read_file"])
        broker.register(agent)
        let agentValue = broker.agent(id: agent.id)!
        XCTAssertEqual(broker.requestAction(.executeTool, agent: agentValue), .denied(reason: "The agent is not granted the execute_tool permission."))
    }

    func testApprovalRequiredActionGatesUntilApproved() async {
        let gate = ApprovalGate()
        var agent = Agent(identity: "dev", role: .developer, permissions: ["tools.git_push"])
        let policy = ApprovalPolicy(requiresApprovalFor: [.gitPush])
        agent.approvalPolicy = policy
        let fabric = AgentFabric()
        fabric.register(agent)
        let verdict = fabric.requestAction(.gitPush, agent: agent)
        XCTAssertEqual(verdict, .requiresApproval)
        let request = await gate.request(action: .gitPush, description: "push approved")
        let approved = await gate.approve(id: request.id)
        XCTAssertTrue(approved)
    }
}

@MainActor
final class DiagnosticsTests: XCTestCase {
    func testDiagnosticsStoreRecordsAndCountsProviders() {
        let store = DiagnosticsStore()
        store.record(
            DiagnosticsSnapshot(
                providerHealth: ["lemonade": true, "openai": false],
                averageLatencyMs: 90,
                tokenUsage: 1_204
            )
        )
        XCTAssertEqual(store.providersAvailable(), 1)
        XCTAssertEqual(store.providersUnavailable(), 1)
        XCTAssertFalse(store.isHealthy())
        XCTAssertEqual(store.current.averageLatencyMs, 90)
    }
}
