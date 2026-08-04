import Foundation

/// Layered memory: conversation, project, agent, user, long-term, working, and
/// artifact references. The API hides the layering from the UI.
public enum MemoryLayer: String, Codable, CaseIterable, Sendable {
    case conversation
    case project
    case agent
    case user
    case longTerm = "long_term"
    case working
    case artifactReferences = "artifact_references"
}

public struct MemoryEntry: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var layer: MemoryLayer
    public var key: String
    public var value: String
    public var createdAt: Date
    public var expiresAt: Date?
    public var sourceRef: String?

    public init(
        id: UUID = UUID(),
        layer: MemoryLayer,
        key: String,
        value: String,
        createdAt: Date = Date(),
        expiresAt: Date? = nil,
        sourceRef: String? = nil
    ) {
        self.id = id
        self.layer = layer
        self.key = key
        self.value = value
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.sourceRef = sourceRef
    }

    public var isExpired(now: Date = Date()) -> Bool {
        guard let expiresAt else { return false }
        return expiresAt <= now
    }
}

public protocol MemoryPersisting: Sendable {
    func loadEntries() async -> [MemoryEntry]
    func save(_ entry: MemoryEntry) async throws
    func delete(id: UUID) async throws
}

public actor InMemoryMemoryStore: MemoryPersisting {
    private var entries: [UUID: MemoryEntry] = [:]
    public init() {}
    public func loadEntries() async -> [MemoryEntry] {
        Array(entries.values)
    }
    public func save(_ entry: MemoryEntry) async throws {
        entries[entry.id] = entry
    }
    public func delete(id: UUID) async throws {
        entries[id] = nil
    }
}

/// Layered memory with recall and expiration. Working memory is bounded; long
/// memory persists. Implementation layering is not exposed to callers.
@MainActor
public final class MemoryStore: ObservableObject {

    @Published public private(set) var entries: [MemoryEntry] = []
    private static let workingMemoryLimit = 64

    private let store: any MemoryPersisting

    public init(store: any MemoryPersisting = InMemoryMemoryStore()) {
        self.store = store
    }

    public func load() async {
        entries = await store.loadEntries()
    }

    /// Remembers a fact. Working memory is evicted when it exceeds its bound.
    @discardableResult
    public func remember(
        _ value: String,
        key: String,
        layer: MemoryLayer,
        expiresAt: Date? = nil,
        sourceRef: String? = nil
    ) -> MemoryEntry {
        let entry = MemoryEntry(
            layer: layer,
            key: key,
            value: value,
            expiresAt: expiresAt,
            sourceRef: sourceRef
        )
        entries.insert(entry, at: 0)
        if layer == .working {
            trimWorkingMemory()
        }
        persist(entry)
        return entry
    }

    /// Recalls the most recent non-expired entry for a key.
    public func recall(key: String, layer: MemoryLayer? = nil) -> String? {
        entries
            .filter { $0.key == key && !$0.isExpired() }
            .filter { layer == nil || $0.layer == layer }
            .sorted { $0.createdAt > $1.createdAt }
            .first?.value
    }

    /// Full-text search across non-expired entries.
    public func search(_ text: String, layers: Set<MemoryLayer>? = nil) -> [MemoryEntry] {
        let query = text.lowercased()
        return entries
            .filter { !$0.isExpired() }
            .filter { layers == nil || layers!.contains($0.layer) }
            .filter { $0.key.lowercased().contains(query) || $0.value.lowercased().contains(query) }
            .sorted { $0.createdAt > $1.createdAt }
    }

    /// The compact working context (conversation + working memory) for a prompt.
    public func workingContext() -> String {
        let working = entries
            .filter { $0.layer == .working || $0.layer == .conversation }
            .filter { !$0.isExpired() }
            .sorted { $0.createdAt > $1.createdAt }
            .prefix(workingMemoryLimit)
            .map { $0.value }
        return working.joined(separator: "\n")
    }

    /// Expires due entries and reports how many were removed.
    @discardableResult
    public func expire(now: Date = Date()) -> Int {
        let expired = entries.filter { $0.isExpired(now: now) }
        entries.removeAll { $0.isExpired(now: now) }
        for entry in expired {
            Task { [store] in try? await store.delete(id: entry.id) }
        }
        return expired.count
    }

    private func trimWorkingMemory() {
        let working = entries.filter { $0.layer == .working }
        guard working.count > Self.workingMemoryLimit else { return }
        let overflow = working.sorted { $0.createdAt > $1.createdAt }
            .dropFirst(Self.workingMemoryLimit)
        let overflowIDs = Set(overflow.map(\.id))
        entries.removeAll { overflowIDs.contains($0.id) }
    }

    private func persist(_ entry: MemoryEntry) {
        Task { [store] in
            try? await store.save(entry)
        }
    }
}
