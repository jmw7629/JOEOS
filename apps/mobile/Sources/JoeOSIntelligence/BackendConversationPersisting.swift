import Foundation
import JoeOSCore

/// Backend-backed `ConversationPersisting` adapter (Phase P3A).
///
/// The backend remains canonical for conversation history. This adapter reads
/// authoritative conversations and messages from the JoeOS backend and maps
/// them onto the local engine models. Writes to history are no-ops because
/// accepted messages are authoritative on the backend; new messages are
/// submitted through `ConversationClient.submit`/`stream`, never written
/// locally. The local client may additionally keep a bounded display cache,
/// unsent drafts, and the last acknowledged event cursor — none of which are
/// authoritative.
public final class BackendConversationPersisting: ConversationPersisting, @unchecked Sendable {

    private let client: ConversationClient
    private let endpoint: ValidatedEndpoint
    private let sessionProvider: @Sendable () -> UUID?

    public init(
        client: ConversationClient,
        endpoint: ValidatedEndpoint,
        sessionProvider: @escaping @Sendable () -> UUID?
    ) {
        self.client = client
        self.endpoint = endpoint
        self.sessionProvider = sessionProvider
    }

    public func loadConversations() async -> [Conversation] {
        guard let sessionID = sessionProvider() else { return [] }
        do {
            let list = try await client.list(sessionID: sessionID)
            return list.conversations.map(Self.map)
        } catch {
            return []
        }
    }

    public func save(_ conversation: Conversation) async throws {
        // The backend is authoritative for conversation history. Locally edited
        // drafts are not persisted here; submit/retry happen through the client.
        _ = conversation
    }

    public func deleteConversation(id: UUID) async throws {
        _ = id
    }

    /// Maps a canonical backend conversation onto the local engine model.
    public static func map(_ backend: BackendConversation) -> Conversation {
        Conversation(
            id: backend.conversationID,
            title: backend.title,
            createdAt: Date(timeIntervalSince1970: TimeInterval(backend.createdAt)),
            updatedAt: Date(timeIntervalSince1970: TimeInterval(backend.updatedAt)),
            messages: backend.messages.map { message in
                ConversationMessage(
                    id: message.messageID,
                    role: Self.role(message.role),
                    content: message.content,
                    createdAt: Date(timeIntervalSince1970: TimeInterval(message.createdAt)),
                    providerID: message.provider,
                    modelID: message.model,
                    tokenCount: message.tokensUsed,
                    isPartial: false
                )
            }
        )
    }

    private static func role(_ raw: String) -> ConversationRole {
        switch raw {
        case "user": .user
        case "assistant": .assistant
        case "system": .system
        default: .tool
        }
    }
}
