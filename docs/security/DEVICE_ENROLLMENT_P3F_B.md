# JoeOS P3F-B Checklist — Native Apple Client Signing and Physical-Device Security Drill

Status: **not started** (remote validation of P3F-A is complete; this checklist defines the
remaining on-device work that cannot be validated from the VPS).

## Purpose

P3F-A built and validated the native Apple client from the authoritative VPS over Tailscale
(Swift package, Xcode project, simulator build/tests, device Debug/Release builds, and an
archive). That proves the source set compiles and links for simulator and `arm64` device, and
that 73/73 unit tests pass under the Xcode test bundle.

P3F-B closes the remaining gap before distribution: a real signing pipeline and the
physical-device security drill matrix required by
[Device Enrollment Security Protocol](DEVICE_ENROLLMENT.md#remaining-security-gates) and
`README.md`. These items require an Apple Developer account, full Xcode on a Mac with the
physical iPhone attached, Face ID, and the live JoeOS VPS. They are not verifiable remotely and
are deliberately not claimed as done.

## Rules

- No item is marked complete until it is demonstrated on the actual artifact and, where
  relevant, the live VPS.
- Items blocked by account/hardware are recorded as `PENDING` with the exact blocker; they are
  never re-labeled as passing.
- Every drill that touches enrollment must be run against the live VPS pairing ceremony
  (`pair_joeos_iphone.command`), never a fixture.

---

## 1. Signing and distribution pipeline

- [ ] **PENDING** Apple Developer team configured in `apps/mobile/Config/Local.xcconfig`
      (`JOEOS_DEVELOPMENT_TEAM`), overrides the gitignored default in `Base.xcconfig`.
- [ ] **PENDING** Automatic signing succeeds for the `JoeOSClient` app target on a personal or
      paid team with a unique bundle identifier (default `com.joeos.client`).
- [ ] **PENDING** No extraneous entitlements: the signing identity grants only what the source
      uses (Keychain-access-groups for `ThisDeviceOnly` Keychain; `NSFaceIDUsageDescription`
      usage string already present in `Info.plist`). No push, camera, microphone, contacts,
      calendar, or notification entitlements are added.
- [ ] **PENDING** `Export for Development` produces a signed `.ipa`; device install succeeds on
      a physical iPhone (iOS 17 or later).
- [ ] **PENDING** `Export for App Store` (or TestFlight upload) succeeds and the archive passes
      validation with the signing identity in place.
- [ ] **PENDING** A real 1024×1024 opaque `AppIcon` set replaces the empty `AppIcon` slot and is
      the only asserted icon source. The repository does not masquerade the SVG as a valid icon.
- [ ] **DONE (P3F-A)** `Config/Base.xcconfig` provides `PRODUCT_NAME`, versioning, and bundle
      identifier defaults; `Local.xcconfig` is gitignored so team identity never leaks.

## 2. Physical-device pairing drill

Objective: prove the implemented `prepare → review → confirm → complete` ceremony binds two
P-256 keys on a real Secure Enclave, behind Face ID, to the live VPS.

- [ ] **PENDING** iPhone runs on the same Tailscale tailnet / private network as the JoeOS VPS.
- [ ] **PENDING** `pair_joeos_iphone.command` issues a manual code for the exact private origin;
      the client rejects a mismatched origin before signing.
- [ ] **PENDING** Review step displays the bound server UUID, exact origin, device UUID, both key
      fingerprints, and expiry; the user confirms before any Face ID prompt.
- [ ] **PENDING** Approval-key signature requires the current Face ID enrollment (Secure Enclave
      P-256, non-exportable, purpose-separated from the authentication key).
- [ ] **PENDING** Completion posts with an idempotency UUID; the VPS records
      `active_unassigned`, immutable public keys, and append-only identity events.
- [ ] **PENDING** `server.identity.cli list` shows the new device with both fingerprints and no
      key material; audit events are present.
- [ ] **PENDING** A second phone entering the same offer's code fails (single-use, five-attempt
      lockout path exercised on the live server).

## 3. Biometric-change drill

