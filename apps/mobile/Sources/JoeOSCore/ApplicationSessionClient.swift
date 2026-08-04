import Foundation
#if canImport(Security)
import Security
#endif

// MARK: - Application session wire models

public struct ApplicationSession: Codable, Equatable, Sendable {
    public let sessionID: UUID
    public let userID: UUID
    public let deviceID: UUID
    public let organizationID: UUID
    public let workspaceID: UUID
    public let status: String
    public let createdAt: Int
    public let expiresAt: Int

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case userID = "user_id"
        case deviceID = "device_id"
        case organizationID = "organization_id"
        case workspaceID = "workspace_id"
        case status
        case createdAt = "created_at"
        case expiresAt = "expires_at"
    }
}

public struct PrincipalUser: Codable, Equatable, Sendable {
    public let id: UUID
    public let displayName: String?
    public let status: String?
}

public struct PrincipalReference: Codable, Equatable, Sendable {
    public let id: UUID
    public let name: String?
}

public struct AuthenticatedPrincipal: Codable, Equatable, Sendable {
    public let sessionID: UUID
    public let deviceID: UUID
    public let user: PrincipalUser
    public let organization: PrincipalReference
    public let workspace: PrincipalReference
    public let roles: [String]
    public let capabilities: [String]

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case deviceID = "device_id"
        case user
        case organization
        case workspace
        case roles
        case capabilities
    }

    public func hasCapability(_ capability: String) -> Bool {
        capabilities.contains(capability)
    }
}

public struct AuthenticationChallenge: Codable, Equatable, Sendable {
    public let challengeID: UUID
    public let deviceID: UUID
    public let userID: UUID
    public let organizationID: UUID
    public let workspaceID: UUID
    public let serverNonce: String
    public let expiresAt: Int
    public let message: String

    enum CodingKeys: String, CodingKey {
        case challengeID = "challenge_id"
        case deviceID = "device_id"
        case userID = "user_id"
        case organizationID = "organization_id"
        case workspaceID = "workspace_id"
        case serverNonce = "server_nonce"
        case expiresAt = "expires_at"
        case message
    }
}

public struct ApplicationSessionResponse: Codable, Equatable, Sendable {
    public let session: ApplicationSession
    public let refreshToken: String
    public let refreshID: UUID
    public let principal: AuthenticatedPrincipal

    enum CodingKeys: String, CodingKey {
        case session
        case refreshToken = "refresh_token"
        case refreshID = "refresh_id"
        case principal
    }
}

public struct StoredApplicationSession: Codable, Equatable, Sendable {
    public let session: ApplicationSession
    public let refreshID: UUID
    public let refreshToken: String
    public let principal: AuthenticatedPrincipal

    public init(response: ApplicationSessionResponse) {
        session = response.session
        refreshID = response.refreshID
        refreshToken = response.refreshToken
        principal = response.principal
    }

    public var sessionID: UUID { session.sessionID }
}

public enum SessionClientError: Error, Equatable, LocalizedError, Sendable {
    case invalidEndpoint
    case invalidChallenge
    case signingFailed
    case invalidResponse
    case sessionExpired
    case sessionRevoked

    public var errorDescription: String? {
        switch self {
        case .invalidEndpoint:
            "The selected connection is not valid."
        case .invalidChallenge:
            "The authentication challenge is invalid."
        case .signingFailed:
            "The device authentication key could not sign the challenge."
        case .invalidResponse:
            "The backend returned an invalid session response."
        case .sessionExpired:
            "The application session has expired."
        case .sessionRevoked:
            "The application session has been revoked."
        }
    }
}

// MARK: - Session credential persistence

public protocol SessionCredentialStoring: Sendable {
    func load() -> StoredApplicationSession?
    func save(_ stored: StoredApplicationSession) throws
    func clear()
}

/// In-memory session store (tests and development).
public final class InMemorySessionStore: SessionCredentialStoring, @unchecked Sendable {
    private let lock = NSLock()
    private var value: StoredApplicationSession?

    public init() {}

    public func load() -> StoredApplicationSession? {
        lock.lock()
        defer { lock.unlock() }
        return value
    }

    public func save(_ stored: StoredApplicationSession) throws {
        lock.lock()
        defer { lock.unlock() }
        value = stored
    }

    public func clear() {
        lock.lock()
        defer { lock.unlock() }
        value = nil
    }
}

#if canImport(Security)
/// ThisDeviceOnly Keychain-backed session store. The refresh credential is the
/// only server-issued secret and it is never written to logs.
public final class KeychainSessionStore: SessionCredentialStoring, @unchecked Sendable {
    private static let service = "com.joeos.client.application-session.v1"
    private static let account = "current"

    public init() {}

