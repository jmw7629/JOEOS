import SwiftUI
import WebKit
import AppKit

struct CommandWebView: NSViewRepresentable {
    private let url = URL(string: "http://100.121.165.22:8080")!

    func makeNSView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.underPageBackgroundColor = NSColor(red: 0.02, green: 0.04, blue: 0.08, alpha: 1)
        webView.allowsBackForwardNavigationGestures = true
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {}
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.main.async {
            guard let window = NSApp.windows.first else { return }
            window.appearance = NSAppearance(named: .darkAqua)
            window.titlebarAppearsTransparent = true
            window.backgroundColor = NSColor(red: 0.02, green: 0.04, blue: 0.08, alpha: 1)
            window.minSize = NSSize(width: 760, height: 520)
        }
    }
}

@main struct JoeOSClientApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var delegate
    var body: some Scene {
        Window("JoeOS Client", id: "main") {
            CommandWebView().preferredColorScheme(.dark)
        }
        .defaultSize(width: 1200, height: 800)
        .windowStyle(.titleBar)
    }
}
