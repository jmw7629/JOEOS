import Foundation
import JoeOSCore

/// Persistence for canonical conversations. Injected so the engine is testable
/// without I/O and so future clients share one conversation store.
public protocol ConversationPersisting: Sendable {
    func loadConversations() async -> [Conversation]
    func save(_ conversation: Conversation) async throws
    func deleteConversation(id: UUID) async throws
}

/// In-memory conversation store (tests and development).
public actor InMemoryConversationStore: ConversationPersisting {
    private var conversations: [UUID: Conversation] = [:]

    public init() {}

    public func loadConversations() async -> [Conversation] {
        conversations.values.sorted { $0.updatedAt > $1.updatedAt }
    }

    public func save(_ conversation: Conversation) async throws {
        conversations[conversation.id] = conversation
    }

    public func deleteConversation(id: UUID) async throws {
        conversations[id] = nil
    }
}

public struct ExecutionResult: Equatable, Sendable {
    public var reply: String
    public var providerID: String?
    public var modelID: String?
    public var tokenCount: Int?
    public var cancelled: Bool

    public init(
        reply: String,
        providerID: String? = nil,
        modelID: String? = nil,
        tokenCount: Int? = nil,
        cancelled: Bool = false
    ) {
        self.reply = reply
        self.providerID = providerID
        self.modelID = modelID
        self.tokenCount = tokenCount
        self.cancelled = cancelled
    }
}

/// Executes one routed request against the JoeOS backend. The UI never talks
/// to a provider directly; the backend does the actual inference.
public protocol ConversationExecuting: Sendable {
    func execute(
        messages: [ConversationMessage],
        decision: ExecutionRouter.Decision,
        onPartial: @escaping @Sendable (String) -> Void
    ) async throws -> ExecutionResult
}

/// The Conversation Engine: the operating system's primary interface. It owns
/// canonical conversations, continuity (full context), routing, streaming,
/// cancellation, and retry — the UI only submits text.
@MainActor
public final class ConversationEngine: ObservableObject {

    @Published public private(set) var conversations: [Conversation]
    @Published public private(set) var activeConversationID: UUID?
    @Published public private(set) var activePartial: ConversationMessage?
    @Published public private(set) var lastError: Error?

    private let store: any ConversationPersisting
    private let executor: any ConversationExecuting
    private let router: ExecutionRouter
    private var localOnly: () -> Bool
    private var runningTasks: [UUID: Task<Void, Never>] = [:]

    public init(
        store: any ConversationPersisting = InMemoryConversationStore(),
        executor: any ConversationExecuting,
        router: ExecutionRouter,
        localOnly: @escaping () -> Bool = { false }
    ) {
        self.store = store
        self.executor = executor
        self.router = router
        self.localOnly = localOnly
        self.conversations = []
    }

    public func load() async {
        conversations = await store.loadConversations()
    }

    @discardableResult
    public func createConversation(title: String = "New conversation") -> Conversation {
        let conversation = Conversation(title: title)
        conversations.insert(conversation, at: 0)
        activeConversationID = conversation.id
        let store = self.store
        Task {
            try? await store.save(conversation)
        }
        return conversation
    }

    public func select(_ id: UUID) {
        activeConversationID = id
    }

    public func deleteConversation(id: UUID) async throws {
        try await store.deleteConversation(id: id)
        conversations.removeAll { $0.id == id }
        if activeConversationID == id {
            activeConversationID = conversations.first?.id
        }
    }

    /// Submits a user message, routes it, streams partial output, and persists
    /// the final assistant message. The conversation id never changes across
    /// restart (canonical identity).
    @discardableResult
    public func submit(_ content: String, in conversationID: UUID) async throws -> ConversationMessage {
        guard let conversation = conversations.first(where: { $0.id == conversationID }) else {
            throw ConversationEngineError.unknownConversation
        }
        let trimmed = content.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw ConversationEngineError.emptyMessage
        }
        let userMessage = ConversationMessage(role: .user, content: trimmed)
        var updated = conversation
        updated.messages.append(userMessage)
        updated.updatedAt = Date()
        await persist(updated)
        replaceConversation(updated)

        let decisionResult = router.route(
            request: trimmed,
            useCase: .general,
            requireStreaming: true,
            localOnly: localOnly()
        )
        guard case .success(let decision) = decisionResult else {
            if case .failure(let error) = decisionResult {
                lastError = error
            }
            throw ConversationEngineError.noRoute
        }

