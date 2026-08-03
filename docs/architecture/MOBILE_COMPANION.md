# JoeOS Mobile Companion and Secure Remote Operations Platform

Phase 14 delivers `server/mobile/`: the authoritative server-side Mobile
Companion platform, plus a native iOS companion policy module. The mobile
client is always a client of authoritative JoeOS services — it never accesses
core databases, secrets, arbitrary service methods, or unrestricted terminals.

## Platform strategy

The repository already contains a **native SwiftUI iOS application** at
`apps/mobile/JoeOSClient/` (iOS 17, Secure Enclave device enrollment, WebKit
same-origin transport, Keychain receipt/journal, and a Swift Package
`apps/mobile/Package.swift` with `JoeOSCore`). Per the phase rules, this phase
extends that existing native architecture rather than replacing it.

- **Client**: the native SwiftUI app consumes the Remote API below. A new
  `JoeOSCore` module, `MobileCompanionPolicy.swift`, enforces the same
  allowlist, offline-safety, deep-link, and app-lock boundaries locally.
- **Server**: `server/mobile/` is the authoritative Mobile Companion platform
  the client talks to. It is fully testable in this repository's Python test
  stack and is wired into the backend, bootstrap discovery, and Command
  Center.
- **No second backend, no duplicate source of truth.** Mobile screens render
  scoped server state; the client is never authoritative.
- **Honest platform status.** No Swift toolchain is installed on this machine
  (as the repo README documents), so the iOS app cannot be compiled or run
  here. Production APNs/FCM push, background execution, biometric security,
  and App Store readiness are **not** claimed as implemented; contracts and an
  isolated test fixture are provided, and the remaining platform configuration
  is documented.

## Architecture

```text
server/mobile/
├── models.py          typed contracts (clients, hosts, pairing, sessions, permissions, offline, handoff, deep links, push)
├── storage.py         versioned SQLite registry (mobile.db)
├── clients.py         Mobile Client Registry + Host Registry + discovery
├── security.py        Pairing Coordinator + Authentication + Secure Sessions
├── remote.py          Scoped Remote API + Remote Command Gateway (allowlist)
├── offline.py         Offline Action Queue + Handoff Coordinator + Deep-Link Registry
├── push.py            provider-neutral push contracts + privacy-safe delivery + test fixture
├── service.py         MobileService facade
└── router.py          REST API under /api/v1/mobile/*
```

## Security model

- **Explicit pairing.** Pairing uses short-lived, single-use, rate-limited
  codes confirmed on both the trusted host and the mobile client. Codes are
  SHA-256 hashed at rest; the plaintext is shown once on the host operator
  surface and never logged or stored.
- **Short-lived sessions.** Sessions expire (default 8h), support renewal,
  and are revocable server-side immediately. Refresh credentials rotate, are
  hashed at rest, and are independently revocable. No permanent bearer tokens;
  credentials never appear in URLs, logs, or payloads.
- **Allowlisted remote commands.** The Remote Command Gateway accepts only a
  typed allowlist and rejects prohibited operations (`shell_execute`,
  `git_push`, `grant_permission`, ...). Raw AI output is never executed.
- **Safe offline actions.** Only safe, idempotent actions can be queued
  offline; high-risk actions are prohibited. Queued actions are revalidated
  against authoritative state on reconnect; version mismatches produce
  conflict review instead of blind overwrite.
- **Privacy-safe push.** Push payloads default to minimal content
  ("JoeOS needs your attention."). Production delivery is not fabricated.
- **Deep links** are opaque, short-lived, user-bound, single-use references
  that open a review screen and never execute an action.
- **Revocation & lost-device** stop server-side access immediately
  (sessions, refresh credentials, push) and honestly do not claim an
  operating-system remote wipe.

## Offline and synchronization

The offline queue stores safe operations with idempotency keys, base-version
tracking, and conflict policies (`keep_authoritative`). Revalidation reports
replayed, conflicted, and discarded operations. Handoff moves work between
surfaces without duplicating action or state, using trusted-destination
checks. The client never holds authoritative state; it renders scoped server
queries.

## Integrations

- **Command Center**: `mobile.companion` health signal plus the desktop Mobile
  Companion section showing real paired-client, session, and revocation state.
- **Scoped queries**: the mobile Remote API reads real authoritative state via
  registered providers (`command_center`, `projects`, `missions`, `runtime`).
- **Communications / Automation / Wearables**: the companion remains a client;
  it does not duplicate inboxes, workflow engines, or device registries.

## Known limitations

- The native iOS app is not compiled or signed here (no Swift toolchain /
  Xcode on this machine, matching the repo's documented reality).
- Production APNs/FCM push delivery, background execution guarantees,
  biometric security, universal links, and App Store distribution require
  platform credentials and entitlements that are not configured; they are
  documented, not claimed.
- Camera/microphone/share-sheet behavior is governed by the server-side
  Upload gateway and mobile permission model; native capture is future work.
