import Foundation

/// The disposition for a WebKit navigation relative to the active JoeOS origin.
public enum NavigationDisposition: Equatable, Sendable {
    case allowSameOrigin
    case openExternally
    case block
}

/// Why a connection address was rejected before it could reach WebKit or a session.
public enum EndpointValidationError: Error, Equatable, LocalizedError, Sendable {
    case empty
    case malformedURL
    case unsupportedScheme(String)
    case invalidHost(String)
    case wildcardHost
    case invalidPort(Int)
    case credentialsNotAllowed
    case queryNotAllowed
    case fragmentNotAllowed
    case insecurePublicHost

    public var errorDescription: String? {
        switch self {
        case .empty:
            "Enter a JoeOS address."
        case .malformedURL:
            "The address is not a valid URL."
        case .unsupportedScheme(let scheme):
            "The scheme “\(scheme)” is not supported. Use http or https."
        case .invalidHost(let host):
            "The host “\(host)” is malformed."
        case .wildcardHost:
            "Wildcard hosts are not allowed."
        case .invalidPort(let port):
            "The port \(port) is not valid."
        case .credentialsNotAllowed:
            "URLs with embedded credentials are not allowed."
        case .queryNotAllowed:
            "URLs with query strings are not allowed in a profile."
        case .fragmentNotAllowed:
            "URLs with fragments are not allowed in a profile."
        case .insecurePublicHost:
            "Public HTTP hosts are not allowed. Use HTTPS, or a private, loopback, .local, or Tailscale address."
        }
    }
}

/// A normalized origin: lowercased scheme and host with a scheme-relative port.
///
/// Equality ignores a stored port that equals the scheme default, so
/// `https://halo.example.com` and `https://halo.example.com:443` are the same
/// origin for navigation and bootstrap purposes.
public struct EndpointOrigin: Equatable, Hashable, Sendable {
    public let scheme: String
    public let host: String
    public let port: Int?

    public init(scheme: String, host: String, port: Int?) {
        self.scheme = scheme.lowercased()
        self.host = host.lowercased()
        self.port = port
    }

    public init?(url: URL) {
        guard let scheme = url.scheme?.lowercased(),
              let host = url.host?.lowercased(),
              !host.isEmpty
        else {
            return nil
        }
        self.scheme = scheme
        self.host = host
        self.port = url.port
    }

    public var defaultPort: Int {
        scheme == "https" ? 443 : 80
    }

    public var effectivePort: Int {
        port ?? defaultPort
    }

    /// Host rendered for URL construction and user-visible copy (IPv6 bracketed).
    public var renderedHost: String {
        host.contains(":") ? "[\(host)]" : host
    }

    /// The URL for this origin with no path.
    public var url: URL? {
        var components = URLComponents()
        components.scheme = scheme
        components.host = host
        if let port {
            components.port = port
        }
        return components.url
    }

    public static func == (lhs: EndpointOrigin, rhs: EndpointOrigin) -> Bool {
        lhs.scheme == rhs.scheme && lhs.host == rhs.host && lhs.effectivePort == rhs.effectivePort
    }

    public func hash(into hasher: inout Hasher) {
        hasher.combine(scheme)
        hasher.combine(host)
        hasher.combine(effectivePort)
    }
}

/// A connection address that passed transport policy validation.
public struct ValidatedEndpoint: Sendable {
    public let url: URL
    public let origin: EndpointOrigin

    public init(url: URL, origin: EndpointOrigin) {
        self.url = url
        self.origin = origin
    }
}

/// Enforcing transport boundary for every JoeOS connection.
///
/// HTTPS is allowed for any valid host. HTTP is limited to loopback, RFC 1918,
/// link-local, `.local`, IPv6 unique-local/link-local, and Tailscale's
/// `100.64.0.0/10` range. Public HTTP is rejected before WebKit loads it.
public enum EndpointPolicy {

    /// Validates a profile address and returns a normalized same-origin URL.
    ///
    /// Query strings and fragments are rejected (they are not connection
    /// properties). A path, when present, is preserved.
    public static func validate(_ address: String) -> Result<ValidatedEndpoint, EndpointValidationError> {
        let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .failure(.empty) }

        guard let components = URLComponents(string: trimmed) else {
            return .failure(.malformedURL)
        }
        guard let rawScheme = components.scheme?.lowercased(),
              rawScheme == "http" || rawScheme == "https"
        else {
            return .failure(.unsupportedScheme(components.scheme ?? "none"))
        }
        guard let rawHost = components.host?.lowercased(), !rawHost.isEmpty else {
            return .failure(.invalidHost(components.host ?? "none"))
        }
        guard !rawHost.contains("*") else { return .failure(.wildcardHost) }
        guard isSyntacticallyValidHost(rawHost) else {
            return .failure(.invalidHost(rawHost))
        }
        if let port = components.port {
            guard (1...65_535).contains(port) else {
                return .failure(.invalidPort(port))
            }
        }
        if components.user != nil || components.password != nil {
            return .failure(.credentialsNotAllowed)
        }
        if components.query != nil {
            return .failure(.queryNotAllowed)
        }
        if components.fragment != nil {
            return .failure(.fragmentNotAllowed)
        }

