# JoeOS Smart Glasses, Wearable Display, and Ambient Computing Platform

Phase 13 delivers `server/wearables/`: a local-first, provider-neutral
wearable integration layer that makes JoeOS usable through lightweight
wearable interfaces while preserving JoeOS as the authoritative OS. No
production hardware is fabricated — only an isolated simulator produces
devices, and no manufacturer (Even Realities, Meta, Apple, Android XR, etc.)
is claimed as supported without a real adapter.

## Principles

- **Glanceability.** Cards are concise, prioritized, and device-adaptive;
  a one-line monochrome device never receives the desktop layout.
- **Minimum disclosure.** Privacy modes (titles-only, minimal preview,
  public-environment, emergency-only) hide contact/project/message detail.
- **Explicit pairing.** No device accesses JoeOS without a completed pairing
  with a single-use, expiring, rate-limited code.
- **Revocable trust.** Trust is capability-scoped and revoked immediately;
  revocation terminates the active session and blocks reconnection.
- **Capability negotiation.** Capabilities are negotiated per connection and
  never inferred from device type; the adapter is the authority.
- **No voice equals authority.** Voice is push-to-talk, permission-gated,
  local-first, with a visible recording indicator; high-risk intents escalate
  to a stronger surface.
- **No camera equals action.** Capture is explicit, permission-gated, with an
  enforced recording indicator; vision output never authorizes an action.
- **Safe fallback.** Offline operations are idempotent and revalidated
  against authoritative state on reconnect.
- **Battery/thermal awareness.** Resource policies react to reported state
  without inventing data.

## Architecture

```text
server/wearables/
├── models.py          typed contracts (devices, capabilities, sessions, cards, voice, camera, checklists, handoff, offline)
├── storage.py         versioned SQLite registry (wearables.db)
├── devices.py         Device Registry + Adapter Registry + Discovery (explicit, non-continuous)
├── permissions.py     granular device permissions (camera/mic/location never default)
├── security.py        pairing (single-use codes), trust, authentication, secure sessions
├── connections.py     Connection Manager (bounded backoff) + Capability Negotiation
├── content.py         Glance Card system + Wearable Notification Router + privacy modes
├── interaction.py     normalized input events + allowlisted command gateway + confirmation levels
├── voice_camera.py    Voice gateway (push-to-talk, local-first) + Camera/Vision gateways + recording indicators
├── experiences.py     checklists, handoff, offline queue, resource governor
├── simulator.py       isolated Wearable Simulator (deterministic fixtures, never production state)
├── service.py         WearableService facade
└── router.py          REST API under /api/v1/wearables/*
```

## Security model

- **Pairing** codes are SHA-256 hashed, single-use, expire in 120s, are
  rate-limited, and never logged or stored in plain text. Simulator devices
  use a deterministic fixture code.
- **Trust** is capability-scoped (`session_trusted`, `capability_scoped`,
  `project_scoped`, `paired_but_restricted`, `revoked`) and never a single
  "trusted device" switch.
- **Sessions** authenticate (nonce challenge/response, replay-protected),
  expire after 8 hours, support heartbeat, and terminate on revocation.
- **Permissions** are granular; camera, microphone, location, and
  private-content are never granted by default.
- **Commands** are allowlisted. High-risk commands (`cancel_task`,
  `external_send`, `deploy`, ...) require deliberate confirmation and
  escalate to desktop/companion rather than accepting an ambiguous gesture.
- **Recording indicators** (mic_active/camera_active) are enforced at the
  service layer; adapters and plugins cannot hide them.
- **Prompt injection** resistance: QR/visual/voice content is data, never
  authority; voice transcripts cannot grant permissions or execute commands.

## Integration

- **Command Center**: `wearables.platform` health signal.
- **Communications**: wearable cards carry source, severity, priority, read
  and acknowledgement state; no competing inbox.
- **Automation**: cards/notifications can be delivered to wearables through
  the router; routine items are never mirrored wholesale.
- **Plugin Platform**: adapters are contributed through the Adapter Registry
  with capability declarations; no adapter accesses core services directly.

## Known limitations

- Only the simulator produces devices; no real manufacturer adapter ships and
  none is claimed. Bluetooth/USB/gaze/gesture support is architecture only.
- Camera and microphone use real fixtures only in tests; no production
  capture is performed.
- Secure transport is modeled as an encrypted session contract; real BLE/USB
  transport and OS-level pairing remain future work.
