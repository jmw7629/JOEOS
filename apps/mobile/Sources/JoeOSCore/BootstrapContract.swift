import Foundation

// MARK: - Bootstrap document (strict, non-secret discovery contract)

/// The strict schema-v2 bootstrap document served at `/api/v1/bootstrap`.
public struct BootstrapDocument: Codable, Sendable {
    public let schemaVersion: Int
    public let generatedAt: String
    public let server: ServerIdentity
    public let security: SecurityPosture
    public let deviceEnrollment: DeviceEnrollmentProfile
    public let capabilities: [CapabilityDescriptor]
    public let routes: [RouteDescriptor]

    enum CodingKeys: String, CodingKey, CaseIterable {
        case schemaVersion = "schema_version"
        case generatedAt = "generated_at"
        case server
        case security
        case deviceEnrollment = "device_enrollment"
        case capabilities
        case routes
    }

    public init(from decoder: Decoder) throws {
        try Self.rejectUnknownKeys(decoder: decoder, known: CodingKeys.allCases)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(Int.self, forKey: .schemaVersion)
        generatedAt = try container.decode(String.self, forKey: .generatedAt)
        server = try container.decode(ServerIdentity.self, forKey: .server)
        security = try container.decode(SecurityPosture.self, forKey: .security)
        deviceEnrollment = try container.decode(DeviceEnrollmentProfile.self, forKey: .deviceEnrollment)
        capabilities = try container.decode([CapabilityDescriptor].self, forKey: .capabilities)
        routes = try container.decode([RouteDescriptor].self, forKey: .routes)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(generatedAt, forKey: .generatedAt)
        try container.encode(server, forKey: .server)
        try container.encode(security, forKey: .security)
        try container.encode(deviceEnrollment, forKey: .deviceEnrollment)
        try container.encode(capabilities, forKey: .capabilities)
        try container.encode(routes, forKey: .routes)
    }

    /// Enumerates the actual JSON keys (including undeclared ones) and rejects
    /// any key the consuming type does not declare. Foundation only surfaces
    /// declared keys through a typed container's `allKeys`, so a catch-all key
    /// type is required to see (and reject) drift.
    static func rejectUnknownKeys<CodingKeysType>(
        decoder: Decoder,
        known: [CodingKeysType]
    ) throws where CodingKeysType: CodingKey {
        let container = try decoder.container(keyedBy: JSONCatchAllKey.self)
        let knownRaw = Set(known.map(\.stringValue))
        guard Set(container.allKeys.map(\.stringValue)).isSubset(of: knownRaw) else {
            throw DecodingError.dataCorrupted(
                DecodingError.Context(
                    codingPath: decoder.codingPath,
                    debugDescription: "Bootstrap document contains unknown fields."
                )
            )
        }
    }
}

/// A catch-all coding key that never fails to decode, so the real set of JSON
/// keys (including undeclared ones) can be inspected during strict decoding.
public struct JSONCatchAllKey: CodingKey, Sendable {
    public var stringValue: String
    public var intValue: Int?

    public init?(stringValue: String) {
        self.stringValue = stringValue
    }

    public init?(intValue: Int) {
        self.stringValue = String(intValue)
        self.intValue = intValue
    }
}

public struct ServerIdentity: Codable, Sendable {
    public let serverID: String
    public let productID: String?
    public let displayName: String
    public let serverVersion: String
    public let apiVersion: String?
    public let deploymentMode: String?

    enum CodingKeys: String, CodingKey, CaseIterable {
        case serverID = "server_id"
        case productID = "product_id"
        case displayName = "display_name"
        case serverVersion = "server_version"
        case apiVersion = "api_version"
        case deploymentMode = "deployment_mode"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try BootstrapDocument.rejectUnknownKeys(decoder: decoder, known: CodingKeys.allCases)
        serverID = try container.decode(String.self, forKey: .serverID)
        productID = try container.decodeIfPresent(String.self, forKey: .productID)
        displayName = try container.decode(String.self, forKey: .displayName)
        serverVersion = try container.decode(String.self, forKey: .serverVersion)
        apiVersion = try container.decodeIfPresent(String.self, forKey: .apiVersion)
        deploymentMode = try container.decodeIfPresent(String.self, forKey: .deploymentMode)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(serverID, forKey: .serverID)
        try container.encodeIfPresent(productID, forKey: .productID)
        try container.encode(displayName, forKey: .displayName)
        try container.encode(serverVersion, forKey: .serverVersion)
        try container.encodeIfPresent(apiVersion, forKey: .apiVersion)
        try container.encodeIfPresent(deploymentMode, forKey: .deploymentMode)
    }
}