        return try await run(
            conversation: updated,
            userMessage: userMessage,
            decision: decision
        )
    }

    /// Cancels the in-flight assistant response for a conversation.
    public func cancel(_ conversationID: UUID) {
        guard let task = runningTasks[conversationID] else { return }
        task.cancel()
        runningTasks[conversationID] = nil
    }

    public func cancelActive() {
        guard let activeConversationID else { return }
        cancel(activeConversationID)
    }

    /// Resubmits the last user message in a conversation (recovery/retry).
    @discardableResult
    public func retry(in conversationID: UUID) async throws -> ConversationMessage? {
        guard let conversation = conversations.first(where: { $0.id == conversationID }),
              let lastUser = conversation.messages.last(where: { $0.role == .user })
        else {
            return nil
        }
        var fresh = conversation
        fresh.messages.removeAll { $0.role == .assistant && $0.isPartial }
        fresh.messages.removeAll { $0.role == .assistant && $0.createdAt > lastUser.createdAt }
        replaceConversation(fresh)
        return try await submit(lastUser.content, in: conversationID)
    }

    // MARK: - Execution

    private func run(
        conversation: Conversation,
        userMessage: ConversationMessage,
        decision: ExecutionRouter.Decision
    ) async throws -> ConversationMessage {
        let assistantID = UUID()
        let partial = ConversationMessage(
            id: assistantID,
            role: .assistant,
            content: "",
            providerID: decision.providerID,
            modelID: decision.modelID,
            isPartial: true
        )
        var inFlight = conversation
        inFlight.messages.append(partial)
        replaceConversation(inFlight)
        activePartial = partial

        let task = Task { @MainActor [weak self] in
            guard let self else { return }
            await self.executeAssistant(
                in: inFlight,
                assistantID: assistantID,
                decision: decision
            )
        }
        runningTasks[conversation.id] = task
        await task.value
        guard let final = conversations.first(where: { $0.id == conversation.id })?
            .messages.first(where: { $0.id == assistantID })
        else {
            throw ConversationEngineError.unknownConversation
        }
        activePartial = nil
        return final
    }

    private func executeAssistant(
        in conversation: Conversation,
        assistantID: UUID,
        decision: ExecutionRouter.Decision
    ) async {
        do {
            let context = conversation.messages.filter { !$0.isPartial || $0.id == assistantID }
            let result = try await executor.execute(
                messages: context,
                decision: decision,
                onPartial: { [weak self] delta in
                    Task { @MainActor in
                        self?.applyPartial(delta, in: conversation.id, assistantID: assistantID)
                    }
                }
            )
            try Task.checkCancellation()
            var updated = conversation
            updated.messages.removeAll { $0.id == assistantID }
            updated.messages.append(
                ConversationMessage(
                    id: assistantID,
                    role: .assistant,
                    content: result.reply,
                    createdAt: conversation.messages.first(where: { $0.id == assistantID })?.createdAt ?? Date(),
                    providerID: result.providerID ?? decision.providerID,
                    modelID: result.modelID ?? decision.modelID,
                    tokenCount: result.tokenCount,
                    isPartial: false
                )
            )
            updated.updatedAt = Date()
            replaceConversation(updated)
            await persist(updated)
        } catch is CancellationError {
            await markCancelled(in: conversation.id, assistantID: assistantID, decision: decision)
        } catch {
            lastError = error
            await markFailed(in: conversation.id, assistantID: assistantID)
        }
    }

    private func applyPartial(_ delta: String, in conversationID: UUID, assistantID: UUID) {
        guard var conversation = conversations.first(where: { $0.id == conversationID }),
              let index = conversation.messages.firstIndex(where: { $0.id == assistantID })
        else {
            return
        }
        conversation.messages[index].content += delta
        replaceConversation(conversation)
        activePartial = conversation.messages[index]
    }

    private func markCancelled(in conversationID: UUID, assistantID: UUID, decision: ExecutionRouter.Decision) async {
        guard var conversation = conversations.first(where: { $0.id == conversationID }),
              let index = conversation.messages.firstIndex(where: { $0.id == assistantID })
        else {
            return
        }
        conversation.messages[index].content += "\n[Generation stopped by the operator]"
        conversation.messages[index].isPartial = false
        replaceConversation(conversation)
        await persist(conversation)
    }

    private func markFailed(in conversationID: UUID, assistantID: UUID) async {
        guard var conversation = conversations.first(where: { $0.id == conversationID }),
              let index = conversation.messages.firstIndex(where: { $0.id == assistantID })
        else {
            return
        }
        conversation.messages[index].content = "The backend could not complete this response. Review the connection and retry."
        conversation.messages[index].isPartial = false
        replaceConversation(conversation)
        await persist(conversation)
    }

    private func replaceConversation(_ conversation: Conversation) {
        if let index = conversations.firstIndex(where: { $0.id == conversation.id }) {
            conversations[index] = conversation
        } else {
            conversations.insert(conversation, at: 0)
        }
    }

    private func persist(_ conversation: Conversation) async {
        try? await store.save(conversation)
    }
}

public enum ConversationEngineError: Error, Equatable, LocalizedError, Sendable {
    case unknownConversation
    case emptyMessage
    case noRoute

    public var errorDescription: String? {
        switch self {
        case .unknownConversation:
            "The conversation no longer exists."
        case .emptyMessage:
            "Enter a message first."
        case .noRoute:
            "No model is available on the JoeOS backend to answer this request."
        }
    }
}
