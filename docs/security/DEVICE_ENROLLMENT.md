# JoeOS Device Enrollment Security Protocol

Status: implemented local-console server and native iPhone source foundation

Protocol: `joeos-device-enrollment-v1`

JoeOS can now bind two device public keys to one local installation through a short-lived, operator-created pairing ceremony. A successful ceremony creates an `active_unassigned` device record. It does **not** create a user session, assign a role, authorize API access, approve a privileged action, or permit agent, shell, file, Git, download, deployment, payment, message, or device execution.

Application authentication, RBAC, privileged approvals, and authenticated request enforcement remain unavailable. The native iPhone source now implements this pairing ceremony with Secure Enclave keys, an explicit review-before-Face-ID boundary, and device-only Keychain recovery. This slice is still not public-internet ready and a pairing receipt still grants no authority.

## Security objective and threat model

The ceremony is designed to:

- require access to the JoeOS host's local operator console before an offer can be created;
- bind an exact JoeOS installation UUID, canonical audience origin, offer, challenge, device, metadata, two public keys, nonces, and expiry times into one transcript;
- prove possession of the 256-bit pairing secret with HMAC-SHA-256;
- prove possession of two distinct P-256 private keys with purpose-separated ECDSA/SHA-256 signatures;
- reject expired, changed, malformed, replayed, or excessively retried completions;
- commit enrollment, both keys, idempotency state, and audit state atomically in SQLite;
- let the local operator enumerate and revoke paired devices without exposing key material;
- prevent a stolen JoeOS database or WAL file alone from revealing a still-live pairing key.

The current design assumes the operator controls the local JoeOS account and terminal, the configured origin points to the intended JoeOS instance, and network access remains inside an operator-managed private tailnet or equivalent private boundary. It does not protect against a compromised JoeOS process, a compromised operator account, a client that accepts the wrong origin, or theft of both the database and its identity master key. Pairing identifies device keys; it does not authenticate a person.

HTTP enrollment is accepted only on loopback or a Tailscale `100.64.0.0/10` address. HTTPS enrollment accepts loopback, private/link-local addresses, `.local`, `.ts.net`, single-label hosts, or exact hostnames configured in `JOEOS_ALLOWED_HOSTS`. Origins must be canonical ASCII origins with no credentials, path, query, fragment, trailing dot, redundant default port, zone identifier, or IPv4-mapped IPv6 address. Private HTTPS through Tailscale Serve remains the recommended iPhone transport.

## Local offer authority

There is deliberately no HTTP endpoint that creates a pairing offer. The operator creates one on the JoeOS host with:

```bash
./pair_joeos_iphone.command
```

The equivalent CLI is:

```bash
./.venv/bin/python -m server.identity.cli issue
```

The CLI automatically prefers a private Tailscale Serve HTTPS entry only when it is not Funnel-enabled and proxies to the expected JoeOS loopback port. It then tries the direct Tailscale HTTP listener and loopback, accepting only a reachable exact-origin bootstrap document for the same installation. `JOEOS_PUBLIC_ORIGIN` remains an explicit override when managed networking requires a different exact origin:

```bash
JOEOS_PUBLIC_ORIGIN=https://your-halo.your-tailnet.ts.net ./.venv/bin/python -m server.identity.cli issue
```

Before printing a code, the CLI requests the exact origin's `/api/v1/bootstrap` document, refuses redirects, validates the bounded strict schema, verifies that its installation UUID matches the local database, and requires the advertised local-console enrollment capability.

An offer contains a fresh UUIDv4 and a fresh 32-byte random secret. Its manual form is:

```text
JOEOS1|<canonical-origin>|<offer-uuid>|<unpadded-base32-secret>
```

The full code is a secret. It is printed only to the local terminal and is never returned by the API. The normal offer lifetime is five minutes. The server enforces a minimum of 60 seconds and a maximum of 300 seconds if a lower lifetime is supplied internally.

Only one pending offer is active for an installation. Issuing another offer revokes every earlier pending offer, scrubs its protected pairing key, and supersedes open challenges. An offer permits at most five challenge claims. Each later claim supersedes the earlier open challenge for that offer.

## Cryptographic profile

All binary values sent in JSON use canonical RFC 4648 base64url without padding unless stated otherwise.

| Item | Required value |
|---|---|
| Pairing secret | 32 random bytes, unpadded Base32 only inside the manual code |
| Pairing key derivation | HKDF-SHA-256 profile using the offer UUID bytes as salt and `joeos.device-enrollment.pairing-key.v1` as context |
| Client and server nonces | 32 random bytes each |
| Device keys | Two distinct NIST P-256 (`secp256r1`) keys |
| Public-key encoding | Canonical DER SubjectPublicKeyInfo, then unpadded base64url |
| Public-key fingerprint | SHA-256 of canonical DER, then unpadded base64url |
| Signatures | Canonical ASN.1 DER ECDSA/SHA-256, then unpadded base64url |
| Transcript digest | SHA-256 of the exact transcript |
| Secret proofs | HMAC-SHA-256 with purpose-separated domains |

