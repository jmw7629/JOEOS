import Foundation

// MARK: - Canonical conversation wire models

public struct BackendConversationMessage: Codable, Equatable, Sendable, Identifiable {
    public let messageID: UUID
    public let role: String
    public let content: String
    public let status: String
    public let provider: String?
    public let model: String?
    public let tokensUsed: Int?
    public let createdAt: Int
    public let completedAt: Int?
    public let errorDetail: String

    public var id: UUID { messageID }

    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case role
        case content
        case status
        case provider
        case model
        case tokensUsed = "tokens_used"
        case createdAt = "created_at"
        case completedAt = "completed_at"
        case errorDetail = "error_detail"
    }
}

public struct BackendConversation: Codable, Equatable, Sendable, Identifiable {
    public let conversationID: UUID
    public let title: String
    public let status: String
    public let createdAt: Int
    public let updatedAt: Int
    public let revision: Int
    public let messages: [BackendConversationMessage]

    public var id: UUID { conversationID }

    enum CodingKeys: String, CodingKey {
        case conversationID = "conversation_id"
        case title
        case status
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case revision
        case messages
    }
}

public struct BackendConversationList: Codable, Equatable, Sendable {
    public let conversations: [BackendConversation]
    public let streamSupported: Bool

    enum CodingKeys: String, CodingKey {
        case conversations
        case streamSupported = "stream_supported"
    }
}

public struct BackendConversationRun: Codable, Equatable, Sendable {
    public let runID: UUID
    public let conversationID: UUID
    public let messageID: UUID
    public let status: String
    public let provider: String?
    public let model: String?
    public let parentRunID: UUID?
    public let createdAt: Int
    public let startedAt: Int?
    public let terminalAt: Int?
    public let errorDetail: String

    public var isTerminal: Bool {
        ["completed", "failed", "cancelled", "interrupted"].contains(status)
    }

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case conversationID = "conversation_id"
        case messageID = "message_id"
        case status
        case provider
        case model
        case parentRunID = "parent_run_id"
        case createdAt = "created_at"
        case startedAt = "started_at"
        case terminalAt = "terminal_at"
        case errorDetail = "error_detail"
    }
}

/// A persisted conversation event received over the authenticated SSE
/// subscription. At-least-once delivery means clients deduplicate by `id`.
public struct PersistedConversationEvent: Equatable, Sendable {
    public let id: Int
    public let schemaVersion: Int
    public let event: String
    public let organizationID: UUID?
    public let workspaceID: UUID?
    public let userID: UUID?
    public let conversationID: UUID?
    public let runID: UUID?
    public let timestamp: Int
    public let trace: String
    public let payload: [String: Sendable]

    public static func == (lhs: PersistedConversationEvent, rhs: PersistedConversationEvent) -> Bool {
        lhs.id == rhs.id && lhs.event == rhs.event
    }
}

/// Events delivered by the authenticated conversation-event subscription.
public enum ConversationSubscriptionEvent: Equatable, Sendable {
    case subscribed
    case event(PersistedConversationEvent)
    case revoked
    case done
}

// MARK: - Server-sent conversation events

public enum ConversationEvent: Equatable, Sendable {
    case opened
    case delta(runID: UUID, content: String)
    case completed(runID: UUID, messageID: UUID?, content: String)
    case cancelled(runID: UUID)
    case failed(runID: UUID, reason: String)
    case error(code: String, message: String)
    case done
}

public enum ConversationClientError: Error, Equatable, LocalizedError, Sendable {
    case invalidEndpoint
    case invalidEvent
    case invalidSession
    case decodingFailed

    public var errorDescription: String? {
        switch self {
        case .invalidEndpoint:
            "The selected connection is not valid."
        case .invalidEvent:
            "The conversation stream contained an invalid event."
        case .invalidSession:
            "The application session is invalid or revoked."
        case .decodingFailed:
            "The conversation payload could not be decoded."
        }
    }
}

// MARK: - Conversation client

/// Native Swift canonical-conversation integration (Phase P3A).
///
/// The backend is authoritative for conversation history and message state. The
/// client presents its application session on every request; streaming is
/// genuine only when the selected provider truly streams, otherwise a single
/// completed delta is delivered with honest non-streaming semantics.
public struct ConversationClient: Sendable {
    private static let sessionHeader = "X-JoeOS-Session"

