import Foundation

public enum DeviceEnrollmentError: Error, Equatable, LocalizedError, Sendable {
    case invalidPairingCode
    case invalidOrigin
    case invalidServerResponse
    case invalidReceipt
    case keyGenerationFailed
    case signingFailed
    case keysAreNotDistinct
    case challengeExpired
    case malformedCompletionDocument
    case networkUnavailable

    public var errorDescription: String? {
        switch self {
        case .invalidPairingCode:
            "The pairing code is not a valid JoeOS local-console code."
        case .invalidOrigin:
            "The pairing code is not bound to the active JoeOS origin."
        case .invalidServerResponse:
            "JoeOS returned a response that did not match the strict enrollment contract."
        case .invalidReceipt:
            "JoeOS returned a device receipt that did not validate."
        case .keyGenerationFailed:
            "The device keys could not be created or read."
        case .signingFailed:
            "The device key could not produce a valid signature."
        case .keysAreNotDistinct:
            "The device-authentication and approval keys are not distinct."
        case .challengeExpired:
            "The enrollment challenge has expired. Start a new local-console pairing window."
        case .malformedCompletionDocument:
            "The saved enrollment completion could not be validated."
        case .networkUnavailable:
            "JoeOS could not be reached to complete enrollment."
        }
    }
}

/// Canonical base64url coding and UUID-version checks for the enrollment
/// protocol and the bootstrap contract.
public enum EnrollmentCoding {

    public static func base64URLEncode(_ data: Data) -> String {
        data.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }

    public static func base64URLDecode(_ value: String) -> Data? {
        var encoded = value
            .replacingOccurrences(of: "-", with: "+")
            .replacingOccurrences(of: "_", with: "/")
        let remainder = encoded.count % 4
        if remainder != 0 {
            encoded += String(repeating: "=", count: 4 - remainder)
        }
        guard let data = Data(base64Encoded: encoded) else { return nil }
        guard base64URLEncode(data) == value else { return nil }
        return data
    }

    public static func isVersion4(_ value: UUID) -> Bool {
        var bytes = value.uuid
        return withUnsafeBytes(of: &bytes) {
            ($0[6] >> 4) == 4 && ($0[8] & 0xc0) == 0x80
        }
    }

    /// Validates a canonical base64url-encoded 32-byte value (43 characters).
    public static func isCanonicalBase64URL32(_ value: String) -> Bool {
        guard let decoded = base64URLDecode(value), decoded.count == 32 else { return false }
        return base64URLEncode(decoded) == value
    }
}

public enum EnrollmentAudienceOriginError: Error, Equatable, LocalizedError, Sendable {
    case empty
    case malformed
    case unsupportedScheme(String)
    case invalidHost(String)
    case invalidPort(Int)
    case credentialsNotAllowed
    case queryNotAllowed
    case fragmentNotAllowed
    case ipv4MappedNotAllowed

    public var errorDescription: String? {
        switch self {
        case .empty:
            "The pairing code does not contain an origin."
        case .malformed:
            "The pairing code origin is malformed."
        case .unsupportedScheme(let scheme):
            "The origin scheme “\(scheme)” is not supported."
        case .invalidHost(let host):
            "The origin host “\(host)” is malformed."
        case .invalidPort(let port):
            "The origin port \(port) is invalid."
        case .credentialsNotAllowed:
            "Origins with embedded credentials are not allowed."
        case .queryNotAllowed:
            "Origins with query strings are not allowed."
        case .fragmentNotAllowed:
            "Origins with fragments are not allowed."
        case .ipv4MappedNotAllowed:
            "IPv4-mapped IPv6 origins are not allowed."
        }
    }
}

/// A strictly canonical origin: lowercase scheme and host, default port
/// omitted, IPv4-mapped IPv6 rejected. Used to bind enrollment to the exact
/// active JoeOS origin.
public struct EnrollmentAudienceOrigin: Equatable, Hashable, Sendable {
    public let value: String

    public init(_ rawValue: String) throws {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw EnrollmentAudienceOriginError.empty }
        guard let components = URLComponents(string: trimmed) else {
            throw EnrollmentAudienceOriginError.malformed
        }
        guard let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https"
        else {
            throw EnrollmentAudienceOriginError.unsupportedScheme(components.scheme ?? "none")
        }
        guard let host = components.host?.lowercased(), !host.isEmpty else {
            throw EnrollmentAudienceOriginError.invalidHost(components.host ?? "none")
        }
        if host.contains(":") && host.lowercased().contains(":ffff:") {
            throw EnrollmentAudienceOriginError.ipv4MappedNotAllowed
        }
        if let port = components.port, !(1...65_535).contains(port) {
            throw EnrollmentAudienceOriginError.invalidPort(port)
        }
        if components.user != nil || components.password != nil {
            throw EnrollmentAudienceOriginError.credentialsNotAllowed
        }
        if components.query != nil {
            throw EnrollmentAudienceOriginError.queryNotAllowed
        }
        if components.fragment != nil {
            throw EnrollmentAudienceOriginError.fragmentNotAllowed
        }
        let defaultPort = scheme == "https" ? 443 : 80
        let renderedHost = host.contains(":") ? "[\(host)]" : host
        let portSuffix = components.port.flatMap { $0 == defaultPort ? nil : ":\($0)" } ?? ""
        value = "\(scheme)://\(renderedHost)\(portSuffix)"
    }
}

/// A manual local-console pairing code:
/// `JOEOS1|<canonical origin>|<offer UUID>|<32-byte base64url secret>`.
public struct JoeOSPairingCode: Equatable, Sendable {
    public static let prefix = "JOEOS1"
    public static let expectedSegmentCount = 4
    public static let secretByteCount = 32

    public let pairingSecret: Data
    public let offerID: UUID
    public let audienceOrigin: EnrollmentAudienceOrigin

    public init(_ manualCode: String) throws {
        let value = manualCode.trimmingCharacters(in: .whitespacesAndNewlines)
        guard value == manualCode else { throw DeviceEnrollmentError.invalidPairingCode }
        let segments = value.split(separator: "|", omittingEmptySubsequences: false)
        guard segments.count == Self.expectedSegmentCount,
              segments[0] == Self.prefix
        else {
            throw DeviceEnrollmentError.invalidPairingCode
        }
        let origin: EnrollmentAudienceOrigin
        do {
            origin = try EnrollmentAudienceOrigin(String(segments[1]))
        } catch {
            throw DeviceEnrollmentError.invalidOrigin
        }
        guard let offerID = UUID(uuidString: String(segments[2])),
              EnrollmentCoding.isVersion4(offerID),
              String(segments[2]) == offerID.uuidString.lowercased()
        else {
            throw DeviceEnrollmentError.invalidPairingCode
        }
        guard let secret = EnrollmentCoding.base64URLDecode(String(segments[3])),
              secret.count == Self.secretByteCount
        else {
            throw DeviceEnrollmentError.invalidPairingCode
        }
        self.pairingSecret = secret
        self.offerID = offerID
        self.audienceOrigin = origin
    }
}
