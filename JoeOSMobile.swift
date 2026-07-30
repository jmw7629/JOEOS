import SwiftUI
import WebKit

struct JoeOSWebView: UIViewRepresentable {
    private let url = URL(string: "http://100.121.165.22:8080")!

    func makeUIView(context: Context) -> WKWebView {
        let webView = WKWebView()
        webView.isOpaque = false
        webView.backgroundColor = UIColor(red: 0.02, green: 0.04, blue: 0.08, alpha: 1)
        webView.scrollView.backgroundColor = webView.backgroundColor
        webView.allowsBackForwardNavigationGestures = true
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {}
}

@main struct JoeOSMobileApp: App {
    var body: some Scene {
        WindowGroup {
            NavigationStack {
                JoeOSWebView()
                    .ignoresSafeArea(edges: .bottom)
                    .navigationTitle("JoeOS Client")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbarBackground(Color(red: 0.02, green: 0.04, blue: 0.08), for: .navigationBar)
                    .toolbarBackground(.visible, for: .navigationBar)
                    .toolbarColorScheme(.dark, for: .navigationBar)
                    .toolbar {
                        ToolbarItem(placement: .navigationBarLeading) {
                            Label("LIVE", systemImage: "circle.fill")
                                .font(.caption2.bold()).foregroundStyle(.cyan)
                        }
                    }
            }
            .preferredColorScheme(.dark)
        }
    }
}
