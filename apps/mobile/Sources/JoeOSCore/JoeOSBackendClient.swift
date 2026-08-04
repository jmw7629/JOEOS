import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum BackendClientError: Error, Equatable, LocalizedError, Sendable {
    case invalidEndpoint
    case cannotBuildURL
    case invalidResponse
    case unexpectedStatus(Int)
    case decodingFailed
    case cancelled

    public var errorDescription: String? {
        switch self {
        case .invalidEndpoint:
            "The selected connection is not valid."
        case .cannotBuildURL:
            "The request URL could not be derived from this connection."
        case .invalidResponse:
            "JoeOS returned a response that could not be consumed."
        case .unexpectedStatus(let status):
            "JoeOS returned HTTP \(status)."
        case .decodingFailed:
            "JoeOS returned data that did not match the contract."
        case .cancelled:
            "The request was cancelled."
        }
    }
}

public struct BackendHTTPResponse: Sendable {
    public let finalURL: URL
    public let statusCode: Int
    public let headers: [String: String]
    public let body: Data
}

public protocol BackendHTTPTransport: Sendable {
    func send(_ request: URLRequest, maximumResponseBytes: Int) async throws -> BackendHTTPResponse
}

/// Ephemeral, cookie-free, redirect-refusing transport shared by every JoeOS
/// client. All requests are built from the validated connection profile, so
/// moving the backend to HTTPS is a profile change, not a source change.
public final class URLSessionBackendTransport: BackendHTTPTransport, @unchecked Sendable {
    private let session: URLSession
    private let delegate: BackendNoRedirectDelegate

    public init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        configuration.waitsForConnectivity = false
        let delegate = BackendNoRedirectDelegate()
        self.delegate = delegate
        session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: nil)
    }

    public func send(_ request: URLRequest, maximumResponseBytes: Int) async throws -> BackendHTTPResponse {
        let (bytes, rawResponse) = try await session.bytes(for: request)
        guard let response = rawResponse as? HTTPURLResponse,
              let finalURL = response.url
        else {
            throw BackendClientError.invalidResponse
        }
        var body = Data()
        for try await byte in bytes {
            guard body.count < maximumResponseBytes else {
                throw BackendClientError.invalidResponse
            }
            body.append(byte)
        }
        let headers = response.allHeaderFields.reduce(into: [String: String]()) { result, item in
            guard let key = item.key as? String else { return }
            result[key] = String(describing: item.value)
        }
        return BackendHTTPResponse(
            finalURL: finalURL,
            statusCode: response.statusCode,
            headers: headers,
            body: body
        )
    }
}

private final class BackendNoRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
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

/// The one backend client contract used by the connection layer, the stores,
/// the intelligence layer, and every future JoeOS client. It only ever talks
/// to the selected JoeOS origin.
public struct JoeOSBackendClient: Sendable {
    public static let defaultMaximumResponseBytes = 262_144

    private let transport: any BackendHTTPTransport
    private let maximumResponseBytes: Int
    private let timeout: TimeInterval

    public init(
        transport: any BackendHTTPTransport = URLSessionBackendTransport(),
        maximumResponseBytes: Int = defaultMaximumResponseBytes,
        timeout: TimeInterval = 15
    ) {
        self.transport = transport
        self.maximumResponseBytes = max(1_024, min(maximumResponseBytes, 2_621_440))
        self.timeout = max(1, min(timeout, 60))
    }

    /// Builds a same-origin URL for the given path on the validated endpoint.
    /// The scheme and port always come from the profile, so HTTPS requires no
    /// source change.
    public func url(endpoint: ValidatedEndpoint, path: String) throws -> URL {
        guard var components = URLComponents(url: endpoint.url, resolvingAgainstBaseURL: false) else {
            throw BackendClientError.cannotBuildURL
        }
        guard path.hasPrefix("/") else {
            throw BackendClientError.cannotBuildURL
        }
        components.path = path
        components.query = nil
        components.fragment = nil
        guard let url = components.url,
              EndpointOrigin(url: url) == endpoint.origin
        else {
            throw BackendClientError.cannotBuildURL
        }
        return url
    }

    public func get<Response: Decodable & Sendable>(
        _ type: Response.Type,
        path: String,
        endpoint: ValidatedEndpoint
    ) async throws -> Response {
        var request = URLRequest(
            url: try url(endpoint: endpoint, path: path),
            cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
            timeoutInterval: timeout
        )
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
        return try await perform(request)
    }

    public func post<Body: Encodable & Sendable, Response: Decodable & Sendable>(
        body: Body,
        to path: String,
        endpoint: ValidatedEndpoint
    ) async throws -> Response {
        var request = URLRequest(
            url: try url(endpoint: endpoint, path: path),
            cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
            timeoutInterval: timeout
        )
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(body)
        return try await perform(request)
    }

    public func put<Body: Encodable & Sendable, Response: Decodable & Sendable>(
        body: Body,
        to path: String,
        endpoint: ValidatedEndpoint
    ) async throws -> Response {
        var request = URLRequest(
            url: try url(endpoint: endpoint, path: path),
            cachePolicy: .reloadIgnoringLocalAndRemoteCacheData,
            timeoutInterval: timeout
        )
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONEncoder().encode(body)
        return try await perform(request)
    }

    private func perform<Response: Decodable & Sendable>(
        _ request: URLRequest
    ) async throws -> Response {
        let response = try await transport.send(request, maximumResponseBytes: maximumResponseBytes)
        guard response.statusCode == 200 else {
            throw BackendClientError.unexpectedStatus(response.statusCode)
        }
        guard !response.body.isEmpty else {
            throw BackendClientError.invalidResponse
        }
        do {
            return try JSONDecoder().decode(Response.self, from: response.body)
        } catch {
            throw BackendClientError.decodingFailed
        }
    }
}