public struct SecurityPosture: Codable, Sendable {
    public let ownershipModel: String?
    public let networkBoundary: String?
    public let applicationAuthentication: String
    public let deviceEnrollment: String
    public let roleBasedAccess: String
    public let privilegedActions: String
    public let publicInternetReady: Bool
    public let secretsReturned: Bool
    public let warning: String

    enum CodingKeys: String, CodingKey, CaseIterable {
        case ownershipModel = "ownership_model"
        case networkBoundary = "network_boundary"
        case applicationAuthentication = "application_authentication"
        case deviceEnrollment = "device_enrollment"
        case roleBasedAccess = "role_based_access"
        case privilegedActions = "privileged_actions"
        case publicInternetReady = "public_internet_ready"
        case secretsReturned = "secrets_returned"
        case warning
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try BootstrapDocument.rejectUnknownKeys(decoder: decoder, known: CodingKeys.allCases)
        ownershipModel = try container.decodeIfPresent(String.self, forKey: .ownershipModel)
        networkBoundary = try container.decodeIfPresent(String.self, forKey: .networkBoundary)
        applicationAuthentication = try container.decode(String.self, forKey: .applicationAuthentication)
        deviceEnrollment = try container.decode(String.self, forKey: .deviceEnrollment)
        roleBasedAccess = try container.decode(String.self, forKey: .roleBasedAccess)
        privilegedActions = try container.decode(String.self, forKey: .privilegedActions)
        publicInternetReady = try container.decode(Bool.self, forKey: .publicInternetReady)
        secretsReturned = try container.decode(Bool.self, forKey: .secretsReturned)
        warning = try container.decode(String.self, forKey: .warning)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(ownershipModel, forKey: .ownershipModel)
        try container.encodeIfPresent(networkBoundary, forKey: .networkBoundary)
        try container.encode(applicationAuthentication, forKey: .applicationAuthentication)
        try container.encode(deviceEnrollment, forKey: .deviceEnrollment)
        try container.encode(roleBasedAccess, forKey: .roleBasedAccess)
        try container.encode(privilegedActions, forKey: .privilegedActions)
        try container.encode(publicInternetReady, forKey: .publicInternetReady)
        try container.encode(secretsReturned, forKey: .secretsReturned)
        try container.encode(warning, forKey: .warning)
    }
}

public struct DeviceEnrollmentProfile: Codable, Sendable {
    public let wireProtocol: String
    public let offerAuthority: String
    public let pairingSecretBytes: Int
    public let offerTTLSeconds: Int
    public let challengeTTLSeconds: Int
    public let keyAlgorithm: String
    public let publicKeyFormat: String
    public let signatureFormat: String
    public let proofAlgorithm: String
    public let requiredKeyPurposes: [String]
    public let activationState: String
    public let grantsAuthority: Bool

