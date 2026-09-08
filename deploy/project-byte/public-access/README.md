# Authenticated public live workspace

This gateway publishes the existing PROJECT_BYTE workspace behind sign-in without
changing the app's source, database, access keys, roles, settings or private URLs.
It is independent of the read-only preview and execution-permission runner.

The owner explicitly selected **full access live workspace with sign in**. This
allows installing this gateway and enabling Funnel on port 443 for it. It does not merge
pending application PRs, start a permission runner, resume BYTE or change R3/VITROS.

## Authentication and browser behavior

Use the separate public owner sign-in key or an existing strong collaborator access
key. Live preflight found the private owner key is too short for public authentication.
The public owner key is generated from 32 random bytes and kept in a private 0600
file outside the repository and release. The service requires this file; it rejects
the raw private owner key, preserving that key for the existing private app. Public
owner sign-in resolves to the current owner credential entirely inside the gateway.
Rotating either credential invalidates the corresponding owner sessions. The gateway
validates credentials and settings with read-only queries; invalid keys are never
forwarded to the app. A random Secure/HttpOnly/SameSite=Strict host cookie
identifies a volatile gateway session. The real key exists only in that session's
server memory and is never written to gateway disk, cookies, logs or browser storage.
A separate per-session nonce is supplied in authenticated HTML and used in the
existing browser access-key slot. Every API call requires both cookie and nonce.
The gateway then sends the original key to the fixed loopback app, preserving its
role checks. Headerless health/profile reads receive the nonce in the public-only
fetch wrapper. No key is added to requests to another origin.

Current credential revocation, role changes and session-duration settings are
checked on every request. Reload preserves the original session creation time.
New tabs receive the current session's nonce. Account changes notify other tabs
and stale nonces cause a reload without revoking the newer session. Logout revokes
the captured session on the server and clears the local tab; other tabs reload.
Failed requests and logout do not expire a cookie in the response, because a late
response could erase a newer sign-in. A revoked browser cookie is harmless and is
replaced at the next successful sign-in. Restarting the gateway signs everyone out.

Anonymous visitors receive only the sign-in page. All workspace HTML, scripts,
API data and attachments require a valid session. Attachment responses force a
download with an octet-stream content type and a sandbox CSP. The gateway forwards
upload metadata and supports all currently observed GET/POST/PATCH/DELETE routes;
unknown paths are not proxied. State-changing requests require the exact configured
Origin, and gateway login/logout require a custom header. No CORS access is granted.

## Network and failure isolation

The gateway listens only on 127.0.0.1:8097 and targets only 127.0.0.1:8094. Public TLS is
terminated by Tailscale Funnel on port 443. Existing Desktop Commander Serve on port 8443 remains
private and unchanged. The gateway trusts a single X-Forwarded-For IP and https
X-Forwarded-Proto only from loopback Serve. Pinned Tailscale 1.102.2 replaces incoming
X-Forwarded-For rather than appending untrusted values (ipn/ipnlocal/serve.go).

Login has a per-client limit of 10 attempts/minute, at most 2 concurrent login bodies
per client, and an 8-second hard read deadline. All inbound headers/bodies have a
30-second deadline and 24 total request slots. Long upstream chat replies do not
block other workspace requests. Request/response sizes are bounded at 10/20 MiB.

A private 0600 SQLite source map assigns each credential fingerprint its own stable
127.77.x.y source address for upstream connections. This isolates the existing app's
per-IP login-failure counter across credentials without serializing long requests.
Addresses are never reused and allocation is capped at 65534. **Do not delete or
reset this map while the backend may retain failure counters.** The map contains
fingerprints, not raw keys. It is separate from the app database. Mapping corruption
or exhausted capacity fails closed. The systemd service restricts writable paths,
networking, memory and tasks; the app's files are mounted read-only to the gateway.

The public domain and existing 8443 service share a hostname. Cookies are host-scoped,
not port-scoped, so the trusted Desktop Commander endpoint is not a separate cookie
security boundary. Public API actions additionally require a nonce and exact Origin.
Only443 is made public. No other VPS listeners, firewall rules, or DNS records change.

## Deployment and rollback

Copy gateway.py, signin.html and session.js from the independently reviewed exact
commit into a new 0700 release directory under
`/home/joevps/.local/share/project-byte-public-gateway/releases/`. Set files 0600,
create a 0700 state directory, verify SHA256 values, and point `current` at the release.
Install the included systemd unit, run `systemd-analyze verify`, and start only
`project-byte-public-gateway.service`.

Before starting the service, provision a `secrets.token_urlsafe(32)` value in
`credentials/public-owner.secret` under the gateway root. Set the credentials directory
0700 and the file 0600, owned by the service user. Keep this key out of Git, logs and
public artifacts. Give the owner the key through a private local credential file.
Do not replace the application's `admin.secret`. Preserve the public key across
release updates; intentional rotation signs out public owner sessions.

Before public access, verify login and authenticated read-only live API calls using
the public owner key entirely on the VPS; print only statuses/counts, never credentials,
cookies, nonces or business records. Verify anonymous GET/HEAD/API/uploads fail closed,
logout revokes the session, source map permissions and service restrictions work.
Then enable exactly:

```sh
sudo tailscale funnel --bg --https=443 --yes http://127.0.0.1:8097
```

Verify external HTTPS without tailnet access and inspect Funnel status to confirm
only 443 is public and 8443's private proxy is unchanged. Confirm the original app and
preview services remain healthy and owner-paused bridges remain inactive.

Rollback public sharing first with `sudo tailscale funnel --https=443 off`. Stop the
new gateway service if needed. This does not alter the private app or preview. Keep
the source map; point `current` to the preceding release for a gateway-only rollback.
Do not run the existing application installer for this operation.

## Verification

`python3 deploy/project-byte/public-access/test_gateway.py` covers authentication,
revocation, expiry, CSRF/Origin checks, route/framing boundaries, forced downloads,
source allocation, rate/concurrency limits, hard deadlines and parallel chat reads.
`test_browser.mjs` reconstructs the actual app in disposable storage and exercises
all 12 workspaces at 320/390/768/1440px, headerless health, task creation/upload/download,
new tabs, account changes, viewer restrictions, original TTL and logout. It uses no
production data/models/credentials and leaves no fixture services running.
