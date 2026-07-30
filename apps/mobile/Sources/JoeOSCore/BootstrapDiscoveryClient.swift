import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public struct BootstrapHTTPResponse: Sendable {
    public let finalURL: URL
    public let statusCode: Int
    public let headers: [String: String]
    public let body: Data

    public init(finalURL: URL, statusCode: Int, headers: [String: String], body: Data) {
        self.finalURL = finalURL
        self.statusCode = statusCode
        var normalizedHeaders: [String: String] = [:]
        for key in headers.keys.sorted() {
            guard let value = headers[key] else { continue }
            let normalizedKey = key.lowercased()
            if let existing = normalizedHeaders[normalizedKey] {
                normalizedHeaders[normalizedKey] = "\(existing), \(value)"
            } else {
                normalizedHeaders[normalizedKey] = value
            }
        }
        self.headers = normalizedHeaders
        self.body = body
    }

    public func header(named name: String) -> String? {
        headers[name.lowercased()]
    }
}

public protocol BootstrapHTTPTransport: Sendable {
    func send(_ request: URLRequest, maximumResponseBytes: Int) async throws -> BootstrapHTTPResponse
}

public protocol BootstrapDiscovering: Sendable {
    func discover(from endpoint: ValidatedEndpoint) async throws -> ValidatedBootstrapContract
}

public enum BootstrapDiscoveryError: Error, Equatable, LocalizedError, Sendable {
    case cannotDeriveSameOriginURL
    case unexpectedResponseURL
    case unexpectedStatus(Int)
    case invalidContentType(String?)
    case emptyResponse
    case responseTooLarge(Int)
    case invalidPayload
    case invalidContract(BootstrapContractViolation)

    public var errorDescription: String? {
        switch self {
        case .cannotDeriveSameOriginURL:
            "The bootstrap URL could not be derived safely from this connection."
        case .unexpectedResponseURL:
            "Bootstrap discovery did not finish at the exact expected same-origin path."
        case .unexpectedStatus(let status):
            "JoeOS bootstrap discovery returned HTTP \(status)."
        case .invalidContentType:
            "JoeOS bootstrap discovery did not return JSON."
        case .emptyResponse:
            "JoeOS bootstrap discovery returned an empty response."
        case .responseTooLarge:
            "JoeOS bootstrap discovery exceeded the response limit."
        case .invalidPayload:
            "JoeOS bootstrap discovery returned an invalid strict contract."
        case .invalidContract(let violation):
            violation.localizedDescription
        }
    }
}

public struct BootstrapDiscoveryClient: BootstrapDiscovering, Sendable {
    public static let defaultMaximumResponseBytes = 65_536
    public static let defaultTimeout: TimeInterval = 5

    private let transport: any BootstrapHTTPTransport
    private let maximumResponseBytes: Int
    private let timeout: TimeInterval

    public init(
        transport: any BootstrapHTTPTransport = URLSessionBootstrapTransport(),
        maximumResponseBytes: Int = defaultMaximumResponseBytes,
        timeout: TimeInterval = defaultTimeout
    ) {
        self.transport = transport
        self.maximumResponseBytes = max(1_024, min(maximumResponseBytes, 262_144))
        self.timeout = max(1, min(timeout, 15))
    }

    public func discover(from endpoint: ValidatedEndpoint) async throws -> ValidatedBootstrapContract {
        let bootstrapURL = try Self.bootstrapURL(from: endpoint)
        var request = URLRequest(
            url: bootstrapURL,
            cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
            timeoutInterval: timeout
        )
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")

        let response = try await transport.send(request, maximumResponseBytes: maximumResponseBytes)
        guard response.finalURL == bootstrapURL else {
            throw BootstrapDiscoveryError.unexpectedResponseURL
        }
        guard response.statusCode == 200 else {
            throw BootstrapDiscoveryError.unexpectedStatus(response.statusCode)
        }
        if let declaredLength = response.header(named: "content-length").flatMap(Int.init),
           declaredLength > maximumResponseBytes {
            throw BootstrapDiscoveryError.responseTooLarge(maximumResponseBytes)
        }
        guard !response.body.isEmpty else {
            throw BootstrapDiscoveryError.emptyResponse
        }
        guard response.body.count <= maximumResponseBytes else {
            throw BootstrapDiscoveryError.responseTooLarge(maximumResponseBytes)
        }
        let contentType = response.header(named: "content-type")
        guard Self.isJSONContentType(contentType) else {
            throw BootstrapDiscoveryError.invalidContentType(contentType)
        }

        let document: BootstrapDocument
        do {
            document = try JSONDecoder().decode(BootstrapDocument.self, from: response.body)
        } catch {
            throw BootstrapDiscoveryError.invalidPayload
        }
        do {
            return try BootstrapContractValidator.validate(document)
        } catch let violation as BootstrapContractViolation {
            throw BootstrapDiscoveryError.invalidContract(violation)
        }
    }

    public static func bootstrapURL(from endpoint: ValidatedEndpoint) throws -> URL {
        guard var components = URLComponents(url: endpoint.url, resolvingAgainstBaseURL: false) else {
            throw BootstrapDiscoveryError.cannotDeriveSameOriginURL
        }
        components.percentEncodedPath = "/api/v1/bootstrap"
        components.percentEncodedQuery = nil
        components.fragment = nil
        guard let url = components.url, EndpointOrigin(url: url) == endpoint.origin else {
            throw BootstrapDiscoveryError.cannotDeriveSameOriginURL
        }
        return url
    }

    private static func isJSONContentType(_ rawValue: String?) -> Bool {
        guard let mediaType = rawValue?.lowercased().split(separator: ";", maxSplits: 1).first?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        else {
            return false
        }
        return mediaType == "application/json" ||
            (mediaType.hasPrefix("application/") && mediaType.hasSuffix("+json"))
    }
}

public final class URLSessionBootstrapTransport: BootstrapHTTPTransport, @unchecked Sendable {
    private let session: URLSession
    private let redirectDelegate: NoRedirectDelegate

    public init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.waitsForConnectivity = false
        let delegate = NoRedirectDelegate()
        redirectDelegate = delegate
        session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
    }

    public func send(_ request: URLRequest, maximumResponseBytes: Int) async throws -> BootstrapHTTPResponse {
        let (bytes, rawResponse) = try await session.bytes(for: request)
        guard let response = rawResponse as? HTTPURLResponse,
              let finalURL = response.url
        else {
            throw BootstrapDiscoveryError.invalidPayload
        }
        if response.expectedContentLength > Int64(maximumResponseBytes) {
            throw BootstrapDiscoveryError.responseTooLarge(maximumResponseBytes)
        }

        var body = Data()
        body.reserveCapacity(min(maximumResponseBytes, max(0, Int(response.expectedContentLength))))
        for try await byte in bytes {
            guard body.count < maximumResponseBytes else {
                throw BootstrapDiscoveryError.responseTooLarge(maximumResponseBytes)
            }
            body.append(byte)
        }

        let headers = response.allHeaderFields.reduce(into: [String: String]()) { result, item in
            guard let key = item.key as? String else { return }
            result[key] = String(describing: item.value)
        }
        return BootstrapHTTPResponse(
            finalURL: finalURL,
            statusCode: response.statusCode,
            headers: headers,
            body: body
        )
    }
}

private final class NoRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}