The two key purposes are `device_authentication` and `approval`. The same public key cannot be registered for both purposes. The approval key is reserved for a future biometric step-up protocol; storing it during pairing does not make an approval valid today.

The key and secret-proof bytes are defined exactly as follows, where `||` means byte concatenation and UUID bytes are the UUID's 16-byte network-order representation:

```text
prk         = HMAC-SHA-256(key=offer_id.bytes, message=pairing_secret)
pairing_key = HMAC-SHA-256(key=prk, message=ASCII("joeos.device-enrollment.pairing-key.v1") || 0x01)
claim_proof = HMAC-SHA-256(key=pairing_key, message=claim_transcript)
server_proof = HMAC-SHA-256(key=pairing_key, message=ASCII("JOEOS-DEVICE-ENROLLMENT-SERVER-PROOF-V1") || 0x00 || transcript_sha256)
client_proof = HMAC-SHA-256(key=pairing_key, message=ASCII("JOEOS-DEVICE-ENROLLMENT-CLIENT-PROOF-V1") || 0x00 || transcript_sha256)
```

The claim transcript begins with `JOEOS-DEVICE-ENROLLMENT-CLAIM-V1` and one NUL byte. It then length-prefixes the following fields with the same four-byte unsigned big-endian encoding used by the enrollment transcript: observed server UUID text, audience origin, offer UUID text, request UUID text, client-instance UUID text, raw client nonce, display name, platform, OS version, app version, authentication DER SubjectPublicKeyInfo, and approval DER SubjectPublicKeyInfo. UUIDs and the origin/platform fields are ASCII; human-readable metadata is UTF-8. The server verifies this proof before it allocates or counts a challenge.

## Exact transcript encoding

The transcript begins with the ASCII domain bytes `JOEOS-DEVICE-ENROLLMENT-TRANSCRIPT-V1` followed by one NUL byte. The server then appends each field below in order. Every field is encoded as a four-byte unsigned big-endian byte length followed by exactly that many value bytes.

| Order | Field | Value encoding |
|---:|---|---|
| 1 | `server_id` | canonical UUID text, ASCII |
| 2 | `audience_origin` | validated canonical origin, ASCII |
| 3 | `offer_id` | canonical UUID text, ASCII |
| 4 | `request_id` | canonical UUIDv4 text, ASCII |
| 5 | `challenge_id` | canonical UUID text, ASCII |
| 6 | `device_id` | canonical UUID text, ASCII |
| 7 | `client_instance_id` | canonical UUID text, ASCII |
| 8 | `client_nonce` | raw 32 bytes |
| 9 | `server_nonce` | raw 32 bytes |
| 10 | `display_name` | UTF-8 |
| 11 | `platform` | ASCII: `ios`, `macos`, `windows`, or `linux` |
| 12 | `os_version` | UTF-8 |
| 13 | `app_version` | UTF-8 |
| 14 | authentication public key | canonical DER SubjectPublicKeyInfo bytes |
| 15 | approval public key | canonical DER SubjectPublicKeyInfo bytes |
| 16 | `issued_at` | decimal Unix seconds, ASCII |
| 17 | `expires_at` | decimal Unix seconds, ASCII |

The challenge expires after two minutes or when its parent offer expires, whichever comes first.

For each key, JoeOS returns a separate ASCII signing envelope ending in a newline. Its first line is either `JOEOS-DEVICE-ENROLLMENT-PROOF-V1:DEVICE-AUTHENTICATION` or `JOEOS-DEVICE-ENROLLMENT-PROOF-V1:APPROVAL`. Subsequent `name:value` lines bind, in order, `server_id`, `audience_origin`, `offer_id`, `request_id`, `challenge_id`, `device_id`, `key_purpose`, the matching key fingerprint, the base64url transcript digest, issuance timestamp, and base64url server nonce. The client independently reconstructs both envelopes, compares them byte-for-byte with the bounded returned values, and then signs those exact bytes. It must not sign a merely semantically similar payload.

## Ceremony

1. The native client reads strict bootstrap schema version 2 and confirms the exact enrollment profile and two advertised relative POST routes. Discovery metadata alone establishes no trust.
2. The operator creates the one-use offer locally and transfers its full manual code to the intended client.
3. The client verifies that the code's origin is exactly the origin it will contact, generates a UUIDv4 request ID, the two distinct P-256 keys, and a client nonce. It derives the pairing key, constructs the exact claim transcript, and posts the observed server UUID, offer and request UUIDs, origin, metadata, nonce, public keys, and claim proof to `/api/v1/device-enrollment/challenges`.
4. JoeOS verifies the claim proof before allocating state. It then allocates fresh challenge and device UUIDs plus a server nonce, persists the bound transcript, and returns the request UUID, digest, server proof, and two purpose-separated signing payloads. Exact retries with the same request UUID and identical request return the same open challenge—even across a concurrent race—and do not consume another claim. Reusing that UUID with changed content returns a conflict. A 32 KiB request limit and a 30-attempt-per-minute, per-source process-local guard run before JSON parsing.
5. The client requires the echoed request UUID and every returned identity/origin value to match its local ceremony, reconstructs the exact transcript and both signing envelopes from its own state plus validated response fields, compares their digests/bytes, and verifies `HMAC-SHA-256(pairing_key, server-proof-domain || transcript_digest)` before signing.
6. The client signs each exact payload with its matching private key. It posts the transcript digest, `HMAC-SHA-256(pairing_key, client-proof-domain || transcript_digest)`, both signatures, and a fresh idempotency UUID to `/api/v1/device-enrollment/challenges/{challenge_id}/complete`.
7. JoeOS verifies the open challenge, expiry, installation identity, transcript digest, HMAC proof, and both key-possession signatures. It then inserts the device, immutable public keys, completion record, and append-only identity audit event in one immediate SQLite transaction. The consumed offer's protected pairing key is scrubbed.
8. The receipt reports `active_unassigned` and explicitly states that the device has no role, session, approval, or execution authority.