    enum CodingKeys: String, CodingKey, CaseIterable {
        case wireProtocol = "protocol"
        case offerAuthority = "offer_authority"
        case pairingSecretBytes = "pairing_secret_bytes"
        case offerTTLSeconds = "offer_ttl_seconds"
        case challengeTTLSeconds = "challenge_ttl_seconds"
        case keyAlgorithm = "key_algorithm"
        case publicKeyFormat = "public_key_format"
        case signatureFormat = "signature_format"
        case proofAlgorithm = "proof_algorithm"
        case requiredKeyPurposes = "required_key_purposes"
        case activationState = "activation_state"
        case grantsAuthority = "grants_authority"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try BootstrapDocument.rejectUnknownKeys(decoder: decoder, known: CodingKeys.allCases)
        wireProtocol = try container.decode(String.self, forKey: .wireProtocol)
        offerAuthority = try container.decode(String.self, forKey: .offerAuthority)
        pairingSecretBytes = try container.decode(Int.self, forKey: .pairingSecretBytes)
        offerTTLSeconds = try container.decode(Int.self, forKey: .offerTTLSeconds)
        challengeTTLSeconds = try container.decode(Int.self, forKey: .challengeTTLSeconds)
        keyAlgorithm = try container.decode(String.self, forKey: .keyAlgorithm)
        publicKeyFormat = try container.decode(String.self, forKey: .publicKeyFormat)
        signatureFormat = try container.decode(String.self, forKey: .signatureFormat)
        proofAlgorithm = try container.decode(String.self, forKey: .proofAlgorithm)
        requiredKeyPurposes = try container.decode([String].self, forKey: .requiredKeyPurposes)
        activationState = try container.decode(String.self, forKey: .activationState)
        grantsAuthority = try container.decode(Bool.self, forKey: .grantsAuthority)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(wireProtocol, forKey: .wireProtocol)
        try container.encode(offerAuthority, forKey: .offerAuthority)
        try container.encode(pairingSecretBytes, forKey: .pairingSecretBytes)
        try container.encode(offerTTLSeconds, forKey: .offerTTLSeconds)
        try container.encode(challengeTTLSeconds, forKey: .challengeTTLSeconds)
        try container.encode(keyAlgorithm, forKey: .keyAlgorithm)
        try container.encode(publicKeyFormat, forKey: .publicKeyFormat)
        try container.encode(signatureFormat, forKey: .signatureFormat)
        try container.encode(proofAlgorithm, forKey: .proofAlgorithm)
        try container.encode(requiredKeyPurposes, forKey: .requiredKeyPurposes)
        try container.encode(activationState, forKey: .activationState)
        try container.encode(grantsAuthority, forKey: .grantsAuthority)
    }
}

public struct CapabilityDescriptor: Codable, Sendable {
    public let id: String
    public let status: String
    public let access: String
    public let routeIDs: [String]
    public let description: String?

    enum CodingKeys: String, CodingKey, CaseIterable {
        case id
        case status
        case access
        case routeIDs = "route_ids"
        case description
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try BootstrapDocument.rejectUnknownKeys(decoder: decoder, known: CodingKeys.allCases)
        id = try container.decode(String.self, forKey: .id)
        status = try container.decode(String.self, forKey: .status)
        access = try container.decode(String.self, forKey: .access)
        routeIDs = try container.decode([String].self, forKey: .routeIDs)
        description = try container.decodeIfPresent(String.self, forKey: .description)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(status, forKey: .status)
        try container.encode(access, forKey: .access)
        try container.encode(routeIDs, forKey: .routeIDs)
        try container.encodeIfPresent(description, forKey: .description)
    }
}

public struct RouteDescriptor: Codable, Sendable {
    public let id: String
    public let path: String
    public let wireProtocol: String
    public let methods: [String]
    public let access: String
    public let stability: String?
    public let description: String?

    enum CodingKeys: String, CodingKey, CaseIterable {
        case id
        case path
        case wireProtocol = "protocol"
        case methods
        case access
        case stability
        case description
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        try BootstrapDocument.rejectUnknownKeys(decoder: decoder, known: CodingKeys.allCases)
        id = try container.decode(String.self, forKey: .id)
        path = try container.decode(String.self, forKey: .path)
        wireProtocol = try container.decode(String.self, forKey: .wireProtocol)
        methods = try container.decode([String].self, forKey: .methods)
        access = try container.decode(String.self, forKey: .access)
        stability = try container.decodeIfPresent(String.self, forKey: .stability)
        description = try container.decodeIfPresent(String.self, forKey: .description)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(path, forKey: .path)
        try container.encode(wireProtocol, forKey: .wireProtocol)
        try container.encode(methods, forKey: .methods)
        try container.encode(access, forKey: .access)
        try container.encodeIfPresent(stability, forKey: .stability)
        try container.encodeIfPresent(description, forKey: .description)
    }
}

