import Combine
import Foundation
import JoeOSCore

enum BrowserConnectionPhase: Equatable {
    case idle
    case loading
    case online
    case offline
    case error
}

enum BootstrapDiscoveryState: Equatable {
    case idle
    case checking
    case validated(ValidatedBootstrapContract)
    case legacyServer
    case unavailable
    case rejected
}

@MainActor
final class BrowserSession: ObservableObject {
    @Published private(set) var phase: BrowserConnectionPhase = .idle
    @Published private(set) var progress: Double = 0
    @Published private(set) var currentURL: URL?
    @Published private(set) var lastSuccessfulLoadAt: Date?
    @Published private(set) var errorMessage: String?
    @Published private(set) var policyNotice: String?
    @Published private(set) var bootstrapState: BootstrapDiscoveryState = .idle

    private let bootstrapDiscovery: any BootstrapDiscovering

    init(bootstrapDiscovery: any BootstrapDiscovering = BootstrapDiscoveryClient()) {
        self.bootstrapDiscovery = bootstrapDiscovery
    }

    var isLoading: Bool { phase == .loading }

    var statusTitle: String {
        switch phase {
        case .idle: "Ready"
        case .loading: "Connecting"
        case .online: "Online"
        case .offline: "Offline"
        case .error: "Attention"
        }
    }

    var statusDetail: String {
        switch phase {
        case .idle:
            "Select a Halo connection to begin."
        case .loading:
            "Opening the private JoeOS command center."
        case .online:
            "Connected directly to the selected JoeOS server."
        case .offline:
            errorMessage ?? "JoeOS is not reachable from this device."
        case .error:
            errorMessage ?? "JoeOS could not finish loading."
        }
    }

    func beginLoading(_ url: URL) {
        phase = .loading
        progress = 0.05
        currentURL = url
        errorMessage = nil
        policyNotice = nil
    }

    func updateProgress(_ value: Double) {
        progress = min(1, max(progress, value))
    }

    func finishLoading(_ url: URL?) {
        phase = .online
        progress = 1
        currentURL = url ?? currentURL
        lastSuccessfulLoadAt = Date()
        errorMessage = nil
    }

    func fail(_ error: Error) {
        let nsError = error as NSError
        guard nsError.domain != NSURLErrorDomain || nsError.code != NSURLErrorCancelled else {
            return
        }

        progress = 0
        if Self.offlineCodes.contains(nsError.code) {
            phase = .offline
            errorMessage = "JoeOS is unreachable. Confirm the Halo is online and this iPhone is connected to the same private network."
        } else if nsError.domain == NSURLErrorDomain,
                  nsError.code == NSURLErrorSecureConnectionFailed ||
                  nsError.code == NSURLErrorServerCertificateUntrusted ||
                  nsError.code == NSURLErrorServerCertificateHasBadDate ||
                  nsError.code == NSURLErrorServerCertificateHasUnknownRoot {
            phase = .error
            errorMessage = "The secure connection could not be verified. Check the JoeOS HTTPS certificate before retrying."
        } else if nsError.domain == NSURLErrorDomain,
                  nsError.code == NSURLErrorAppTransportSecurityRequiresSecureConnection {
            phase = .error
            errorMessage = "iOS blocked this insecure address. Use HTTPS, or choose an allowed private or Tailscale HTTP address."
        } else {
            phase = .error
            errorMessage = "JoeOS could not load. Review the connection profile and retry."
        }
    }

    func webContentProcessTerminated() {
        phase = .error
        progress = 0
        errorMessage = "The JoeOS web process stopped unexpectedly. Your server was not changed; reload to reconnect."
    }

    func recordBlockedNavigation(_ url: URL) {
        policyNotice = "A cross-origin redirect to \(url.host ?? "another site") was blocked. Only links you tap are opened outside JoeOS."
    }

    func clearPolicyNotice() {
        policyNotice = nil
    }

    func discoverBootstrap(from endpoint: ValidatedEndpoint?) async {
        guard let endpoint else {
            bootstrapState = .idle
            return
        }
        bootstrapState = .checking
        do {
            let contract = try await bootstrapDiscovery.discover(from: endpoint)
            try Task.checkCancellation()
            bootstrapState = .validated(contract)
        } catch is CancellationError {
            return
        } catch let error as BootstrapDiscoveryError {
            switch error {
            case .unexpectedStatus(404), .unexpectedStatus(405):
                bootstrapState = .legacyServer
            case .unexpectedResponseURL, .invalidPayload, .invalidContract,
                 .invalidContentType, .responseTooLarge, .emptyResponse,
                 .cannotDeriveSameOriginURL:
                bootstrapState = .rejected
            case .unexpectedStatus:
                bootstrapState = .unavailable
            }
        } catch {
            bootstrapState = .unavailable
        }
    }

    private static let offlineCodes: Set<Int> = [
        NSURLErrorNotConnectedToInternet,
        NSURLErrorNetworkConnectionLost,
        NSURLErrorCannotConnectToHost,
        NSURLErrorCannotFindHost,
        NSURLErrorTimedOut,
        NSURLErrorDNSLookupFailed,
    ]
}
