import Foundation

/// The Provider Registry. It is data-driven: providers are registered from the
/// JoeOS backend's authoritative `/api/v1/ai/providers` contract (or explicit
/// local configuration) and never require UI changes to appear.
public struct ProviderRegistry: Sendable {
    public private(set) var records: [ProviderRecord] = []

    public init(records: [ProviderRecord] = []) {
        self.records = records
    }

    public mutating func register(_ record: ProviderRecord) {
        if let index = records.firstIndex(where: { $0.providerID == record.providerID }) {
            records[index] = record
        } else {
            records.append(record)
        }
    }

    public mutating func replaceAll(_ records: [ProviderRecord]) {
        self.records = records
    }

    public func provider(id: String) -> ProviderRecord? {
        records.first(where: { $0.providerID == id })
    }

    public func availableProviders(localOnly: Bool) -> [ProviderRecord] {
        records.filter { record in
            guard record.available else { return false }
            if localOnly { return record.kind == .local }
            return true
        }
    }

    /// Whether a cloud provider may be routed to. Cloud routing is never
    /// silent: it requires an explicit cloud-approved provider and local-only
    /// mode must be off.
    public func allowsRouting(to record: ProviderRecord, localOnly: Bool) -> Bool {
        guard record.available else { return false }
        if localOnly { return record.kind == .local }
        if record.kind == .cloud { return record.cloudApproved }
        return true
    }
}

/// The Model Registry: capability metadata per model, reported honestly.
public struct ModelRegistry: Sendable {
    public private(set) var models: [ModelRecord] = []

    public init(models: [ModelRecord] = []) {
        self.models = models
    }

    public mutating func register(_ record: ModelRecord) {
        if let index = models.firstIndex(where: { $0.id == record.id }) {
            models[index] = record
        } else {
            models.append(record)
        }
    }

    public mutating func replaceAll(_ models: [ModelRecord]) {
        self.models = models
    }

    public func models(forProvider providerID: String) -> [ModelRecord] {
        models.filter { $0.provider == providerID }
    }

    public func availableModels(localOnly: Bool) -> [ModelRecord] {
        models.filter { $0.availability && (!localOnly || $0.offlineSupported) }
    }

    /// The best available model for a capability, ranked by cost then latency.
    /// `from` restricts candidates to models served by the given providers.
    public func best(
        for capability: ModelCapability,
        from providerIDs: Set<String>? = nil,
        localOnly: Bool,
        requireStreaming: Bool = false
    ) -> ModelRecord? {
        availableModels(localOnly: localOnly)
            .filter { providerIDs == nil || providerIDs!.contains($0.provider) }
            .filter { $0.canHandle(capability) }
            .filter { !requireStreaming || $0.streamingSupported }
            .sorted {
                if $0.estimatedCostPer1KTokens != $1.estimatedCostPer1KTokens {
                    return $0.estimatedCostPer1KTokens < $1.estimatedCostPer1KTokens
                }
                return $0.averageLatencyMs < $1.averageLatencyMs
            }
            .first
    }
}
