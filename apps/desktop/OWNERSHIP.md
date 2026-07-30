# Desktop client boundary

Owns macOS SwiftUI presentation, enrolled-node discovery, Keychain-backed connection profiles, native notifications, file pickers, window/dock behavior, biometric approval challenges, and accessibility.

Current compatibility source: `../../JoeOSClient.swift` and `../../JoeOSClient-Info.plist`.

The WebView consumes the same authenticated SDK contracts as the PWA. Native code supplies device identity and approval signatures through a narrow message bridge; arbitrary JavaScript never receives Keychain material.
