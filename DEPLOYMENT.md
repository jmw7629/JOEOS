# JoeOS Local — immediate use

JoeOS is now a single private service on the Halo. FastAPI serves both the dashboard and its same-origin API; Lemonade remains on Halo loopback and is never exposed to the browser.

## Start it

On the AMD Halo, run `start_joeos.sh`. It creates a private Python environment on the first launch, installs the pinned packages, detects the Halo's Tailscale IPv4 address, and starts JoeOS on port `8080`.

On macOS, double-click `start_joeos.command`. On Windows, double-click `start_joeos.bat`.

For the recommended private HTTPS iPhone route, double-click `start_joeos_secure.command` instead. It runs JoeOS on loopback and configures Tailscale Serve on private HTTPS port 443. It does not enable public Tailscale Funnel.

The launcher prints the exact URL. With the previously assigned Tailscale address it will be:

```text
http://100.121.165.22:8080
```

Keep that terminal window open while using JoeOS. Press `Ctrl+C` to stop it.

## Use it from iPhone

1. Connect the iPhone to the same Tailscale tailnet as the Halo.
2. Open the URL printed by the launcher.
3. For an app-like icon, use **Share → Add to Home Screen**. Launch JoeOS from that icon afterward.

For an installable PWA and encrypted browser transport, use the HTTPS address printed by `start_joeos_secure.command`. Do not use Tailscale Funnel unless public internet access is intentional.

## Prepare native device pairing

JoeOS now includes the server-side local-console pairing ceremony. Keep JoeOS running, then create its only active five-minute offer from a second terminal on the same JoeOS host:

```bash
./pair_joeos_iphone.command
```

On a Linux Halo without Finder, the equivalent is:

```bash
./.venv/bin/python -m server.identity.cli issue
```

The CLI automatically prefers a non-Funnel Tailscale Serve HTTPS mapping only when it proxies to the expected JoeOS loopback port. If that origin is stale or unreachable, it safely tries the direct Tailscale HTTP listener and then loopback, verifying the exact bootstrap URL and local installation UUID before creating anything. To bind a managed deployment to another exact origin, use the explicit override:

```bash
JOEOS_PUBLIC_ORIGIN=https://your-halo.your-tailnet.ts.net ./.venv/bin/python -m server.identity.cli issue
```

The tool verifies that the exact configured origin reaches the same local JoeOS database before it prints the one-use code. Do not paste that code into a website, browser form, chat, log, or untrusted app. After building and installing the modular SwiftUI client as described in `apps/mobile/README.md`, open its native **Pair This iPhone** panel, paste the full code, verify the exact Halo origin and server ID, and explicitly confirm the two device keys with Face ID. The manual code is cleared before verification, the private keys remain in the Secure Enclave, and an exact signed completion is stored in the non-synchronizing ThisDeviceOnly Keychain before any completion request is sent.

A successful ceremony creates an `active_unassigned` device with no session, role, approval, API authorization, or execution authority. The app's stored receipt is not a live revocation check. Local operators can inspect and revoke these records with:

```bash
./.venv/bin/python -m server.identity.cli list
./.venv/bin/python -m server.identity.cli revoke <device-uuid> --reason "Device replaced"
```

The first launch creates `data/identity-master.key` with owner-only permissions. It protects pending pairing material if only the SQLite database or WAL is copied; it is not full database encryption. Back up or restore that file with `data/joeos.db`, never commit it, and never serve it to a browser. Managed deployments can inject `JOEOS_IDENTITY_MASTER_KEY` or point `JOEOS_IDENTITY_MASTER_KEY_FILE` at a separate owner-only key file. OS Keychain wrapping remains future work. See [Device Enrollment Security Protocol](docs/security/DEVICE_ENROLLMENT.md) for the threat model and exact ceremony.

## Lemonade

JoeOS expects Lemonade's OpenAI-compatible API at:

```text
http://127.0.0.1:13305/api/v1
```

The browser calls only JoeOS endpoints such as `/api/chat`. JoeOS forwards chat to Lemonade server-side, so the loopback URL and any optional `LEMONADE_API_KEY` never enter frontend JavaScript.

JoeOS automatically selects a downloaded text model. To pin one, set `LEMONADE_MODEL` before launching. See `.env.example` for optional overrides.

## What is real

- CPU, unified memory, GPU telemetry supplied by Lemonade, disk, and uptime are sampled every five seconds.
- Metrics, events, and agent profiles are stored locally in `data/joeos.db`.
- Mission Control layouts, widget visibility/order/size, and appearance settings are versioned and stored in the same private SQLite database.
- The widget catalog distinguishes live core modules from modules that still require an approved integration.
- The configuration guide creates a reviewed proposal; it does not silently change the workspace or collect secrets.
- Bot Fleet Start/Stop controls profile state and is persisted locally.
- AI Assistant responses come from the selected Lemonade model.

CI/CD history remains a clearly labeled interactive preview until an approval-gated repository runner is connected. Browser chat is deliberately read-only and cannot launch shells, change files, or deploy code.

## Health checks

- `/healthz` — JoeOS process status and Lemonade reachability.
- `/api/metrics` — live local telemetry.
- `/api/bots` — local profile fleet.
- `/api/events` — local audit stream.
- `/api/chat` — private Lemonade chat proxy.

No Render, Supabase, Stripe, GitHub token, or cloud service-role key is required for local operation.
