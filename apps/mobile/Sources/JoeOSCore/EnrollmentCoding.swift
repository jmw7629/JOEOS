import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

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

    /// RFC 4648 base32 encoding without padding (uppercase alphabet).
    public static func base32Encode(_ data: Data) -> String {
        var result = ""
        var buffer = 0
        var bits = 0
        for byte in data {
            buffer = (buffer << 8) | Int(byte)
            bits += 8
            while bits >= 5 {
                bits -= 5
                result.append(base32Character[(buffer >> bits) & 0x1f])
            }
        }
        if bits > 0 {
            buffer <<= (5 - bits)
            result.append(base32Character[buffer & 0x1f])
        }
        return result
    }

    /// Decodes strict RFC 4648 base32 (uppercase alphabet, no padding).
    /// Rejects lowercase input, padding, and non-canonical trailing bits.
    public static func base32Decode(_ value: String) -> Data? {
        let characters = Array(value.utf8)
        guard !characters.isEmpty, characters.count % 8 != 1 else { return nil }
        var buffer = 0
        var bits = 0
        var result = Data()
        for character in characters {
            guard character < 128, let index = base32Index[Int(character)] else { return nil }
            buffer = (buffer << 5) | index
            bits += 5
            if bits >= 8 {
                bits -= 8
                result.append(UInt8((buffer >> bits) & 0xff))
            }
        }
        if bits > 0, buffer & ((1 << bits) - 1) != 0 {
            return nil
        }
        return result
    }

    private static let base32Character: [Character] =
        Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")

    private static let base32Index: [Int?] = {
        var table = [Int?](repeating: nil, count: 128)
        for (index, character) in base32Character.enumerated() {
            table[Int(character.asciiValue!)] = index
        }
        return table
    }()

    // MARK: IP canonicalization

    struct IPv6Address: Sendable {
        let bytes: Data
        let ipv4Mapped: Bool

        var compressed: String {
            Self.compressedIPv6(bytes)
        }

        static func compressedIPv6(_ bytes: Data) -> String {
            let groups = stride(from: 0, to: 16, by: 2).map { index -> UInt16 in
                UInt16(bytes[index]) << 8 | UInt16(bytes[index + 1])
            }
            var bestStart = -1
            var bestLength = 0
            var currentStart = -1
            var currentLength = 0
            for (index, group) in groups.enumerated() {
                if group == 0 {
                    if currentStart == -1 { currentStart = index }
                    currentLength += 1
                    if currentLength > bestLength {
                        bestStart = currentStart
                        bestLength = currentLength
                    }
                } else {
                    currentStart = -1
                    currentLength = 0
                }
            }
            if bestLength < 2 {
                return groups.map { String($0, radix: 16) }.joined(separator: ":")
            }
            let head = groups[0..<bestStart].map { String($0, radix: 16) }.joined(separator: ":")
            let tail = groups[(bestStart + bestLength)...].map { String($0, radix: 16) }.joined(separator: ":")
            var result = ""
            if bestStart > 0 { result += head }
            result += "::"
            if bestStart + bestLength < 8 { result += tail }
            return result
        }
    }

    /// Validates a dotted-quad IPv4 and returns its canonical dotted form.
    static func ipv4Address(_ text: String) -> String? {
        guard let raw = inetPTON(AF_INET, text) else { return nil }
        let octets = [UInt8](raw)
        return octets.map(String.init).joined(separator: ".")
    }

    /// Validates an IPv6 literal (without brackets) and returns its parsed form.
    static func ipv6Address(_ text: String) -> IPv6Address? {
        guard let raw = inetPTON(AF_INET6, text) else { return nil }
        let bytes = [UInt8](raw)
        guard bytes.count == 16 else { return nil }
        let mapped = bytes.prefix(10).allSatisfy({ $0 == 0 })
            && bytes[10] == 0xff && bytes[11] == 0xff
        return IPv6Address(bytes: Data(bytes), ipv4Mapped: mapped)
    }

    private static func inetPTON(_ family: Int32, _ text: String) -> Data? {
        let length = family == AF_INET ? MemoryLayout<in_addr>.size : MemoryLayout<in6_addr>.size
        var result = Data(count: length)
        let status = result.withUnsafeMutableBytes { (raw: UnsafeMutableRawBufferPointer) -> Int32 in
            guard let base = raw.baseAddress else { return 0 }
            return text.withCString { inet_pton(family, $0, base) }
        }
        return status == 1 ? result : nil
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

/// A strictly canonical origin mirroring the Python identity service's
/// `validate_canonical_audience_origin`: lowercase ASCII scheme and host,
/// default ports rejected, IPv4-mapped IPv6 rejected, and the input must
/// equal the canonical rendering exactly.
public struct EnrollmentAudienceOrigin: Equatable, Hashable, Sendable {
    public let value: String

    public init(_ rawValue: String) throws {
        let trimmed = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw EnrollmentAudienceOriginError.empty }
        guard trimmed.unicodeScalars.allSatisfy(\.isASCII) else {
            throw EnrollmentAudienceOriginError.malformed
        }
        guard let components = URLComponents(string: trimmed),
              let scheme = components.scheme,
              let rawHost = components.host
        else {
            throw EnrollmentAudienceOriginError.malformed
        }
        guard scheme == "http" || scheme == "https" else {
            throw EnrollmentAudienceOriginError.unsupportedScheme(scheme)
        }
        guard components.user == nil, components.password == nil else {
            throw EnrollmentAudienceOriginError.credentialsNotAllowed
        }
        guard components.path.isEmpty, components.query == nil, components.fragment == nil else {
            throw EnrollmentAudienceOriginError.malformed
        }
        let host = rawHost.trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        guard !host.isEmpty, !host.contains("%"), !host.hasSuffix(".") else {
            throw EnrollmentAudienceOriginError.invalidHost(rawHost)
        }
        let canonicalHost: String
        if host.contains(":") {
            guard let address = EnrollmentCoding.ipv6Address(host) else {
                throw EnrollmentAudienceOriginError.invalidHost(rawHost)
            }
            if address.ipv4Mapped {
                throw EnrollmentAudienceOriginError.ipv4MappedNotAllowed
            }
            canonicalHost = "[\(address.compressed)]"
        } else if let ipv4 = EnrollmentCoding.ipv4Address(host) {
            canonicalHost = ipv4
        } else {
            guard EnrollmentAudienceOrigin.isCanonicalHostname(host) else {
                throw EnrollmentAudienceOriginError.invalidHost(rawHost)
            }
            canonicalHost = host
        }
        let port = components.port
        if let port {
            let defaultPort = scheme == "https" ? 443 : 80
            guard port != defaultPort else {
                throw EnrollmentAudienceOriginError.invalidPort(port)
            }
            guard (1...65_535).contains(port) else {
                throw EnrollmentAudienceOriginError.invalidPort(port)
            }
        }
        let portSuffix = port.map { ":\($0)" } ?? ""
        let canonical = "\(scheme)://\(canonicalHost)\(portSuffix)"
        guard trimmed == canonical else {
            throw EnrollmentAudienceOriginError.malformed
        }
        value = canonical
    }

    private static func isCanonicalHostname(_ host: String) -> Bool {
        if host == "localhost" { return true }
        let labels = host.split(separator: ".", omittingEmptySubsequences: false)
        guard !labels.isEmpty else { return false }
        return labels.allSatisfy { EnrollmentAudienceOrigin.isCanonicalLabel(String($0)) }
    }

    private static func isCanonicalLabel(_ label: String) -> Bool {
        let bytes = Array(label.utf8)
        guard (1...63).contains(bytes.count),
              isLowercaseAlphanumeric(bytes[0]),
              isLowercaseAlphanumeric(bytes[bytes.count - 1]),
              bytes.allSatisfy({ isLowercaseAlphanumeric($0) || $0 == 0x2d })
        else { return false }
        return true
    }

    private static func isLowercaseAlphanumeric(_ byte: UInt8) -> Bool {
        (0x61...0x7a).contains(byte) || (0x30...0x39).contains(byte)
    }
}

/// A manual local-console pairing code:
/// `JOEOS1|<canonical origin>|<offer UUID>|<32-byte canonical base32 secret>`.
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
        guard (80...400).contains(value.count) else {
            throw DeviceEnrollmentError.invalidPairingCode
        }
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
        let encodedSecret = String(segments[3])
        guard let secret = EnrollmentCoding.base32Decode(encodedSecret),
              secret.count == Self.secretByteCount,
              EnrollmentCoding.base32Encode(secret) == encodedSecret
        else {
            throw DeviceEnrollmentError.invalidPairingCode
        }
        self.pairingSecret = secret
        self.offerID = offerID
        self.audienceOrigin = origin
    }
}