Objective: prove the approval key survives Face ID enrollment changes with no silent bypass.

- [ ] **PENDING** Enroll, pair, then add a new Face; approve a step-up signature — Secure Enclave
      revalidation path behaves as documented.
- [ ] **PENDING** Reset Face ID; the approval-key signature fails closed (no alternate bypass),
      and the error is surfaced without exposing key material.
- [ ] **PENDING** After a failed biometric, the completion journal retries byte-for-byte with the
      same idempotency key (no re-sign, no second pairing secret) per the protocol.
- [ ] **PENDING** Documented behavior matches the real outcome; any divergence is a blocking bug
      report, not a doc edit.

## 4. Backup / recovery / restore drill

- [ ] **PENDING** With a completed pairing, run a verified JoeOS backup
      (`scripts/release.py` / Backup Coordinator path); hash + manifest verify.
- [ ] **PENDING** Restore to the same host; the security-reset hook revokes mobile sessions and
      invalidates pending approvals, so the paired device cannot re-enable stale authority.
- [ ] **PENDING** Re-run the pairing ceremony after restore; the phone completes as a fresh
      `active_unassigned` device or the documented mismatch is reported honestly.
- [ ] **PENDING** Confirm the pairing-key envelope at rest (AES-256-GCM under `identity-master.key`)
      does not leak into logs, support bundles, or the frontend.

## 5. Device replacement drill

- [ ] **PENDING** Pair on device A; revoke it with `server.identity.cli revoke <uuid>`.
- [ ] **PENDING** Device A can no longer complete or replay its receipt (revocation prevents
      receipt replay per protocol).
- [ ] **PENDING** Pair on device B with a fresh offer; device A's keys are deactivated and do not
      reappear.

## 6. Revocation-freshness drill

Objective: prove revocation changes live identity state and that no stale device key is treated
as valid on the wire.

- [ ] **PENDING** After revocation, an authenticated request using the revoked authentication key
      is rejected by the VPS (once request authentication exists) or is documented as
      `not_implemented` if it does not.
- [ ] **PENDING** A second revocation of the same device is a no-op and emits no new mutation.
- [ ] **PENDING** Revocation during an open challenge invalidates that challenge and its offer
      server-side.

## 7. Distribution and runtime hygiene

- [ ] **PENDING** On-device confirmation that the default profile opens the JoeOS VPS and WKWebView
      loads only the exact private origin; external HTTP(S) links hand off to Safari.
- [ ] **PENDING** ATS audit on device: private HTTPS/Tailscale Serve route recommended; HTTP only
      for loopback/RFC1918/link-local/`.local`/Tailscale `100.64.0.0/10` per `EndpointPolicy`.
- [ ] **PENDING** App Store privacy declarations list only the data the client actually sends
      (no analytics, no third-party SDKs, no tracking).
- [ ] **PENDING** `PLIST_LINT` and asset catalog validation pass under the signed build.

## 8. Honest limitations recorded for P3F-B

- [ ] **KNOWN** No passkey/OIDC sign-in, biometric step-up approvals, authenticated sessions,
      RBAC, or signed runner channel exist yet; the pairing receipt still grants no authority.
- [ ] **KNOWN** `active_unassigned` remains the only pairing outcome until Phase 3 identity/
      authorization lands.
- [ ] **KNOWN** App Store distribution (icon, privacy manifest, App Review) is out of scope until
      the signing + drill items above pass on a physical device.

---

## Verification trail from P3F-A (completed, for context)

- Swift package: `swift test` 73/73 on VPS and Mac mirror.
- Xcode project: `apps/mobile/Xcode/JoeOSClient.xcodeproj`, prebuilt-module approach
  (toolchain SwiftPM limitation documented), Simulator Debug build green.
- Test bundle: `xcodebuild test` 73/73 passed on iPhone 16 Pro simulator (iOS 18.6).
- Device Debug/Release: `ARCHS=arm64`, `CODE_SIGNING_ALLOWED=NO`, both green.
- Archive: `JoeOSClient.xcarchive` produced with `arm64` Mach-O and dSYM.