// MARK: - Validated contract

public enum BootstrapContractViolation: Error, Equatable, LocalizedError, Sendable {
    case unsupportedSchemaVersion(Int)
    case invalidServerID
    case unsafeSecurityPosture
    case invalidDeviceEnrollmentProfile
    case unknownRouteReference
    case routeAccessMismatch
    case invalidDescriptor
    case missingSecurityGate(String)
    case missingDiscoveryContract
    case missingDeviceEnrollmentContract

    public var errorDescription: String? {
        switch self {
        case .unsupportedSchemaVersion(let version):
            "Unsupported bootstrap schema version \(version)."
        case .invalidServerID:
            "The bootstrap server identifier is not a version-4 UUID."
        case .unsafeSecurityPosture:
            "The server reports a public-internet-ready posture that the client does not accept."
        case .invalidDeviceEnrollmentProfile:
            "The advertised device-enrollment profile does not match the fixed JoeOS contract."
        case .unknownRouteReference:
            "A capability references a route that is not advertised."
        case .routeAccessMismatch:
            "An advertised route access does not match its capability."
        case .invalidDescriptor:
            "An advertised descriptor is malformed."
        case .missingSecurityGate(let name):
            "The advertised contract is missing the required \(name) gate."
        case .missingDiscoveryContract:
            "The advertised contract does not expose the exact same-origin discovery route and capability."
        case .missingDeviceEnrollmentContract:
            "The advertised contract does not expose the exact device-enrollment routes and capability."
        }
    }
}

/// The validated, consumed form of the bootstrap contract.
public struct ValidatedBootstrapContract: Sendable {
    public let displayName: String
    public let serverVersion: String
    public let observedServerID: UUID
    public let supportsLocalConsolePairing: Bool
    public let hasApplicationAuthentication: Bool
    public let hasRoleBasedAccess: Bool
    public let hasPrivilegedActions: Bool
    public let document: BootstrapDocument

    public init(
        displayName: String,
        serverVersion: String,
        observedServerID: UUID,
        supportsLocalConsolePairing: Bool,
        hasApplicationAuthentication: Bool,
        hasRoleBasedAccess: Bool,
        hasPrivilegedActions: Bool,
        document: BootstrapDocument
    ) {
        self.displayName = displayName
        self.serverVersion = serverVersion
        self.observedServerID = observedServerID
        self.supportsLocalConsolePairing = supportsLocalConsolePairing
        self.hasApplicationAuthentication = hasApplicationAuthentication
        self.hasRoleBasedAccess = hasRoleBasedAccess
        self.hasPrivilegedActions = hasPrivilegedActions
        self.document = document
    }
}

/// Validates the strict schema-v2 JoeOS bootstrap contract.
public enum BootstrapContractValidator {

