# JoeOS Production Readiness and Release Engineering

Phase 18 delivers an honest, local-first production readiness and release
engineering layer (`server/production/`). It reports build metadata and
supported targets derived from the actual build, enforces explicit release
gates that never fabricate success, coordinates versioned migrations with
backup-before-risk and future-schema protection, creates verified backups and
staged restores that reset stale authority, validates staged update packages
before activation, and provides Safe Mode, Repair Mode, crash-loop detection,
and a `doctor` CLI.

## Honesty principles (as implemented)

1. **User data first** — migrations never replace a database with an empty
   one; backups snapshot authoritative data; restore stages and validates
   before activation.
2. **Verify before activate** — backups are not verified until the archive
   hash and manifest match; updates are not activated until integrity and
   compatibility checks pass.
3. **Exact versioning** — application, schemas, and backup format are versioned
   explicitly; a database or backup with a newer unknown format blocks writes
   and restore.
4. **No fabricated claims** — unsupported targets, scans, signing, SBOM, and
   update distribution are reported as `unsupported` or `not_configured`,
   never as passing.
5. **Derived data is rebuildable** — caches/indexes are treated as rebuildable;
   user data and security state are not.

## Architecture

```
server/production/
  models.py        typed models (BuildMetadata, ReleaseGate, ReleaseStatus,
                   SupportedTarget, MigrationState, BackupRecord, RestorePlan,
                   UpdateRecord, RecoveryState, CompatibilityCheck)
  metadata.py      automatic build metadata + honest supported-target matrix
  compatibility.py Compatibility Registry (schemas, backup format)
  migrations.py    Migration Coordinator (lock, backup-before, future-schema)
  backup.py        Backup Coordinator + Restore Coordinator (staging, security reset)
  updates.py       Update Coordinator (staged package verification)
  recovery.py      Recovery Coordinator, Safe Mode, Repair Mode, crash-loop
  service.py       ProductionService facade (status, gates, doctor)
  router.py        /api/v1/production/* REST API
```

## Build metadata and targets

`build_metadata()` derives the version, commit, branch, channel, build time,
environment, platform/architecture, dirty working-tree state, dependency-lock
hash, and per-store schema versions from the actual tree — nothing is
hard-coded. A dirty working tree is labeled and never presented as a release.
`supported_targets()` honestly marks linux + web as supported on this host and
macOS/Windows/iOS/Android as unsupported (no host toolchains).

## Release gates

The release status reports explicit gates, each passing / warning / blocking /
unavailable / unsupported / not_configured. Gates include version consistency,
schema compatibility (future on-disk schemas block writes), automated tests,
validated backup availability, production bundle, artifact integrity, secret
scan, SBOM, dependency scan, signing, update distribution, external telemetry
(default off), and public listener (loopback only). Scans, signing, SBOM, and
update distribution are reported `not_configured` because they are not
actually run/configured in this repository.

## Migrations

The Migration Coordinator tracks declared schema versions per store, acquires
a migration lock with stale-owner handling, requires a backup before a risky
migration (via an injected backup hook), refuses writes when an on-disk schema
is newer than supported, and preserves the original database on failure (no
empty-database fallback). Migrations are versioned, ordered, and recorded.

## Backups and restore

Backups snapshot the authoritative data directory (main database plus every
platform store) into a zip archive with a manifest and integrity hash. SQLite
files use the online backup API for a consistent snapshot. A backup is
`verified` only after the hash and manifest match. Retention keeps the latest
N and refuses to delete the only verified backup.

Restore stages to a temporary directory, validates every file hash and the
backup format, creates a current-state recovery checkpoint, activates
atomically, and invokes a security-reset hook so that restored mobile sessions
are revoked and pending approvals invalidated — stale authority never
reactivates silently. Workflow pausing and device restriction are surfaced in
the restore plan as policy requirements.

## Updates

The Update Coordinator validates a staged update package produced by
`scripts/release.py`: it reads `release-manifest.json`, checks every file hash,
verifies version compatibility, and requires a backup before activation. Update
states are explicit and completion is only reported after post-update
validation. There is no network distribution; that limitation is reported
honestly.

## Recovery, Safe Mode, and Repair Mode

Recovery state persists Safe Mode and Repair Mode flags and detects crash
loops over a rolling window. Safe Mode restricts third-party plugins,
workflows, agents, cloud providers, remote clients, and model preload, and is
clearly visible in the Production Center. Repair Mode is detection-driven and
always preserves authoritative data.

## Doctor CLI

`python scripts/doctor.py` runs non-destructive checks (version, platform,
working tree, data directory, database/migration gate, backups, safe mode,
disk, secret scan, signing) and prints PASS/WARN/FAIL/UNAVAILABLE/UNSUPPORTED
with remediation. Exit code 0 = pass, 1 = warning, 2 = fail.

## Integration

- Command Center lists `production.platform` (17th service).
- Bootstrap advertises `production.overview` (route) and
  `production.platform.read` (capability) at 128 routes / 40 capabilities.
- The backend wires the real data root, declared schema versions, and a
  security-reset hook (mobile `revoke_all` + pending-approval invalidation).
- Production Center workspace shows build, gates, targets, migrations,
  backups, and recovery with real state.

## Honest limitations

- No signing, notarization, package-manager publication, container
  publication, mobile-store distribution, push delivery, or network update
  distribution is implemented or claimed.
- Secret scanning, dependency scanning, SBOM, and static analysis are not
  configured in this repository and are reported `not_configured`.
- Backup/restore covers the local data directory; encrypted backups,
  external destinations, and selective restore are not implemented.
- Rollback is only available where data remains compatible; irreversible
  migrations are disclosed rather than silently rolled back.
