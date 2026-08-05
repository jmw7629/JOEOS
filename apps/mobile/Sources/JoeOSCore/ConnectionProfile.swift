import Foundation

/// Transport scheme for a JoeOS connection profile.
///
/// The client always builds requests from `(transport, host, port)`. Moving a
/// backend from HTTP to HTTPS is a profile edit — no source changes.
public enum ConnectionProtocol: String, Codable, CaseIterable, Sendable {
    case http
    case https

    public var defaultPort: Int {
        switch self {
        case .http: 80
        case .https: 443
        }
    }

    public var displayName: String {
        rawValue.uppercased()
    }
}

/// Whether a profile targets a development or production host.
public enum ConnectionEnvironment: String, Codable, CaseIterable, Sendable {
    case development
    case production
}

/// The authentication requirement a profile expects from the JoeOS backend.
public enum ProfileAuthenticationMode: String, Codable, CaseIterable, Sendable {
    case none
    case deviceEnrollment = "device_enrollment"
    case application

    public var displayName: String {
        switch self {
        case .none: "None"
        case .deviceEnrollment: "Device enrollment"
        case .application: "Application sign-in"
        }
    }
}

public enum ConnectionProfileError: Error, Equatable, LocalizedError, Sendable {
    case encodingFailed
    case malformedStoredProfile
    case legacyEndpointUnparseable

    public var errorDescription: String? {
        switch self {
        case .encodingFailed:
            "The connection profile could not be encoded."
        case .malformedStoredProfile:
            "A stored connection profile was malformed."
        case .legacyEndpointUnparseable:
            "A legacy connection profile could not be migrated."
        }
    }
}

/// A saved JoeOS connection profile.
///
/// A profile is non-secret preference data: it never contains API keys,
/// provider credentials, passwords, or session tokens.
public struct ConnectionProfile: Identifiable, Equatable, Sendable {
    public let id: UUID
    public var displayName: String
    /// The transport ("Protocol" in the profile).
    public var transport: ConnectionProtocol
    public var host: String
    /// `nil` means "discover / use the transport default port".
    public var port: Int?
    /// True when the backend requires HTTPS ("HTTPS Required").
    public var httpsRequired: Bool {
        get { transport == .https }
        set { transport = newValue ? .https : .http }
    }
    public var environment: ConnectionEnvironment
    public var notes: String
    /// Optional advertised API version, for example "v1".
    public var apiVersion: String?
    public var requiresAuthentication: Bool
    public var authenticationMode: ProfileAuthenticationMode
    public var lastConnectedAt: Date?
    public var lastSuccessfulAt: Date?

    public init(
        id: UUID = UUID(),
        displayName: String,
        transport: ConnectionProtocol,
        host: String,
        port: Int?,
        environment: ConnectionEnvironment,
        notes: String = "",
        apiVersion: String? = nil,
        requiresAuthentication: Bool = true,
        authenticationMode: ProfileAuthenticationMode = .deviceEnrollment,
        lastConnectedAt: Date? = nil,
        lastSuccessfulAt: Date? = nil
    ) {
        self.id = id
        self.displayName = displayName
        self.transport = transport
        self.host = host
        self.port = port
        self.environment = environment
        self.notes = notes
        self.apiVersion = apiVersion
        self.requiresAuthentication = requiresAuthentication
        self.authenticationMode = authenticationMode
        self.lastConnectedAt = lastConnectedAt
        self.lastSuccessfulAt = lastSuccessfulAt
    }

    /// Creates a profile from a full endpoint URL string (legacy shape and
    /// migration path). Used by the legacy `init(name:endpoint:)` form.
    public init(id: UUID = UUID(), name: String, endpoint: String) throws {
        let validated = try EndpointPolicy.validate(endpoint).get()
        let isHTTPS = validated.origin.scheme == "https"
        self.init(
            id: id,
            displayName: name,
            transport: isHTTPS ? .https : .http,
            host: validated.origin.host,
            port: validated.origin.port,
            environment: .development,
            notes: "Migrated from the endpoint-based profile format.",
            apiVersion: nil,
            requiresAuthentication: true,
            authenticationMode: .deviceEnrollment
        )
    }

    /// The canonical profile address used by older views and tests.
    public var endpoint: String {
        var address = "\(transport.rawValue)://\(origin.renderedHost)"
        if let port {
            address += ":\(port)"
        }
        return address
    }

    /// The effective origin of this profile before validation.
    public var origin: EndpointOrigin {
        EndpointOrigin(scheme: transport.rawValue, host: host, port: port)
    }

    /// The validated endpoint for this profile, if the host passes policy.
    public var validatedEndpoint: Result<ValidatedEndpoint, EndpointValidationError> {
        EndpointPolicy.validate(endpoint)
    }

    /// The URL to load when this profile is active (after validation).
    public var url: URL? {
        try? validatedEndpoint.get().url
    }

    /// Whether the host may currently be reached over the profile's transport.
    public var isReachablePerPolicy: Bool {
        switch validatedEndpoint {
        case .success:
            true
        case .failure:
            false
        }
    }