        let origin = EndpointOrigin(scheme: rawScheme, host: rawHost, port: components.port)
        if rawScheme == "http" && !isPrivateHTTPHost(rawHost) {
            return .failure(.insecurePublicHost)
        }

        guard let url = origin.url else {
            return .failure(.malformedURL)
        }
        return .success(ValidatedEndpoint(url: url, origin: origin))
    }

    /// Decides whether WebKit may load a candidate URL relative to the active
    /// JoeOS endpoint. Same-origin navigation is always allowed; user-tapped
    /// external HTTP(S) links are handed to the system browser; everything else
    /// (cross-origin redirects, custom schemes) is blocked.
    public static func navigationDisposition(
        for candidate: URL,
        relativeTo endpoint: URL,
        userInitiated: Bool
    ) -> NavigationDisposition {
        guard let candidateOrigin = EndpointOrigin(url: candidate),
              let endpointOrigin = EndpointOrigin(url: endpoint)
        else {
            return .block
        }
        if candidateOrigin == endpointOrigin {
            return .allowSameOrigin
        }
        if userInitiated,
           candidateOrigin.scheme == "http" || candidateOrigin.scheme == "https" {
            return .openExternally
        }
        return .block
    }

    /// Whether the host may be reached over plain HTTP.
    public static func allowsHTTP(_ host: String) -> Bool {
        isPrivateHTTPHost(host.lowercased())
    }

    // MARK: - Host classification

    private static func isSyntacticallyValidHost(_ host: String) -> Bool {
        if host.contains(":") {
            return ipv6Bytes(host) != nil
        }
        guard host.contains(".") || host == "localhost" else { return false }
        guard host.range(of: #"[^a-zA-Z0-9.-]"#, options: .regularExpression) == nil else {
            return false
        }
        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        guard labels.allSatisfy({ !$0.isEmpty && $0.count <= 63 }) else { return false }
        if let bytes = ipv4Bytes(host) {
            _ = bytes
        }
        return true
    }

    private static func isPrivateHTTPHost(_ host: String) -> Bool {
        if host == "localhost" { return true }
        if host.hasSuffix(".local") { return true }

        if host.contains(":") {
            guard let bytes = ipv6Bytes(host) else { return false }
            if host == "::1" { return true }
            let first = bytes[0]
            let second = bytes[1]
            if first == 0xFE && second & 0xC0 == 0x80 { return true } // fe80::/10
            if first & 0xFE == 0xFC { return true }                    // fc00::/7
            return false
        }

        guard let bytes = ipv4Bytes(host) else { return false }
        switch bytes[0] {
        case 10:
            return true
        case 100:
            return (64...127).contains(bytes[1])
        case 127:
            return true
        case 169 where bytes[1] == 254:
            return true
        case 172:
            return (16...31).contains(bytes[1])
        case 192 where bytes[1] == 168:
            return true
        default:
            return false
        }
    }

    private static func ipv4Bytes(_ host: String) -> [UInt8]? {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var bytes: [UInt8] = []
        for part in parts {
            guard !part.isEmpty, part.count <= 3,
                  part.allSatisfy({ $0.isASCII && $0.isNumber })
            else {
                return nil
            }
            if part.count > 1 && part.first == "0" { return nil }
            guard let value = UInt8(part) else { return nil }
            bytes.append(value)
        }
        return bytes
    }

    private static func ipv6Bytes(_ host: String) -> [UInt8]? {
        let pieces: [String]
        if host.contains("::") {
            let halves = host.components(separatedBy: "::")
            guard halves.count == 2 else { return nil }
            let left = halves[0].isEmpty
                ? []
                : halves[0].split(separator: ":", omittingEmptySubsequences: false).map(String.init)
            let right = halves[1].isEmpty
                ? []
                : halves[1].split(separator: ":", omittingEmptySubsequences: false).map(String.init)
            guard left.count + right.count < 8 else { return nil }
            pieces = left + Array(repeating: "0", count: 8 - left.count - right.count) + right
        } else {
            let groups = host.split(separator: ":", omittingEmptySubsequences: false)
            guard groups.count == 8 else { return nil }
            pieces = groups.map(String.init)
        }
        var bytes: [UInt8] = []
        bytes.reserveCapacity(16)
        for piece in pieces {
            guard (1...4).contains(piece.count),
                  piece.range(of: #"[^0-9a-fA-F]"#, options: .regularExpression) == nil,
                  let value = UInt16(piece, radix: 16)
            else {
                return nil
            }
            bytes.append(UInt8((value >> 8) & 0xFF))
            bytes.append(UInt8(value & 0xFF))
        }
        return bytes
    }
}
