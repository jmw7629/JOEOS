import SwiftUI

@main
struct JoeOSMobileApp: App {
    var body: some Scene {
        WindowGroup {
            CommandCenterView()
                .preferredColorScheme(.dark)
        }
    }
}
