# JoeOS Client for iOS 17

This directory contains the production-oriented SwiftUI source set for the next JoeOS iPhone client. It is intentionally isolated from the compatibility wrapper at `../../JoeOSMobile.swift`; that root file remains unchanged.

## Included

- Multiple editable connection profiles, with the authoritative development profile `JoeOS VPS` at `100.98.25.26` as the default. It is the only retained development profile.
- Production connection-profile system: profile list persistence, selected-profile persistence, last-successful profile, validation before activation, duplicate detection, migration from the legacy endpoint-format profiles, and automatic reconnect to the last successful profile.
- Profile fields: UUID, display name, protocol, host, port (discovered from the backend when not set), HTTPS-required, development/production flag, notes, optional API version, and last-connected/last-successful timestamps. Profiles never store API keys, passwords, tokens, or private keys.
- Non-secret profile persistence through SwiftUI `AppStorage`/`UserDefaults` (same keys as `JoeOSCore.ConnectionProfileStorage`).
- HTTPS for any valid host; HTTP only for loopback, RFC 1918, link-local, `.local`, IPv6 unique-local/link-local, and Tailscale's `100.64.0.0/10` range.
- Rejection of URL credentials, query strings, fragments, unsupported schemes, wildcard/public HTTP hosts, and malformed endpoints.
- One shared backend contract (`JoeOSCore.JoeOSBackendClient`) consumed by the iPhone, iPad, Mac, desktop, and browser clients. Every request URL is derived from the validated profile, so moving the backend to HTTPS is a profile change with zero source changes.
- Same-origin WebKit navigation. User-tapped external HTTP(S) links open in Safari; cross-origin redirects and custom schemes are blocked.
- Non-blocking native discovery through the exact same-origin `/api/v1/bootstrap` path, with a strict 64 KiB response ceiling, five-second timeout, HTTP/content-type checks, redirect refusal, unknown-field rejection, and schema-v2 semantic contract validation.
- Strict consumption of the schema-v2 local-console pairing advertisement: its fixed protocol/crypto profile, both enrollment POST routes, and the `identity.device_enrollment` capability are accepted only when they exactly match the server contract and grant no authority.
- Validated display of the observed JoeOS product/version and its reported local-first security limitations. The installation UUID is informational and is never accepted as a trust credential.
- A native local-console pairing sheet that checks the manual code against the exact active origin, verifies the complete server transcript before signing, and presents the bound server, origin, key fingerprints, and expiry for explicit review.
- Two server-scoped, non-exportable P-256 Secure Enclave keys: a device-authentication key and a separate approval key protected by the current Face ID enrollment.
- A phased `prepare` → review → `confirm` → `complete` client API. Face ID and signing occur only after the user confirms the verified review; no browser content can invoke that point.
- Non-synchronizing ThisDeviceOnly Keychain storage for the stable client UUID, validated receipt, and exact signed completion journal. Completion is journaled before networking and can be retried byte-for-byte with the same idempotency key after a timeout or restart.
- Strict recovery behavior: a pending journal takes precedence over an older receipt, unrelated or invalid state is preserved for explicit discard, and only a matching validated receipt clears a completed journal.
- Honest web-only fallback when an older server has no bootstrap endpoint or discovery is temporarily unavailable. Discovery never delays or disables the WKWebView.
- Observable idle/loading/online/offline/error state, estimated progress, retry, toolbar refresh, and WebKit pull-to-refresh.
- Dark native JoeOS chrome, local transport status, settings, privacy copy, and a reused JoeOS SVG mark.
- Pure Swift unit tests for endpoint, origin, navigation, and profile-codec policy.

Pairing is real, but its only result is `active_unassigned`. It does not create an application session, assign a user or role, approve a privileged action, perform a live revocation check, or grant execution authority. Push, voice, camera, and privileged runner behavior remain absent. The client never connects directly to Lemonade, Ollama, a shell, or MCP.

## Source layout

```text
Package.swift                         Testable local package manifest
Sources/JoeOSCore/                    Profile/origin, connection manager,
                                      bootstrap discovery, enrollment crypto,
                                      and the shared backend client
Sources/JoeOSIntelligence/            Executive intelligence layer: provider and
                                      model registries, execution router,
                                      conversation engine, agent fabric,
                                      executive council, memory, task graph,
                                      approvals, diagnostics
Tests/JoeOSCoreTests/                 XCTest connection/profile/endpoint policy
Tests/JoeOSIntelligenceTests/         XCTest agent/routing/conversation tests
JoeOSClient/App/                      SwiftUI application entry point
JoeOSClient/State/                    Application coordinator, session manager,
                                      browser/enrollment coordinators, stores,
                                      offline cache, synchronization engine
JoeOSClient/Views/                    Command chrome, connections, pairing review
JoeOSClient/Web/                      WKWebView and navigation delegates
JoeOSClient/Resources/                Info.plist and asset catalog
```

## Create the Xcode project

Full Xcode is not installed on this machine, so the `.xcodeproj`, signing assets, simulator build, and device archive cannot be generated or verified here. The command-line Swift compiler is available and is used for package tests and parser checks.

Use Xcode 15 or newer:

1. Open Xcode and choose **File → New → Project → iOS → App**.
2. Use:
   - Product Name: `JoeOSClient`
   - Team: your Apple Developer team
   - Organization Identifier: an identifier you control, for example `com.yourcompany`
   - Interface: `SwiftUI`
   - Language: `Swift`
   - Testing System: `XCTest`
   - Storage: none
