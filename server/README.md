# JoeOS server

The server directory contains domain modules extracted behind the existing `joeos_backend.py` compatibility entry point. FastAPI routers remain transport-only; services own policy and use injected repository/provider interfaces.

Initial module order:

1. workspace/widget persistence and guided configuration;
2. versioned event streaming and Mission Control read models;
3. identity, devices, roles, and approvals;
4. agent orchestration, memory/RAG, search, and automation.

The first identity slice is implemented under `server/identity/`: local-console offer creation, strict two-key P-256 challenge/completion, AES-256-GCM protection for pending pairing keys, atomic SQLite activation as `active_unassigned`, append-only identity events, and local device listing/revocation. `server/security/enrollment_guard.py` bounds enrollment bodies and attempts before JSON parsing. The running application advertises this exact non-authoritative capability through bootstrap schema version 2.

There is intentionally no API route for creating pairing offers. See [Device Enrollment Security Protocol](../docs/security/DEVICE_ENROLLMENT.md) before changing the transcript, cryptographic formats, origin policy, expiry, replay behavior, key storage, or activation state.

Application authentication, signed-request enforcement, users/roles, and privileged approvals are not implemented. The modular iOS source now provides native private-key custody and the reviewed enrollment ceremony, but a paired device still has no authority and its local receipt is not a live revocation check.

Privileged execution belongs to an enrolled local runner, not an API route or browser chat handler.

## Project and repository intelligence

`server/intelligence/` implements the Project and Repository Intelligence platform: stable repository fingerprints, incremental file inventory with evidence-based classification, language/framework/package/build/test detection, bounded parser adapters (python, javascript, typescript, go, rust, java, sql, markdown, shell, dockerfile), dependency and architecture graphs, Git history intelligence, change-impact estimation, explainable risk findings, ADR/convention ingestion, project memory, hybrid structured retrieval, and context packs. A cancellable background index maintains health and diagnostics.

The index is a versioned SQLite database under the JoeOS data directory. Every record carries provenance, confidence, and freshness; parsed facts remain distinct from future AI interpretation. Secret-bearing files are flagged and never parsed or returned. See `../docs/architecture/PROJECT_INTELLIGENCE.md`.