The native client separates those steps deliberately. `prepare` verifies the complete server transcript and both exact signing envelopes without signing. Only an explicit confirmation of the displayed server UUID, exact origin, device UUID, fingerprints, and expiry invokes `confirm`; the approval key then requires the current Face ID enrollment. Before `complete` sends anything, the exact signed body and stable idempotency UUID are stored with `WhenUnlockedThisDeviceOnly` Keychain protection. A timeout or app termination can therefore retry the same bytes without another signature or pairing secret. The journal is removed only after the validated receipt is stored. The manual code, raw pairing secret, derived pairing key, and prepared challenge are never serialized.

Malformed or invalid cryptographic completions receive the same generic protocol failure. Five failed completion proofs lock the challenge and its offer and scrub the protected pairing key. Exact concurrent or later retries with the same idempotency key and semantic request return the original active receipt; changing the request under that key returns a conflict. A different retry after consumption is rejected. Revoking the device also prevents receipt replay.

Startup upgrades databases created by the pre-claim-proof implementation. It backfills non-authoritative correlation/audit fields so completed history remains readable, revokes and scrubs every unfinished legacy offer, and supersedes every open legacy challenge. Those ceremonies cannot safely cross the protocol boundary and must be restarted with a new local offer.

## Pairing-key protection at rest

JoeOS never stores the manual 32-byte secret. While an offer is live, its derived pairing key is protected with AES-256-GCM before SQLite sees it. A fresh 96-bit nonce is used for each encryption, and associated data binds the installation UUID, offer UUID, and canonical audience origin. Tampering, record swapping, or a wrong master key fails closed. Completion, expiry, lockout, replacement by a new offer, and revocation of an offer set the stored ciphertext to `NULL`.

By default, JoeOS atomically creates `identity-master.key` beside the SQLite database, normally `data/identity-master.key`, with owner-only mode `0600`. The database contains only a non-secret master-key identifier so startup can reject a mismatched key. Preserve the database and master key together for recovery; replacing the key does not silently re-encrypt existing pending material. Concurrent backend and CLI startup serialize schema migration and master-key binding. Upgrades preserve completed history but revoke and scrub unfinished ceremonies from the older proof format so they restart safely.

Managed installations can instead provide either:

- `JOEOS_IDENTITY_MASTER_KEY`: exactly 32 bytes encoded as canonical unpadded base64url through the process secret environment; or
- `JOEOS_IDENTITY_MASTER_KEY_FILE`: an absolute or resolved path to a regular, non-symlink 32-byte raw key file with mode `0600`.

`JOEOS_IDENTITY_MASTER_KEY` takes precedence when both are present. Neither value belongs in source control, frontend JavaScript, logs, pairing codes, or support bundles.

This envelope specifically reduces exposure from a copied SQLite database or WAL file without the separate master key. It is not full database encryption and does not defend against a process or operating-system account that can read both files. Wrapping the master key with macOS Keychain or another platform keystore remains future work.

## Local lifecycle operations

List paired devices without key material:

```bash
./.venv/bin/python -m server.identity.cli list
```

Revoke one device and deactivate both of its public keys:

```bash
./.venv/bin/python -m server.identity.cli revoke <device-uuid> --reason "Device replaced"
```

Enrollment keys and identifying columns are immutable after insertion. Revocation is local, append-only identity events record important transitions, and a second revocation is a no-op. Because application authentication and signed-request enforcement are not implemented yet, revocation currently changes durable identity state but is not evidence that a general API session has been invalidated—there are no such sessions in this release.

## Remaining security gates

Before JoeOS can expose private state or privileged actions beyond the operator-managed private boundary, it still requires:

- an Xcode project/signing pipeline plus physical-device, backup, recovery, biometric-change, replacement, and revocation-freshness drills for the implemented native pairing source;
- authenticated, short-lived sessions bound to the enrolled authentication key;
- users, workspaces, roles, capability policy, and server-side authorization on every protected route;
- a separate immutable approval challenge with biometric step-up and anti-replay state;
- a signed runner channel for allow-listed execution;
- secrets management, key rotation, backup/restore drills, and OS-keystore master-key wrapping;
- authenticated and identity-scoped realtime subscriptions, audit attribution, proxy policy, and production rate limiting.

Until those gates pass, keep JoeOS on a private tailnet. Do not expose its API with Tailscale Funnel or another public reverse proxy.