3. Save the project at `apps/mobile/Xcode/JoeOSClient`. Create the `Xcode` directory when Xcode asks for the destination.
4. In the new app target, set **iOS Deployment Target** to `17.0`.
5. Delete the generated app entry file and `ContentView.swift` references from the app target. Do not delete any source in this repository.
6. Choose **File → Add Package Dependencies… → Add Local…**, select `apps/mobile`, and add the `JoeOSCore` product to the `JoeOSClient` app target.
7. Choose **File → Add Files to “JoeOSClient”…** and select these folders:
   - `apps/mobile/JoeOSClient/App`
   - `apps/mobile/JoeOSClient/State`
   - `apps/mobile/JoeOSClient/Views`
   - `apps/mobile/JoeOSClient/Web`
   - `apps/mobile/JoeOSClient/Resources/Assets.xcassets`
8. In the file-add sheet, leave **Copy items if needed** off, choose **Create groups**, and check only the `JoeOSClient` app target.
9. In the app target's Build Settings:
   - Set **Generate Info.plist File** to `No`.
   - Set **Info.plist File** to `../../JoeOSClient/Resources/Info.plist` when the project is stored at the path in step 3.
   - Set **Asset Catalog Compiler – Global Accent Color Name** to `AccentColor`.
   - Leave **Primary App Icon Set Name** empty until a real App Store icon is supplied.
10. In **Signing & Capabilities**, enable automatic signing, choose your team, and set a unique bundle identifier such as `com.yourcompany.joeosclient`. No entitlements or background modes are required by this source set.
11. Set Marketing Version to `1.0` and Current Project Version to `1`.
12. Build once for an iOS 17 simulator. Then connect an iPhone running iOS 17 or later, select it as the run destination, approve Developer Mode/trust prompts if required, and run the app.
13. Connect the iPhone to the same Tailscale tailnet or private network as the JoeOS VPS. Accept the local-network prompt. The default profile opens the JoeOS VPS; use the gear button to edit it or add an HTTPS profile.

For distribution, create a proper 1024×1024 opaque PNG app icon based on the reused `JoeOSMark` SVG and add a standard `AppIcon` set. The repository deliberately does not masquerade the SVG as a valid App Store icon.

## ATS and local-network rationale

`Info.plist` keeps general arbitrary loads disabled and enables only:

- `NSAllowsLocalNetworking`, for explicitly selected local endpoints.
- `NSAllowsArbitraryLoadsInWebContent`, because ATS exception domains cannot reliably express raw RFC 1918 or Tailscale IP ranges for WKWebView.

The Swift `EndpointPolicy` is therefore the enforcing boundary before a URL reaches WebKit. It allows public hosts only over HTTPS. For an App Store release, serve JoeOS through private HTTPS/Tailscale Serve and remove `NSAllowsArbitraryLoadsInWebContent` after confirming no supported profile depends on HTTP.

Bootstrap discovery does not broaden that boundary. The client discards any configured path and derives only `/api/v1/bootstrap` while preserving the already validated scheme, host, and port. The live URL session is ephemeral, carries no cookies, refuses redirects, enforces a streaming response limit, and requires JSON with HTTP 200. A valid schema-v2 document proves only that the response matches the advertised same-origin JoeOS contract. Its UUID is not a trust credential. Enrollment proceeds only through the separate native ceremony, whose challenge and completion transports are also ephemeral, cookie-free, redirect-free, exact-origin, bounded, and unavailable to the WKWebView.

No Bonjour service list is declared because this version does not browse or scan the LAN. Camera, microphone, contacts, calendars, and notification permissions are intentionally absent. `NSFaceIDUsageDescription` is limited to the explicit, reviewed enrollment approval-key signature.

## Local verification

From the repository root:

```bash
cd apps/mobile
swift test
find JoeOSClient Sources -name '*.swift' -print0 | xargs -0 swiftc -parse
plutil -lint JoeOSClient/Resources/Info.plist
```

The parser command validates syntax only. A real iOS SDK build, asset compilation, signing check, and device install still require full Xcode.

On the current development Mac, the `JoeOSCore` target builds with Swift 6.1.2, every Swift source parses, the property list and asset JSON validate, and a compiled smoke harness passes the HTTPS/private-network policy, IPv4/IPv6/Tailscale boundaries, navigation handoff, and profile round trip. Apple Command Line Tools on this Mac does not include the `XCTest` module, so `swift test` cannot load the checked-in XCTest target here; run that suite once after installing full Xcode.

## Mac / Xcode handoff (exact commands)

```bash
git clone https://github.com/jmw7629/JOEOS.git
cd JOEOS
git checkout ai-rebuild
cd apps/mobile
# Resolve packages (JoeOSCore + JoeOSIntelligence + both test targets)
swift package resolve
# Swift package checks (syntax + types on Apple hosts)
swift build --target JoeOSCore
swift build --target JoeOSIntelligence
swift test --target JoeOSCoreTests
swift test --target JoeOSIntelligenceTests
```

Then create the Xcode app project per the steps above (Product Name `JoeOSClient`), add the local package, add the `JoeOSClient` folders, and build for the iOS 17 simulator. No `.xcodeproj` is committed; the package manifest plus the source folders are the project-generation definition, and the `JoeOSClient/Resources/Info.plist` is the app plist. No signing certificates, provisioning profiles, or `xcuserdata` are committed.
