# Mobile client boundary

Owns the iPhone SwiftUI experience, connection-profile selection (authoritative development profile: JoeOS VPS at `100.98.25.26`), Keychain/Secure Enclave device identity, enrollment Face ID confirmation, and the future application-authentication, privileged-approval, push notification, offline cache, camera/microphone, and Even Reality G2 boundaries.

Current compatibility source: `../../JoeOSMobile.swift`, `../../JoeOSMobile-Info.plist`, and the root PWA.

The modular iOS 17 source set lives under `JoeOSClient/`, with its testable endpoint, bootstrap, enrollment-crypto, and exact-origin transport policy in `Sources/JoeOSCore/`. Native bootstrap validates the schema-v2 contract, then a separate reviewed ceremony creates two server-scoped Secure Enclave keys and persists only strict ThisDeviceOnly receipt/retry state. The manual secret remains memory-only, the server UUID alone grants no trust, and the resulting `active_unassigned` record grants no session, role, approval, route access, or execution authority. See `README.md` for Xcode creation and device-install steps and `FEATURES.md` for the implemented/deferred boundary. The root compatibility source remains untouched.

The first native security flow is device-key enrollment. Its biometric success signs only the exact verified enrollment envelope; it never grants a reusable shell, model permission, application session, or privileged approval.