    /// The current authoritative development host.
    public static let defaultVPSID = UUID(uuidString: "6BA7B810-9DAD-4D5A-8000-00000000A001")!

    /// Authoritative development profile: the Tailscale VPS. HTTP during
    /// development; the port is discovered from the backend rather than
    /// hard-coded, and HTTPS is enabled by flipping the profile's transport.
    public static let defaultVPS = ConnectionProfile(
        id: defaultVPSID,
        displayName: "JoeOS VPS",
        transport: .http,
        host: "100.98.25.26",
        port: nil,
        environment: .development,
        notes: "Authoritative development host (Tailscale 100.98.25.26). Port is discovered from the backend; switch this profile to HTTPS when the backend serves TLS.",
        apiVersion: nil,
        requiresAuthentication: true,
        authenticationMode: .deviceEnrollment
    )

    /// Returns a copy with the given last-connected timestamp.
    public func connected(at date: Date = Date()) -> ConnectionProfile {
        var copy = self
        copy.lastConnectedAt = date
        return copy
    }

    /// Returns a copy with the given last-successful timestamp.
    public func succeeded(at date: Date = Date()) -> ConnectionProfile {
        var copy = self
        copy.lastSuccessfulAt = date
        return copy
    }
}

// MARK: - Persistence

/// Abstraction over profile preference storage so the manager is testable
/// without touching `UserDefaults`.
public protocol ProfilePersisting: Sendable {
    func string(forKey key: String) -> String?
    func set(_ value: String, forKey key: String)
}

/// The default `ProfilePersisting` backed by standard `UserDefaults`.
public struct UserDefaultsProfilePersisting: ProfilePersisting {
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public func string(forKey key: String) -> String? {
        defaults.string(forKey: key)
    }

    public func set(_ value: String, forKey key: String) {
        defaults.set(value, forKey: key)
    }
}

public enum ConnectionProfileStorage {

    public static let profilesKey = "joeos.connection.profiles.v2"
    public static let activeProfileKey = "joeos.connection.active-profile.v2"
    public static let lastSuccessfulProfileKey = "joeos.connection.last-successful.v2"

    public static var defaultPayload: String {
        (try? encode([.defaultVPS])) ?? "[]"
    }

    /// Encodes a profile list to a stable JSON string.
    public static func encode(_ profiles: [ConnectionProfile]) throws -> String {
        let documents = try profiles.map { try ProfileDocument(profile: $0) }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(documents)
        guard let string = String(data: data, encoding: .utf8) else {
            throw ConnectionProfileError.encodingFailed
        }
        return string
    }

    /// Decodes a profile list. Malformed, empty, or legacy-v1 payloads fall
    /// back to the authoritative default (JoeOS VPS).
    public static func decode(_ rawValue: String) -> [ConnectionProfile] {
        guard let data = rawValue.data(using: .utf8) else { return [.defaultVPS] }
        guard let documents = try? JSONDecoder().decode([ProfileDocument].self, from: data) else {
            return [.defaultVPS]
        }
        let profiles = documents.compactMap { try? $0.makeProfile() }
        return profiles.isEmpty ? [.defaultVPS] : profiles
    }

    /// Migration entry point: rewrites a stored payload to the current schema.
    public static func migratedPayload(_ rawValue: String) -> String? {
        let decoded = decode(rawValue)
        guard let encoded = try? encode(decoded) else { return nil }
        return encoded == rawValue ? nil : encoded
    }

    public static func hasPayload(_ rawValue: String) -> Bool {
        guard let data = rawValue.data(using: .utf8) else { return false }
        return (try? JSONDecoder().decode([ProfileDocument].self, from: data)) != nil
    }
}

// MARK: - Document codec

private enum ProfileDocumentCodingKey: String, CodingKey, CaseIterable {
    case schemaVersion = "schema_version"
    case id
    case displayName = "display_name"
    case name
    case transport = "protocol"
    case host
    case port
    case httpsRequired = "https_required"
    case environment
    case notes
    case apiVersion = "api_version"
    case requiresAuthentication = "requires_authentication"
    case authenticationMode = "authentication_mode"
    case lastConnectedAt = "last_connected_at"
    case lastSuccessfulAt = "last_successful_at"
    case endpoint
}

private struct ProfileDocument: Codable {
    static let currentSchemaVersion = 2

    let schemaVersion: Int
    let id: String
    let displayName: String?
    let transport: String?
    let host: String?
    let port: Int?
    let httpsRequired: Bool?
    let environment: String?
    let notes: String?
    let apiVersion: String?
    let requiresAuthentication: Bool?
    let authenticationMode: String?
    let lastConnectedAt: Int64?
    let lastSuccessfulAt: Int64?
    let legacyEndpoint: String?

