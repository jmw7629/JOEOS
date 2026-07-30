# JoeOS applications

This directory establishes the stable client boundary without moving the working compatibility files before history is unified.

| Target boundary | Current source of truth | Migration rule |
|---|---|---|
| `apps/desktop` | root `JoeOSClient.swift` and plist | Preserve the wrapper until a real Xcode target has configurable enrollment, Keychain storage, navigation/error state, and parity tests |
| `apps/mobile` | root `JoeOSMobile.swift` and plist plus PWA | iPhone remains the primary client; add native approvals and biometrics before privileged actions |
| `apps/web` | nested `joeos-web` repository | Preserve Sites metadata and history; replace the iframe incrementally after SDK/auth/data parity |

Root entry points remain supported during migration. No client is allowed to connect directly to Lemonade, Ollama, a shell, or a runner.
