# JoeOS Executive Operating System

JoeOS is a local-first executive command center for the AMD Ryzen AI Max+ 395 Halo, iPhone, and Mac. The current operational release keeps telemetry, workspace configuration, audit events, agent profiles, and Lemonade inference on the Halo and exposes one same-origin web application over the user's private network.

## Working now

- Mission Control home answering attention, safe AI delegation, blockers/risk, next action, and recent changes.
- Versioned widget catalog with movable, resizable, hide/show modules.
- Touch and keyboard alternatives for every drag layout operation.
- Persistent typography, accent, density, radius, and glass controls.
- Natural-language configuration proposals that require review and an explicit save.
- Live CPU, RAM, GPU, disk, uptime, Lemonade status, local events, and SQLite-backed profile state.
- Private Lemonade chat through FastAPI; the browser cannot execute a shell or access model ports directly.
- Installable mobile PWA plus a native SwiftUI iPhone client with strict private-origin discovery and device-key pairing.
- Server-side local-console device pairing with two distinct P-256 keys, encrypted pending-key storage, expiry/replay controls, local revocation, and an explicit no-authority `active_unassigned` state.

Start the current local release with `start_joeos.sh` on the Halo or `start_joeos.command` on macOS. For the recommended private HTTPS iPhone/PWA route, use `start_joeos_secure.sh` or double-click `start_joeos_secure.command`. See `DEPLOYMENT.md` for details.

The modular native iPhone source now runs the reviewed pairing ceremony with server-scoped Secure Enclave keys, Face ID protection for the approval-key signature, and ThisDeviceOnly Keychain recovery for an exact signed retry. A full Xcode build, signing setup, and physical-device security drill are still required before distribution. Pairing grants no application session, role, approval, API authorization, or execution permission. See [Device Enrollment Security Protocol](docs/security/DEVICE_ENROLLMENT.md) for the exact boundary.

## Architecture program

The complete audit, target topology, feature graph, security boundaries, and dependency-ordered backlog are in `docs/architecture/`. Existing root entry points remain supported while domain modules move under `server/`, shared clients and contracts move under `packages/`, and the nested Sites application is migrated without losing its Git history.

Unconnected calendar, email, weather, markets, manufacturing, social, documents, automation, Git, terminal, voice, vision, and Even Reality G2 modules are deliberately labeled as integration-required. JoeOS never substitutes fabricated production data.
