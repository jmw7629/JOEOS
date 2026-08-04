# Releasing JoeOS

This document is the authoritative release engineering process for JoeOS. It
mirrors what `scripts/release.py` automates and documents the version policy,
validation gates, and honest limitations.

## Version policy

- The **backend version** (`JOEOS_VERSION` in `joeos_backend.py`) is the single
  authoritative release version. It must be a `major.minor.patch` semantic
  version.
- The **web manifest** (`manifest.webmanifest` `"version"`) must match the
  backend version. `scripts/version.check_consistency()` enforces this.
- **Internal packages** (`packages/*/package.json`) version independently by
  policy. They are reported in the release manifest, not forced to match the
  app version.

## Release steps

1. Ensure the working tree is committed and clean:
   `git status --short`
2. Bump `JOEOS_VERSION` in `joeos_backend.py` and the matching
   `"version"` in `manifest.webmanifest`.
3. Verify consistency (read-only):
   `python scripts/release.py --check`
4. Run the full validation gates:
   - `python -m pytest tests/ -q`
   - `node --test tests/frontend.test.mjs` (via `node tests/frontend.test.mjs`)
   - `node --test packages/sdk/tests/client.test.mjs`
   - `python -m pytest packages/plugin-sdk/tests -q`
5. Smoke-package into a temporary directory (no repo writes):
   `python scripts/release.py --dry-run`
6. Produce the versioned bundle:
   `python scripts/release.py --package ./dist`
   This writes `dist/joeos-<version>/` with the backend, `server/`, web
   assets, SDK, launcher scripts, requirements, and release documentation,
   plus a `release-manifest.json` containing per-file SHA-256 digests,
   component versions, and the source commit.

## Validation gates

Every release must pass:

- Version consistency (`scripts/release.py --check`).
- Full Python unit suite (`tests/`).
- Frontend structural tests (`tests/frontend.test.mjs`).
- Client and plugin SDK tests.
- A dry-run package that produces a complete bundle and hash manifest.
- `python scripts/doctor.py` diagnostics (no fail-state checks).
- Production release gates reported by `GET /api/v1/production/status`; scans,
  signing, SBOM, and update distribution are honestly `not_configured`.

## Production readiness platform

`server/production/` provides the release status, versioned migrations,
verified backups, staged restores with security-state reset, staged update
verification, Safe Mode / Repair Mode / crash-loop recovery, and the `doctor`
CLI. See `docs/architecture/PRODUCTION_READINESS.md` for details and honest
guarantees.

## Honest limitations

- No CI pipeline exists in this repository yet; the release steps are run
  locally. A future CI configuration should invoke the same commands.
- No signed artifacts or checksum verification workflow is implemented.
  `release-manifest.json` provides digests for manual verification.
- No signing, notarization, package-manager publication, container
  publication, or mobile-store distribution is implemented or claimed.
- Browser E2E and visual-regression tooling require runtimes not installed on
  this machine; they are not release gates here.
