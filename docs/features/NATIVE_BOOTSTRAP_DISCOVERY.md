# Native Bootstrap Discovery

Status: implemented read-only local-first foundation

Endpoint: `GET /api/v1/bootstrap`

## Purpose

The bootstrap document gives native macOS and iOS clients one strict, versioned place to discover the JoeOS server they are already connected to. It exposes only:

- a stable non-secret server UUID;
- product, server, and API contract versions;
- an honest security posture;
- a fixed, non-secret local-console device-enrollment profile;
- curated capability descriptors;
- relative route metadata intended for supported clients.

It does not return a hostname, IP address, public origin, forwarded host, Lemonade URL, model name, environment variable, credential, token, pairing secret, offer, challenge, signature, or enrollment receipt.

## Contract

Example, abbreviated:

```json
{
  "schema_version": 2,
  "generated_at": "2026-07-29T15:30:00Z",
  "server": {
    "server_id": "12345678-1234-4abc-8def-1234567890ab",
    "product_id": "joeos",
    "display_name": "JoeOS Local Command Center",
    "server_version": "2.0.0",
    "api_version": "v1",
    "deployment_mode": "local_first"
  },
  "security": {
    "ownership_model": "single_owner",
    "network_boundary": "operator_managed_private_tailnet",
    "application_authentication": "unavailable",
    "device_enrollment": "operator_pairing_v1",
    "role_based_access": "unavailable",
    "privileged_actions": "unavailable",
    "public_internet_ready": false,
    "secrets_returned": false,
    "warning": "Local-console device pairing is available, but application authentication, roles, and privileged approvals remain unavailable. JoeOS is not public-internet ready."
  },
  "device_enrollment": {
    "protocol": "joeos-device-enrollment-v1",
    "offer_authority": "local_console_only",
    "pairing_secret_bytes": 32,
    "offer_ttl_seconds": 300,
    "challenge_ttl_seconds": 120,
    "key_algorithm": "ES256",
    "public_key_format": "spki_der_base64url",
    "signature_format": "x962_der_base64url",
    "proof_algorithm": "HKDF-SHA256+HMAC-SHA256+ECDSA-SHA256",
    "required_key_purposes": ["device_authentication", "approval"],
    "activation_state": "active_unassigned",
    "grants_authority": false
  },
  "capabilities": [],
  "routes": []
}
```

`generated_at` is timezone-aware UTC for client clock/skew diagnostics. It is not a signed time source.

The server UUID is a randomly generated UUIDv4 durable installation identifier, not an authentication credential, trust proof, enrollment key, or authorization decision. The repository rejects non-v4 generated or stored identifiers. A client must never grant access merely because a remembered UUID matches.

## Curated route metadata

The current contract advertises only supported native-facing operations:

- bootstrap discovery;
- local-console offer claim and two-key enrollment completion;
- workspace read and non-privileged appearance/layout configuration;
- metrics read;
- Bot Fleet profile-state read;
- audit-event read;
- non-executing local assistant analysis;
- resumable realtime event streaming.

Paths are relative. The native application combines them only with the private server origin the user explicitly configured. JoeOS does not infer, echo, or trust an external origin or forwarded host in this response.

The enrollment routes are exactly `POST /api/v1/device-enrollment/challenges` and `POST /api/v1/device-enrollment/challenges/{challenge_id}/complete`. There is no remote offer-creation route. Existing profile mutation endpoints remain deliberately absent from bootstrap route metadata. No shell, download, deployment, connector-secret, authenticated-session, or privileged-action route is introduced.

## Capability states

Capabilities use explicit `available` or `unavailable` status and one of these access classifications:

- `read_only`;
- `configuration`;
- `stream`;
- `local_analysis`;
- `enrollment`;
- `unavailable`.

Device enrollment is the only identity capability reported as available. It means a compatible client can bind two public keys and receive `active_unassigned`; it grants no authority. Identity authentication, role-based authorization, approval-gated privileged actions, agent execution, and native secret management remain unavailable with no route IDs.

Workspace `PUT` is described only as non-privileged appearance and layout configuration. It is not evidence of a general write or execution permission.

## Persistence

`server_identity` is an additive SQLite table containing one UUID and its creation timestamp. Startup uses an immediate transaction to create the UUID once. Existing metrics, bot, audit, workspace, and realtime tables are not rewritten.

The UUID remains stable across JoeOS process restarts while the database is preserved. Restoring or replacing the database can change the UUID; native clients should present that as a server-change notice, not silently infer compromise or trust.

## Validation and transport

Every response model forbids unknown fields and uses strict Pydantic types. Capability references must resolve to a declared route, route/capability IDs must be unique, WebSocket and HTTP methods must match their protocol, and `generated_at` must be UTC.

The existing API middleware marks the response `Cache-Control: no-store`. The endpoint is GET-only and adds no CORS, authentication, token, cookie, enrollment, host expansion, or execution behavior.

## Native client sequence

1. The user supplies or selects the private JoeOS HTTPS origin.
2. The client requests the relative path `/api/v1/bootstrap`.
3. It rejects unsupported `schema_version` or `api_version` values.
4. It displays the returned security limitations before enabling configuration features.
5. It uses only advertised relative routes whose capability is `available`.
6. It treats pairing support as metadata until the separate native ceremony proves the pairing secret and both private keys. The modular iOS source implements that phased ceremony with an explicit review before Face ID and an exact signed-retry journal; pairing still grants no authority.
7. It treats unavailable authentication, roles, approval, secret-management, and execution capabilities as hard feature gates.
8. It may compare the UUID with a previously observed value for operator information, never authorization.

The exact pairing threat model and byte-level protocol are in [Device Enrollment Security Protocol](../security/DEVICE_ENROLLMENT.md).