    private let backend: JoeOSBackendClient
    private let streaming: any BackendHTTPStreaming
    private let endpoint: ValidatedEndpoint

    public init(
        backend: JoeOSBackendClient,
        endpoint: ValidatedEndpoint,
        streaming: any BackendHTTPStreaming = URLSessionBackendTransport()
    ) {
        self.backend = backend
        self.endpoint = endpoint
        self.streaming = streaming
    }

    public func create(title: String, sessionID: UUID) async throws -> BackendConversation {
        struct Body: Encodable, Sendable {
            let title: String
        }
        return try await backend.post(
            body: Body(title: title),
            to: "/api/v1/conversations",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    public func list(sessionID: UUID) async throws -> BackendConversationList {
        try await backend.get(
            BackendConversationList.self,
            path: "/api/v1/conversations",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    /// Reopens a canonical conversation by its stable server-assigned id.
    public func conversation(_ id: UUID, sessionID: UUID) async throws -> BackendConversation {
        try await backend.get(
            BackendConversation.self,
            path: "/api/v1/conversations/\(id.uuidString.lowercased())",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    public func submit(_ content: String, in conversationID: UUID, sessionID: UUID, idempotencyKey: UUID? = nil) async throws -> BackendConversation {
        struct Body: Encodable, Sendable {
            let content: String
            let idempotencyKey: String?
            enum CodingKeys: String, CodingKey {
                case content
                case idempotencyKey = "idempotency_key"
            }
        }
        return try await backend.post(
            body: Body(content: content, idempotencyKey: idempotencyKey?.uuidString.lowercased()),
            to: "/api/v1/conversations/\(conversationID.uuidString.lowercased())/messages",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    /// Retries the last user message, optionally related to a prior run.
    public func retry(in conversationID: UUID, sessionID: UUID, parentRunID: UUID? = nil) async throws -> BackendConversation {
        struct Body: Encodable, Sendable {
            let parentRunID: String?
            enum CodingKeys: String, CodingKey {
                case parentRunID = "parent_run_id"
            }
        }
        return try await backend.post(
            body: Body(parentRunID: parentRunID?.uuidString.lowercased()),
            to: "/api/v1/conversations/\(conversationID.uuidString.lowercased())/retry",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    public func rename(_ title: String, conversationID: UUID, sessionID: UUID) async throws -> BackendConversation {
        struct Body: Encodable, Sendable {
            let title: String
        }
        return try await backend.patch(
            body: Body(title: title),
            to: "/api/v1/conversations/\(conversationID.uuidString.lowercased())",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    public func archive(conversationID: UUID, sessionID: UUID) async throws -> BackendConversation {
        struct Empty: Encodable, Sendable {}
        return try await backend.post(
            body: Empty(),
            to: "/api/v1/conversations/\(conversationID.uuidString.lowercased())/archive",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    public func run(_ runID: UUID, in conversationID: UUID, sessionID: UUID) async throws -> BackendConversationRun {
        try await backend.get(
            BackendConversationRun.self,
            path: "/api/v1/conversations/\(conversationID.uuidString.lowercased())/runs/\(runID.uuidString.lowercased())",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    /// Cancels an in-flight generation server-side.
    public func cancel(runID: UUID, in conversationID: UUID, sessionID: UUID) async throws {
        struct Empty: Encodable, Sendable {}
        _ = try await backend.post(
            body: Empty(),
            to: "/api/v1/conversations/\(conversationID.uuidString.lowercased())/runs/\(runID.uuidString.lowercased())/cancel",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        ) as EmptyResponse
    }

    /// Streams one message and its response as server-sent events. Partial
    /// deltas arrive only when the provider genuinely streams.
    public func stream(
        _ content: String,
        in conversationID: UUID,
        sessionID: UUID,
        idempotencyKey: UUID? = nil
    ) -> AsyncThrowingStream<ConversationEvent, Error> {
        AsyncThrowingStream { continuation in
            do {
                let url = try backend.url(
                    endpoint: endpoint,
                    path: "/api/v1/conversations/\(conversationID.uuidString.lowercased())/stream"
                )
                var request = URLRequest(
                    url: url,
                    cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
                    timeoutInterval: 180
                )
                request.httpMethod = "POST"
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.setValue("application/json", forHTTPHeaderField: "Accept")
                var body: [String: String] = ["content": content]
                if let idempotencyKey {
                    body["idempotency_key"] = idempotencyKey.uuidString.lowercased()
                }
                request.httpBody = try JSONEncoder().encode(body)
                request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
                for (name, value) in Self.headers(sessionID) {
                    request.setValue(value, forHTTPHeaderField: name)
                }
                let stream = try streaming.stream(request: request)
                let task = Task {
                    do {
                        for try await chunk in stream {
                            for event in try Self.parseSSE(chunk) {
                                continuation.yield(event)
                            }
                        }
                        continuation.finish()
                    } catch {
                        continuation.finish(throwing: error)
                    }
                }
                continuation.onTermination = { _ in task.cancel() }
            } catch {
                continuation.finish(throwing: error)
            }
        }
    }

    /// Subscribes to the authenticated conversation event stream, resuming from
    /// the given cursor. Delivery is at-least-once; clients deduplicate by the
    /// stable event id. The session id is carried in a header, never a query
    /// string. The stream stops when the session is revoked or disabled.
    public func subscribeToConversationEvents(
        from cursor: Int,
        conversationID: UUID? = nil,
        sessionID: UUID,
        backoff: (Int) -> TimeInterval = { _ in 0.5 }
    ) -> AsyncThrowingStream<ConversationSubscriptionEvent, Error> {
        AsyncThrowingStream { continuation in
            do {
                var path = "/api/v1/conversations/events?cursor=\(max(0, cursor))"
                if let conversationID {
                    path += "&conversation_id=\(conversationID.uuidString.lowercased())"
                }
                let url = try backend.url(endpoint: endpoint, path: path)
                var request = URLRequest(
                    url: url,
                    cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
                    timeoutInterval: 300
                )
                request.httpMethod = "GET"
                request.setValue("application/json", forHTTPHeaderField: "Accept")
                for (name, value) in Self.headers(sessionID) {
                    request.setValue(value, forHTTPHeaderField: name)
                }
                let stream = try streaming.stream(request: request)
                let task = Task {
                    do {
                        for try await chunk in stream {
                            let events = try Self.parsePersistedEvents(chunk)
                            for event in events {
                                continuation.yield(event)
                            }
                        }
                        continuation.finish()
                    } catch {
                        continuation.finish(throwing: error)
                    }
                }
                continuation.onTermination = { _ in task.cancel() }
            } catch {
                continuation.finish(throwing: error)
            }
        }
    }

    /// Parses persisted conversation-event SSE frames into typed events.
    static func parsePersistedEvents(_ data: Data) throws -> [ConversationSubscriptionEvent] {
        guard let text = String(data: data, encoding: .utf8) else {
            throw ConversationClientError.invalidEvent
        }
        var events: [ConversationSubscriptionEvent] = []
        for block in text.components(separatedBy: "\n\n") {
            var eventName = "message"
            var dataLines: [String] = []
            for line in block.components(separatedBy: "\n") {
                if line.hasPrefix("event:") {
                    eventName = String(line.dropFirst("event:".count)).trimmingCharacters(in: .whitespaces)
                } else if line.hasPrefix("data:") {
                    dataLines.append(String(line.dropFirst("data:".count)).trimmingCharacters(in: .whitespaces))
                }
            }
            guard let dataText = dataLines.first, !dataText.isEmpty else { continue }
            if eventName == "subscribed" {
                events.append(.subscribed)
                continue
            }
            if eventName == "revoked" {
                events.append(.revoked)
                continue
            }
            if eventName == "done" {
                events.append(.done)
                continue
            }
            guard let object = try? JSONSerialization.jsonObject(with: Data(dataText.utf8)) as? [String: Any] else {
                throw ConversationClientError.invalidEvent
            }
            let envelope = try Self.decodeEnvelope(object)
            events.append(.event(envelope))
        }
        return events
    }

    private struct EnvelopeDocument: Decodable, Sendable {
        let schemaVersion: Int?
        let event: String?
        let org: String?
        let ws: String?
        let user: String?
        let conversation: String?
        let run: String?
        let ts: Int?
        let trace: String?

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case event
            case org
            case ws
            case user
            case conversation
            case run
            case ts
            case trace
        }
    }

    private static func decodeEnvelope(_ object: [String: Any]) throws -> PersistedConversationEvent {
        let data = try JSONSerialization.data(withJSONObject: object)
        let doc = try JSONDecoder().decode(EnvelopeDocument.self, from: data)
        let id = object["id"] as? Int ?? 0
        return PersistedConversationEvent(
            id: id,
            schemaVersion: doc.schemaVersion ?? 1,
            event: doc.event ?? "unknown",
            organizationID: doc.org.flatMap(UUID.init(uuidString:)),
            workspaceID: doc.ws.flatMap(UUID.init(uuidString:)),
            userID: doc.user.flatMap(UUID.init(uuidString:)),
            conversationID: doc.conversation.flatMap(UUID.init(uuidString:)),
            runID: doc.run.flatMap(UUID.init(uuidString:)),
            timestamp: doc.ts ?? 0,
            trace: doc.trace ?? "",
            payload: sendableDictionary(object)
        )
    }

    /// Converts the raw JSON document to a `Sendable` dictionary. Every value
    /// produced by `JSONSerialization` is JSON-safe, so this mapping never
    /// loses data and never forwards an arbitrary non-`Sendable` value.
    private static func sendableDictionary(_ object: [String: Any]) -> [String: Sendable] {
        var result: [String: Sendable] = [:]
        for (key, value) in object {
            result[key] = sendableValue(value)
        }
        return result
    }

    private static func sendableValue(_ value: Any) -> Sendable {
        switch value {
        case let string as String: return string
        case let bool as Bool: return bool
        case let number as NSNumber: return number
        case let array as [Any]: return array.map { sendableValue($0) }
        case let dictionary as [String: Any]: return sendableDictionary(dictionary)
        case is NSNull: return "null"
        default: return String(describing: value)
        }
    }

    public static func headers(_ sessionID: UUID) -> [String: String] {
        [sessionHeader: sessionID.uuidString.lowercased()]
    }

    // MARK: - SSE parsing

    private struct SSEPayload: Decodable, Sendable {
        let event: String?
        let runID: UUID?
        let messageID: UUID?
        let content: String?
        let reason: String?
        let code: String?
        let message: String?
        let cancelled: Bool?

        enum CodingKeys: String, CodingKey {
            case event
            case runID = "run_id"
            case messageID = "message_id"
            case content
            case reason
            case code
            case message
            case cancelled
        }
    }

    static func parseSSE(_ data: Data) throws -> [ConversationEvent] {
        guard let text = String(data: data, encoding: .utf8) else {
            throw ConversationClientError.invalidEvent
        }
        var events: [ConversationEvent] = []
        for block in text.components(separatedBy: "\n\n") {
            var eventName = "message"
            var dataLines: [String] = []
            for line in block.components(separatedBy: "\n") {
                if line.hasPrefix("event:") {
                    eventName = String(line.dropFirst("event:".count)).trimmingCharacters(in: .whitespaces)
                } else if line.hasPrefix("data:") {
                    dataLines.append(String(line.dropFirst("data:".count)).trimmingCharacters(in: .whitespaces))
                }
            }
            guard let dataText = dataLines.first, !dataText.isEmpty else { continue }
            let payload: SSEPayload
            do {
                payload = try JSONDecoder().decode(SSEPayload.self, from: Data(dataText.utf8))
            } catch {
                throw ConversationClientError.invalidEvent
            }
            switch eventName {
            case "conversation.opened":
                events.append(.opened)
            case "message.delta":
                if let runID = payload.runID, let content = payload.content {
                    events.append(.delta(runID: runID, content: content))
                }
            case "run.completed":
                if let runID = payload.runID {
                    events.append(.completed(runID: runID, messageID: payload.messageID, content: payload.content ?? ""))
                }
            case "run.cancelled":
                if let runID = payload.runID {
                    events.append(.cancelled(runID: runID))
                }
            case "run.failed":
                if let runID = payload.runID {
                    events.append(.failed(runID: runID, reason: payload.reason ?? ""))
                }
            case "error":
                events.append(.error(code: payload.code ?? "unknown", message: payload.message ?? ""))
            case "done":
                events.append(.done)
            default:
                break
            }
        }
        return events
    }
}

private struct EmptyResponse: Decodable, Sendable {}
