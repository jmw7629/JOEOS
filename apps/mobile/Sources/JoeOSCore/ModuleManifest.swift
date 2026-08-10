import Foundation

/// Cross-platform JoeOS module manifest.
///
/// This mirrors `server/modules/manifest.py` (ModuleManifest). It is a
/// declarative, data-only contract: a client renders a manifest through a
/// trusted native component registry and never downloads executable code.
/// Unknown fields are ignored (additive); unknown widget types fail safely.
public struct ModuleManifest: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let type: String
    public let version: String
    public let displayName: String
    public let description: String
    public let icon: String
    public let category: String
    public let subcategory: String
    public let route: String
    public let supportedFormFactors: [String]
    public let requiredPermissions: [String]
    public let requiredCapabilities: [String]
    public let commands: [String]
    public let actions: [String]
    public let dataSources: [String]
    public let joeContext: JoeContextScope
    public let widgets: [ModuleWidget]
    public let inspection: Bool
    public let featureFlags: [String]
    public let policyRequirements: [String]
    public let minClientVersion: String
    public let visibility: String
    public let ordering: Int
    public let pinned: Bool
    public let userCustomizable: Bool
    public let schemaVersion: Int

    public init(
        id: String,
        type: String = "module",
        version: String = "1.0.0",
        displayName: String = "",
        description: String = "",
        icon: String = "",
        category: String = "",
        subcategory: String = "",
        route: String = "",
        supportedFormFactors: [String] = ["phone", "tablet", "laptop", "desktop"],
        requiredPermissions: [String] = [],
        requiredCapabilities: [String] = [],
        commands: [String] = [],
        actions: [String] = [],
        dataSources: [String] = [],
        joeContext: JoeContextScope = .none,
        widgets: [ModuleWidget] = [],
        inspection: Bool = false,
        featureFlags: [String] = [],
        policyRequirements: [String] = [],
        minClientVersion: String = "",
        visibility: String = "visible",
        ordering: Int = 0,
        pinned: Bool = false,
        userCustomizable: Bool = false,
        schemaVersion: Int = 1
    ) {
        self.id = id
        self.type = type
        self.version = version
        self.displayName = displayName
        self.description = description
        self.icon = icon
        self.category = category
        self.subcategory = subcategory
        self.route = route
        self.supportedFormFactors = supportedFormFactors
        self.requiredPermissions = requiredPermissions
        self.requiredCapabilities = requiredCapabilities
        self.commands = commands
        self.actions = actions
        self.dataSources = dataSources
        self.joeContext = joeContext
        self.widgets = widgets
        self.inspection = inspection
        self.featureFlags = featureFlags
        self.policyRequirements = policyRequirements
        self.minClientVersion = minClientVersion
        self.visibility = visibility
        self.ordering = ordering
        self.pinned = pinned
        self.userCustomizable = userCustomizable
        self.schemaVersion = schemaVersion
    }

    // Allow the UI to use `id` as the Identifiable requirement without a
    // CodingKeys collision: the backend field is `id`, and we expose `id` from
    // the decoded `id` directly.
    private enum CodingKeys: String, CodingKey {
        case id, type, version, displayName = "display_name", description, icon
        case category, subcategory, route, supportedFormFactors = "supported_form_factors"
        case requiredPermissions = "required_permissions"
        case requiredCapabilities = "required_capabilities"
        case commands, actions, dataSources = "data_sources"
        case joeContext = "joe_context"
        case widgets, inspection, featureFlags = "feature_flags"
        case policyRequirements = "policy_requirements"
        case minClientVersion = "min_client_version"
        case visibility, ordering, pinned, userCustomizable = "user_customizable"
        case schemaVersion = "schema_version"
    }
}

/// Bounded, authorized Joe context scope. A module only ever receives context
/// the user is authorized to access; scope never grants authority.
public struct JoeContextScope: Codable, Equatable, Sendable {
    public let kind: String
    public let objectType: String?
    public let objectID: String?

    public init(kind: String = "none", objectType: String? = nil, objectID: String? = nil) {
        self.kind = kind
        self.objectType = objectType
        self.objectID = objectID
    }

    public static let none = JoeContextScope(kind: "none")
}

/// One trusted, renderable widget in a module. Unknown types are rejected at
/// validation time on the server and ignored by the native renderer.
public struct ModuleWidget: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let type: String
    public let title: String
    public let config: [String: JSONValue]

    public init(id: String, type: String, title: String = "", config: [String: JSONValue] = [:]) {
        self.id = id
        self.type = type
        self.title = title
        self.config = config
    }
}

/// Minimal JSON value for widget config (Sendable + Codable).
public enum JSONValue: Codable, Equatable, Sendable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let string = try? container.decode(String.self) { self = .string(string); return }
        if let number = try? container.decode(Double.self) { self = .number(number); return }
        if let bool = try? container.decode(Bool.self) { self = .bool(bool); return }
        if let array = try? container.decode([JSONValue].self) { self = .array(array); return }
        if let object = try? container.decode([String: JSONValue].self) { self = .object(object); return }
        self = .null
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}