    init(profile: ConnectionProfile) throws {
        schemaVersion = Self.currentSchemaVersion
        id = profile.id.uuidString.lowercased()
        displayName = profile.displayName
        transport = profile.transport.rawValue
        host = profile.host
        port = profile.port
        httpsRequired = profile.httpsRequired
        environment = profile.environment.rawValue
        notes = profile.notes
        apiVersion = profile.apiVersion
        requiresAuthentication = profile.requiresAuthentication
        authenticationMode = profile.authenticationMode.rawValue
        lastConnectedAt = profile.lastConnectedAt.map { Self.epoch($0) }
        lastSuccessfulAt = profile.lastSuccessfulAt.map { Self.epoch($0) }
        legacyEndpoint = nil
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: ProfileDocumentCodingKey.self)
        schemaVersion = (try? container.decodeIfPresent(Int.self, forKey: .schemaVersion)) ?? 1
        id = (try? container.decodeIfPresent(String.self, forKey: .id)) ?? UUID().uuidString
        if schemaVersion >= 2 {
            displayName = try container.decodeIfPresent(String.self, forKey: .displayName)
            transport = try container.decodeIfPresent(String.self, forKey: .transport)
            host = try container.decodeIfPresent(String.self, forKey: .host)
            port = try container.decodeIfPresent(Int.self, forKey: .port)
            httpsRequired = try container.decodeIfPresent(Bool.self, forKey: .httpsRequired)
            environment = try container.decodeIfPresent(String.self, forKey: .environment)
            notes = try container.decodeIfPresent(String.self, forKey: .notes)
            apiVersion = try container.decodeIfPresent(String.self, forKey: .apiVersion)
            requiresAuthentication = try container.decodeIfPresent(Bool.self, forKey: .requiresAuthentication)
            authenticationMode = try container.decodeIfPresent(String.self, forKey: .authenticationMode)
            lastConnectedAt = try container.decodeIfPresent(Int64.self, forKey: .lastConnectedAt)
            lastSuccessfulAt = try container.decodeIfPresent(Int64.self, forKey: .lastSuccessfulAt)
            legacyEndpoint = nil
        } else {
            displayName = try container.decodeIfPresent(String.self, forKey: .name)
            legacyEndpoint = try container.decodeIfPresent(String.self, forKey: .endpoint)
            transport = nil
            host = nil
            port = nil
            httpsRequired = nil
            environment = nil
            notes = nil
            apiVersion = nil
            requiresAuthentication = nil
            authenticationMode = nil
            lastConnectedAt = nil
            lastSuccessfulAt = nil
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: ProfileDocumentCodingKey.self)
        try container.encode(Self.currentSchemaVersion, forKey: .schemaVersion)
        try container.encode(id, forKey: .id)
        try container.encodeIfPresent(displayName, forKey: .displayName)
        try container.encodeIfPresent(transport, forKey: .transport)
        try container.encodeIfPresent(host, forKey: .host)
        try container.encodeIfPresent(port, forKey: .port)
        try container.encodeIfPresent(httpsRequired, forKey: .httpsRequired)
        try container.encodeIfPresent(environment, forKey: .environment)
        try container.encodeIfPresent(notes, forKey: .notes)
        try container.encodeIfPresent(apiVersion, forKey: .apiVersion)
        try container.encodeIfPresent(requiresAuthentication, forKey: .requiresAuthentication)
        try container.encodeIfPresent(authenticationMode, forKey: .authenticationMode)
        try container.encodeIfPresent(lastConnectedAt, forKey: .lastConnectedAt)
        try container.encodeIfPresent(lastSuccessfulAt, forKey: .lastSuccessfulAt)
    }

    func makeProfile() throws -> ConnectionProfile {
        guard let identifier = UUID(uuidString: id) else {
            throw ConnectionProfileError.malformedStoredProfile
        }
        if schemaVersion >= 2, let transport = transport, let host = host {
            let mode = authenticationMode.flatMap(ProfileAuthenticationMode.init(rawValue:))
                ?? .deviceEnrollment
            return ConnectionProfile(
                id: identifier,
                displayName: (displayName ?? "Unnamed connection"),
                transport: ConnectionProtocol(rawValue: transport) ?? .http,
                host: host,
                port: port,
                environment: environment.flatMap(ConnectionEnvironment.init(rawValue:)) ?? .development,
                notes: notes ?? "",
                apiVersion: apiVersion,
                requiresAuthentication: requiresAuthentication ?? true,
                authenticationMode: mode,
                lastConnectedAt: lastConnectedAt.map(Self.date),
                lastSuccessfulAt: lastSuccessfulAt.map(Self.date)
            )
        }
        if schemaVersion == 1, let legacyEndpoint = legacyEndpoint {
            return try ConnectionProfile(id: identifier, name: displayName ?? "Legacy connection", endpoint: legacyEndpoint)
        }
        throw ConnectionProfileError.malformedStoredProfile
    }

    private static func epoch(_ date: Date) -> Int64 {
        Int64(date.timeIntervalSince1970)
    }

    private static func date(_ epoch: Int64) -> Date {
        Date(timeIntervalSince1970: TimeInterval(epoch))
    }
}
