import JoeOSCore
import SwiftUI
import UIKit
import WebKit

struct JoeOSWebView: UIViewRepresentable {
    let endpoint: URL
    let reloadToken: UUID
    @ObservedObject var session: BrowserSession

    func makeCoordinator() -> Coordinator {
        Coordinator(parent: self)
    }

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = false

        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.allowsLinkPreview = true
        webView.isOpaque = false
        webView.backgroundColor = UIColor(red: 0.02, green: 0.035, blue: 0.065, alpha: 1)
        webView.scrollView.backgroundColor = webView.backgroundColor
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        webView.accessibilityLabel = "JoeOS command center"

        if #available(iOS 16.4, *) {
            webView.isInspectable = false
        }

        let refreshControl = UIRefreshControl()
        refreshControl.tintColor = UIColor(red: 0.20, green: 0.84, blue: 1, alpha: 1)
        refreshControl.addTarget(
            context.coordinator,
            action: #selector(Coordinator.pullToRefresh(_:)),
            for: .valueChanged
        )
        webView.scrollView.refreshControl = refreshControl

        context.coordinator.attach(to: webView)
        context.coordinator.load(endpoint, in: webView)
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        context.coordinator.parent = self
        context.coordinator.update(endpoint: endpoint, reloadToken: reloadToken, in: webView)
    }

    static func dismantleUIView(_ webView: WKWebView, coordinator: Coordinator) {
        coordinator.detach()
        webView.navigationDelegate = nil
        webView.uiDelegate = nil
        webView.stopLoading()
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        var parent: JoeOSWebView

        private weak var webView: WKWebView?
        private var progressObservation: NSKeyValueObservation?
        private var loadedEndpoint: URL?
        private var observedReloadToken: UUID

        init(parent: JoeOSWebView) {
            self.parent = parent
            observedReloadToken = parent.reloadToken
        }

        func attach(to webView: WKWebView) {
            self.webView = webView
            progressObservation = webView.observe(\.estimatedProgress, options: [.initial, .new]) { [weak self] webView, _ in
                Task { @MainActor [weak self] in
                    self?.parent.session.updateProgress(webView.estimatedProgress)
                }
            }
        }

        func detach() {
            progressObservation?.invalidate()
            progressObservation = nil
        }

        func update(endpoint: URL, reloadToken: UUID, in webView: WKWebView) {
            if loadedEndpoint != endpoint {
                load(endpoint, in: webView)
                observedReloadToken = reloadToken
                return
            }

            if observedReloadToken != reloadToken {
                observedReloadToken = reloadToken
                parent.session.beginLoading(endpoint)
                webView.reloadFromOrigin()
            }
        }

        func load(_ endpoint: URL, in webView: WKWebView) {
            loadedEndpoint = endpoint
            parent.session.beginLoading(endpoint)
            let request = URLRequest(
                url: endpoint,
                cachePolicy: .reloadRevalidatingCacheData,
                timeoutInterval: 30
            )
            webView.load(request)
        }

        @objc func pullToRefresh(_ sender: UIRefreshControl) {
            guard let webView else {
                sender.endRefreshing()
                return
            }
            parent.session.beginLoading(loadedEndpoint ?? parent.endpoint)
            webView.reloadFromOrigin()
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation?) {
            parent.session.beginLoading(webView.url ?? loadedEndpoint ?? parent.endpoint)
        }

        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation?) {
            webView.scrollView.refreshControl?.endRefreshing()
            parent.session.finishLoading(webView.url)
        }

        func webView(
            _ webView: WKWebView,
            didFailProvisionalNavigation navigation: WKNavigation?,
            withError error: Error
        ) {
            webView.scrollView.refreshControl?.endRefreshing()
            parent.session.fail(error)
        }

        func webView(
            _ webView: WKWebView,
            didFail navigation: WKNavigation?,
            withError error: Error
        ) {
            webView.scrollView.refreshControl?.endRefreshing()
            parent.session.fail(error)
        }

        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
            webView.scrollView.refreshControl?.endRefreshing()
            parent.session.webContentProcessTerminated()
        }

        func webView(
            _ webView: WKWebView,
            decidePolicyFor navigationAction: WKNavigationAction,
            decisionHandler: @escaping (WKNavigationActionPolicy) -> Void
        ) {
            guard let candidate = navigationAction.request.url else {
                decisionHandler(.cancel)
                return
            }

            let userInitiated = navigationAction.navigationType == .linkActivated
            switch EndpointPolicy.navigationDisposition(
                for: candidate,
                relativeTo: parent.endpoint,
                userInitiated: userInitiated
            ) {
            case .allowSameOrigin:
                decisionHandler(.allow)
            case .openExternally:
                UIApplication.shared.open(candidate, options: [:])
                decisionHandler(.cancel)
            case .block:
                parent.session.recordBlockedNavigation(candidate)
                decisionHandler(.cancel)
            }
        }

        func webView(
            _ webView: WKWebView,
            createWebViewWith configuration: WKWebViewConfiguration,
            for navigationAction: WKNavigationAction,
            windowFeatures: WKWindowFeatures
        ) -> WKWebView? {
            guard navigationAction.targetFrame == nil,
                  let candidate = navigationAction.request.url
            else {
                return nil
            }

            switch EndpointPolicy.navigationDisposition(
                for: candidate,
                relativeTo: parent.endpoint,
                userInitiated: navigationAction.navigationType == .linkActivated
            ) {
            case .allowSameOrigin:
                webView.load(navigationAction.request)
            case .openExternally:
                UIApplication.shared.open(candidate, options: [:])
            case .block:
                parent.session.recordBlockedNavigation(candidate)
            }
            return nil
        }
    }
}