    public static func validate(_ document: BootstrapDocument) throws -> ValidatedBootstrapContract {
        guard document.schemaVersion == 2 else {
            throw BootstrapContractViolation.unsupportedSchemaVersion(document.schemaVersion)
        }
        guard let serverID = UUID(uuidString: document.server.serverID),
              EnrollmentCoding.isVersion4(serverID),
              document.server.serverID == serverID.uuidString.lowercased()
        else {
            throw BootstrapContractViolation.invalidServerID
        }
        guard !document.security.publicInternetReady else {
            throw BootstrapContractViolation.unsafeSecurityPosture
        }
        guard isCanonicalEnrollmentProfile(document.deviceEnrollment) else {
            throw BootstrapContractViolation.invalidDeviceEnrollmentProfile
        }

        let capabilityIDs = Set(document.capabilities.map(\.id))
        let routeIDs = Set(document.routes.map(\.id))

        for capability in document.capabilities {
            for referenced in capability.routeIDs where !routeIDs.contains(referenced) {
                throw BootstrapContractViolation.unknownRouteReference
            }
        }

        for route in document.routes where !isValidRoute(route) {
            throw BootstrapContractViolation.invalidDescriptor
        }

        if let discovery = document.capabilities.first(where: { $0.id == "discovery.bootstrap" }) {
            if discovery.access != "read_only" {
                throw BootstrapContractViolation.routeAccessMismatch
            }
        }
        if let discovery = document.routes.first(where: { $0.id == "bootstrap.discovery" }),
           discovery.access != "read_only" {
            throw BootstrapContractViolation.routeAccessMismatch
        }

        if !capabilityIDs.contains("secrets.management") {
            throw BootstrapContractViolation.missingSecurityGate("secrets.management")
        }

        let hasExactDiscovery = document.routes.contains { route in
            route.id == "bootstrap.discovery" &&
            route.path == "/api/v1/bootstrap" &&
            route.methods.contains("GET") &&
            route.access == "read_only" &&
            route.wireProtocol == "http"
        }
        let hasDiscoveryCapability = document.capabilities.contains { capability in
            capability.id == "discovery.bootstrap" && capability.routeIDs.contains("bootstrap.discovery")
        }
        guard hasExactDiscovery && hasDiscoveryCapability else {
            throw BootstrapContractViolation.missingDiscoveryContract
        }

        let hasExactEnrollmentRoutes = document.routes.contains { route in
            route.id == "device-enrollment.challenge" &&
            route.path == "/api/v1/device-enrollment/challenges" &&
            route.methods.contains("POST") &&
            route.access == "enrollment"
        } && document.routes.contains { route in
            route.id == "device-enrollment.complete" &&
            route.path == "/api/v1/device-enrollment/challenges/{challenge_id}/complete" &&
            route.methods.contains("POST") &&
            route.access == "enrollment"
        }
        let hasExactEnrollmentCapability = document.capabilities.contains { capability in
            capability.id == "identity.device_enrollment" &&
            capability.access == "enrollment" &&
            capability.routeIDs == ["device-enrollment.challenge", "device-enrollment.complete"]
        }
        guard hasExactEnrollmentRoutes && hasExactEnrollmentCapability else {
            throw BootstrapContractViolation.missingDeviceEnrollmentContract
        }

        return ValidatedBootstrapContract(
            displayName: document.server.displayName,
            serverVersion: document.server.serverVersion,
            observedServerID: serverID,
            supportsLocalConsolePairing: document.security.deviceEnrollment == "operator_pairing_v1" &&
                document.capabilities.contains { $0.id == "identity.device_enrollment" },
            hasApplicationAuthentication: document.security.applicationAuthentication != "unavailable",
            hasRoleBasedAccess: document.security.roleBasedAccess != "unavailable",
            hasPrivilegedActions: document.security.privilegedActions != "unavailable",
            document: document
        )
    }

    private static func isCanonicalEnrollmentProfile(_ profile: DeviceEnrollmentProfile) -> Bool {
        profile.wireProtocol == "joeos-device-enrollment-v1" &&
        profile.offerAuthority == "local_console_only" &&
        profile.pairingSecretBytes == 32 &&
        profile.offerTTLSeconds == 300 &&
        profile.challengeTTLSeconds == 120 &&
        profile.keyAlgorithm == "ES256" &&
        profile.publicKeyFormat == "spki_der_base64url" &&
        profile.signatureFormat == "x962_der_base64url" &&
        profile.proofAlgorithm == "HKDF-SHA256+HMAC-SHA256+ECDSA-SHA256" &&
        profile.requiredKeyPurposes == ["device_authentication", "approval"] &&
        profile.activationState == "active_unassigned" &&
        !profile.grantsAuthority
    }

    private static func isValidRoute(_ route: RouteDescriptor) -> Bool {
        guard route.path.hasPrefix("/"), !route.path.hasPrefix("//") else { return false }
        guard !route.path.contains("..") else { return false }
        guard !route.methods.isEmpty else { return false }
        return route.wireProtocol == "http" || route.wireProtocol == "websocket"
    }
}
