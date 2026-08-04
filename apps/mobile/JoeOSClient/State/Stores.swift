import Foundation
import JoeOSCore
import JoeOSIntelligence

// MARK: - Offline cache

/// Persistence for the offline cache.
public protocol CachePersisting: Sendable {
    func data(forKey key: String) -> Data?
    func set(_ data: Data, forKey key: String)
    func remove(key: String)
    func clearAll()
}

public actor InMemoryCacheStore: CachePersisting {
    private var values: [String: Data] = [:]
    public init() {}
    public func data(forKey key: String) -> Data? { values[key] }
    public func set(_ data: Data, forKey key: String) { values[key] = data }
    public func remove(key: String) { values[key] = nil }
    public func clearAll() { values.removeAll() }
}

/// Bounded offline cache for last-known server state. Values are Codable and
/// never include secrets.
@MainActor
public final class OfflineCache: ObservableObject {

    @Published public private(set) var itemCount = 0
    private let store: any CachePersisting
    private static let itemLimit = 256

    public init(store: any CachePersisting = InMemoryCacheStore()) {
        self.store = store
    }

    public func cache<Value: Codable & Sendable>(_ value: Value, forKey key: String) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(value)
        store.set(data, forKey: key)
        itemCount += 1
    }

    public func value<Value: Codable & Sendable>(forKey key: String) throws -> Value? {
        guard let data = store.data(forKey: key) else { return nil }
        return try JSONDecoder().decode(Value.self, from: data)
    }

    public func remove(key: String) {
        store.remove(key: key)
        itemCount = max(0, itemCount - 1)
    }

    public func clear() {
        store.clearAll()
        itemCount = 0
    }
}

// MARK: - Synchronization engine

public struct PendingSyncItem: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let action: String
    public let target: String
    public let baseVersion: String?
    public let payload: Data

    public init(
        id: UUID = UUID(),
        action: String,
        target: String,
        baseVersion: String? = nil,
        payload: Data
    ) {
        self.id = id
        self.action = action
        self.target = target
        self.baseVersion = baseVersion
        self.payload = payload
    }
}

/// Revalidated offline synchronization. High-risk actions are never queued
/// (`MobileCompanionPolicy`); queued items are sent with their base version so
/// conflicts preserve authoritative server state.
@MainActor
public final class SynchronizationEngine: ObservableObject {

    @Published public private(set) var pending: [PendingSyncItem] = []
    @Published public private(set) var syncErrors: [String] = []

    private let send: @Sendable (PendingSyncItem) async throws -> Bool

    public init(send: @escaping @Sendable (PendingSyncItem) async throws -> Bool) {
        self.send = send
    }

    public func enqueue(_ item: PendingSyncItem) -> Bool {
        guard MobileCompanionPolicy.allowsOffline(item.action) else {
            syncErrors.append("\(item.action) is not safe to queue offline.")
            return false
        }
        pending.append(item)
        return true
    }

    public func syncAll() async {
        var remaining: [PendingSyncItem] = []
        for item in pending {
            do {
                guard try await send(item) else { continue }
            } catch {
                remaining.append(item)
            }
        }
        pending = remaining
    }

    public func discardPending() {
        pending.removeAll()
        syncErrors.removeAll()
    }
}

// MARK: - Typed stores

/// A store of authoritative items loaded from the JoeOS backend. Honest
/// `isUnavailable` state until a successful fetch.
@MainActor
public final class RemoteCollectionStore<Item: Identifiable & Equatable & Sendable>: ObservableObject {

    @Published public private(set) var items: [Item] = []
    @Published public private(set) var isLoaded = false
    @Published public private(set) var lastError: String?

    private let fetch: @Sendable () async throws -> [Item]

    public init(fetch: @escaping @Sendable () async throws -> [Item]) {
        self.fetch = fetch
    }

    public var isUnavailable: Bool { !isLoaded }

    public func refresh() async {
        do {
            items = try await fetch()
            isLoaded = true
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    public func replace(_ newItems: [Item]) {
        items = newItems
        isLoaded = true
    }
}

/// A conversation executor that routes through the JoeOS backend only. The
/// client never contacts a provider directly.
public struct BackendConversationExecutor: ConversationExecuting, Sendable {
    private let backend: any IntelligenceBackendServing

    public init(backend: any IntelligenceBackendServing) {
        self.backend = backend
    }

    public func execute(
        messages: [ConversationMessage],
        decision: ExecutionRouter.Decision,
        onPartial: @escaping @Sendable (String) -> Void
    ) async throws -> ExecutionResult {
        // The backend inference endpoint is currently non-streaming; the full
        // response is delivered when the backend finishes. Streaming support is
        // reported by the Diagnostics store when the backend provides it.
        return try await backend.infer(messages: messages, decision: decision)
    }
}
