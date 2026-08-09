import SwiftUI

struct ContentView: View {
    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                Image(systemName: "bolt.circle.fill")
                    .font(.system(size: 72))
                    .foregroundStyle(.teal)
                Text("JoeOS SwiftUI")
                    .font(.largeTitle.bold())
                Text("Native SwiftUI client scaffold, built on the Mac build host via Xcode.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 24)
            }
            .padding()
            .navigationTitle("Home")
        }
    }
}

#Preview {
    ContentView()
}