    public func load() -> StoredApplicationSession? {
        var query = baseQuery
        query[kSecReturnData] = kCFBooleanTrue
        query[kSecMatchLimit] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return try? JSONDecoder().decode(StoredApplicationSession.self, from: data)
    }

    public func save(_ stored: StoredApplicationSession) throws {
        let data = try JSONEncoder().encode(stored)
        let query = baseQuery
        if SecItemUpdate(query as CFDictionary, [kSecValueData: data] as CFDictionary) == errSecSuccess {
            return
        }
        var item = baseQuery
        item[kSecValueData] = data
        item[kSecAttrAccessible] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else {
            throw SessionClientError.invalidResponse
        }
    }

    public func clear() {
        SecItemDelete(baseQuery as CFDictionary)
    }

    private var baseQuery: [CFString: Any] {
        [
            kSecClass: kSecClassGenericPassword,
            kSecAttrService: Self.service,
            kSecAttrAccount: Self.account,
            kSecAttrSynchronizable: kCFBooleanFalse as Any,
        ]
    }
}
#endif

// MARK: - Application session client

/// Native Swift application-session integration (Phase P3A).
///
/// The client proves possession of its enrolled P-256 device-authentication key
/// by signing the backend challenge, then receives a short-lived application
/// session plus a single-use refresh credential. The backend stays authoritative
/// for identity, roles, capabilities, and session lifetime.
public struct ApplicationSessionClient: Sendable {
    private static let sessionHeader = "X-JoeOS-Session"

    private let backend: JoeOSBackendClient
    private let endpoint: ValidatedEndpoint

    public init(backend: JoeOSBackendClient, endpoint: ValidatedEndpoint) {
        self.backend = backend
        self.endpoint = endpoint
    }

    /// Requests a device-key authentication challenge for the assigned device.
    public func requestChallenge(deviceID: UUID, userID: UUID) async throws -> AuthenticationChallenge {
        struct Body: Encodable, Sendable {
            let deviceID: UUID
            let userID: UUID
            enum CodingKeys: String, CodingKey {
                case deviceID = "device_id"
                case userID = "user_id"
            }
        }
        return try await backend.post(
            Body(deviceID: deviceID, userID: userID),
            to: "/api/v1/auth/challenge",
            endpoint: endpoint
        )
    }

    /// Solves the challenge by signing the exact message with the
    /// device-authentication key and establishes an application session.
    public func solve(
        _ challenge: AuthenticationChallenge,
        signedWith key: any EnrollmentSigningKey
    ) async throws -> ApplicationSessionResponse {
        let signature: Data
        do {
            signature = try await key.signature(for: Data(challenge.message.utf8))
        } catch {
            throw SessionClientError.signingFailed
        }
        struct Body: Encodable, Sendable {
            let challengeID: UUID
            let signature: String
            enum CodingKeys: String, CodingKey {
                case challengeID = "challenge_id"
                case signature
            }
        }
        let response: ApplicationSessionResponse = try await backend.post(
            Body(
                challengeID: challenge.challengeID,
                signature: EnrollmentCoding.base64URLEncode(signature)
            ),
            to: "/api/v1/auth/session",
            endpoint: endpoint
        )
        guard response.session.status == "active" else {
            throw SessionClientError.invalidResponse
        }
        return response
    }

    /// Rotates to a new session with a single-use refresh credential.
    public func refresh(refreshID: UUID, refreshToken: String) async throws -> ApplicationSessionResponse {
        struct Body: Encodable, Sendable {
            let refreshID: UUID
            let refreshToken: String
            enum CodingKeys: String, CodingKey {
                case refreshID = "refresh_id"
                case refreshToken = "refresh_token"
            }
        }
        return try await backend.post(
            Body(refreshID: refreshID, refreshToken: refreshToken),
            to: "/api/v1/auth/refresh",
            endpoint: endpoint
        )
    }

    /// Explicitly revokes the application session.
    public func logout(sessionID: UUID) async throws {
        struct Body: Encodable, Sendable {
            let sessionID: UUID
            enum CodingKeys: String, CodingKey {
                case sessionID = "session_id"
            }
        }
        _ = try await backend.post(
            Body(sessionID: sessionID),
            to: "/api/v1/auth/logout",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        ) as EmptyResponse
    }

    /// Retrieves the authenticated principal for a live session. The backend
    /// rejects the request when the session is invalid, expired, or revoked.
    public func principal(sessionID: UUID) async throws -> AuthenticatedPrincipal {
        try await backend.get(
            AuthenticatedPrincipal.self,
            path: "/api/v1/principal",
            endpoint: endpoint,
            headers: Self.headers(sessionID)
        )
    }

    public static func headers(_ sessionID: UUID) -> [String: String] {
        [sessionHeader: sessionID.uuidString.lowercased()]
    }
}

private struct EmptyResponse: Decodable, Sendable {}
