# JoeOS Security Platform

Phase 15 delivers `server/security/`: a zero-trust, deny-by-default hardening
layer enforced in authoritative services. It inspects and hardens the
privileged boundaries introduced in previous phases without duplicating the
existing identity, approval, secrets, or audit systems.

## Principles

- **Deny by default.** An action with no matching allow rule is denied.
- **Least privilege.** Grants are scoped, time-limited, and revocable; no
  global wildcard.
- **Exact approval.** Approvals bind to exact action, target, arguments hash,
  content hash, attachment hashes, versions, scope, and expiration; any
  material change invalidates them. Strength is risk-based (levels 0-5).
- **Content is not authority.** Policy is typed structured rules — never
  JavaScript or eval — and never trusts a caller-supplied result or UI label.
- **Secrets stay secret.** The Secret Broker encrypts values at rest with
  AES-256-GCM using established libraries; values are never returned to
  renderers/mobile/wearables, never logged, and never placed in URLs,
  clipboard, or model context. Access is scoped, purpose-bound,
  rate-limited, and audited.
- **Honest guarantees.** Audit uses a hash chain for application-level tamper
  evidence (accidental-modification resistance), not an immutable-log claim.
  No compliance, malware, vulnerability-count, or penetration-test results
  are fabricated.
- **No impersonation.** Agents, workflows, plugins, and devices can never
  impersonate a human user.

## Architecture

```text
server/security/
├── models.py          typed contracts (policies, threat models, identities, scopes, approvals, secrets, audit, incidents, governance)
├── storage.py         versioned SQLite registry (security.db)
├── policy.py          Security Policy Registry + Policy Evaluation Engine (deny-by-default, deterministic precedence)
├── identity.py        Identity Registry + Scope Resolver (explicit containment, traversal/symlink rejection)
├── approvals.py       exact-bound approvals + strength levels + separation of duties + consent
├── secrets.py         authoritative Secret Broker (AES-GCM, rotation, revocation, scanning) + detection
├── audit.py           hash-chained audit, security events, incidents, lockdown, emergency stop, quarantine, circuit breakers
├── classify.py        Data Classification + Privacy Policy Engine + Threat Model Registry
├── service.py         SecurityService facade
└── router.py          REST API under /api/v1/security/*
```

## Security model

- **Policy**: deny-by-default; deny wins at equal priority; higher priority
  wins; `require_approval` and `require_stronger_authentication` surfaces are
  returned to callers for enforcement.
- **Scope**: canonicalized paths with explicit containment (never string
  prefix), NUL rejection, `..` rejection; project/task/device-scoped grants.
- **Approval**: level0-level5; `external_send`, `deployment`, `git_push`,
  `file_deletion`, `service_restart`, `secret_access`, and other high-risk
  actions require level4 and forbid requester self-approval.
- **Secrets**: metadata-only records; encrypted values in a separate table;
  destination policy; per-secret rate limit; masked-fingerprint detection for
  API keys, GitHub tokens, AWS keys, Google keys, Slack tokens, private keys.
- **Audit**: hash chain over (previous_hash, sequence, actor, action, target,
  result, permission_decision, trace_id); tamper verification; redacted
  events.
- **Governance**: Lockdown (requires reauthentication to exit), Emergency
  Stop (reports incomplete cancellation honestly, no auto-restart),
  Quarantine, and per-target Circuit Breakers.

## Integration

- **Command Center**: `security.platform` health signal + Security Center
  frontend section showing real security events and overview.
- **Backend**: `SecurityService` constructed in lifespan with the identity
  master key; `security_router` included; bootstrap advertises
  `security.overview` (128 routes).
- **Other platforms**: the Security Platform is the authoritative policy,
  approval, secret, and audit authority that desktop, web, mobile, wearables,
  plugins, agents, and automation consult through typed contracts.

## Honest limitations

- Audit integrity is hash-chain tamper evidence, not immutability against a
  local root.
- Secure storage is an AES-GCM vault keyed by the JoeOS identity master key;
  OS keychain/hardware-backed protection is documented as future work, not
  claimed.
- Secret detection reports masked candidates with confidence; it is not
  malware detection or a confirmed-credential guarantee.
- Circuit breakers and lockdown integrate via the governance boundary;
  platform-specific cancellation propagates through each platform's own
  cancellable interfaces.
