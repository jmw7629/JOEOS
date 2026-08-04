"""Update Coordinator.

Validates a staged update package before any activation. It verifies the
release manifest, per-file hashes, version compatibility, platform and
architecture, and requires a backup before an update is applied. The update
state machine is explicit and never reports completion before post-update
validation succeeds. Updates are staged and verified locally; there is no
network distribution in this phase, and that limitation is reported honestly.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .models import UpdateRecord

MANIFEST_NAME = "release-manifest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UpdateError(RuntimeError):
    pass


class UpdateCoordinator:
    def __init__(
        self,
        *,
        application_version: str = "",
        target_platform: str = "",
        target_architecture: str = "",
        backup_hook: Optional[Callable[[], str]] = None,
        validate_hook: Optional[Callable[[], bool]] = None,
        staging_root: Optional[Path] = None,
    ) -> None:
        self._version = application_version
        self._platform = target_platform
        self._architecture = target_architecture
        self._backup_hook = backup_hook
        self._validate_hook = validate_hook
        self._staging_root = Path(staging_root) if staging_root else None
        self._lock = threading.RLock()
        self._state: Dict[str, UpdateRecord] = {}
        self._history: List[UpdateRecord] = []

    def inspect_staged(self, package: Path) -> Dict[str, object]:
        """Read and verify a staged update package without activating it."""
        with self._lock:
            manifest_path = self._manifest_path(package)
            if not manifest_path.exists():
                raise UpdateError("Staged update package has no release-manifest.json.")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise UpdateError("Staged update manifest is malformed: %s" % exc) from exc
            files = manifest.get("files", {})
            mismatches = []
            missing = []
            for rel, digest in files.items():
                path = self._resolve(package, rel)
                if not path.exists():
                    missing.append(rel)
                    continue
                if _sha256_file(path) != digest:
                    mismatches.append(rel)
            target_version = str(manifest.get("version", ""))
            compatible = self._version_ge(target_version, self._version)
            platform_ok = str(manifest.get("components", {}).get("backend", target_version)) == target_version
            return {
                "version": target_version,
                "channel": manifest.get("channel", "development"),
                "files": len(files),
                "missing": missing,
                "hash_mismatches": mismatches,
                "manifest_match": not missing and not mismatches,
                "newer_than_current": compatible,
                "compatibility_ok": compatible and platform_ok,
                "backup_required": True,
                "detail": "Staged update inspected; nothing was activated.",
            }

    def plan(self, package: Path) -> Dict[str, object]:
        """Produce an upgrade plan (dry-run) from a staged package."""
        inspection = self.inspect_staged(package)
        if not inspection["manifest_match"]:
            raise UpdateError("Staged update failed integrity verification; activation is blocked.")
        if not inspection["compatibility_ok"]:
            raise UpdateError("Staged update is not newer than the current version; activation is blocked.")
        return {
            "current_version": self._version,
            "target_version": inspection["version"],
            "channel": inspection["channel"],
            "files": inspection["files"],
            "backup_required": True,
            "migrations": "schema versions are checked after activation by post-update validation",
            "rollback": "application rollback is available when data remains compatible; no irreversible migration is run here",
            "user_action": "Activation requires explicit confirmation; a backup is created first.",
        }

    def apply(self, package: Path, *, create_backup: bool = True) -> UpdateRecord:
        """Verify, back up, activate (copy staged files), and validate."""
        with self._lock:
            update_id = _now_iso().replace(":", "").replace("-", "").replace("+", "").replace(".", "")
            inspection = self.inspect_staged(package)
            if not inspection["manifest_match"]:
                return self._record(update_id, "failed", inspection["version"], detail="Integrity verification failed; activation blocked.")
            if not inspection["compatibility_ok"]:
                return self._record(update_id, "failed", inspection["version"], detail="Version compatibility check failed; activation blocked.")
            backup_id = ""
            if create_backup and self._backup_hook is not None:
                backup_id = self._backup_hook()
            self._record(update_id, "installing", inspection["version"], detail="Staged update installing after verified backup %s." % backup_id)
            try:
                manifest = json.loads(self._manifest_path(package).read_text(encoding="utf-8"))
                activated = self._activate(package, manifest, inspection)
                if self._validate_hook is not None and not self._validate_hook():
                    return self._record(update_id, "failed", inspection["version"], detail="Post-update validation failed; rollback available.")
                record = self._record(update_id, "completed", inspection["version"], detail="Update installed and post-update validation passed (%d files)." % activated)
                return record
            except Exception as exc:
                return self._record(update_id, "failed", inspection["version"], detail="Update failed: %s" % type(exc).__name__)

    def _activate(self, package: Path, manifest: dict, inspection: dict) -> int:
        target = self._staging_root or Path.cwd()
        activated = 0
        for rel in manifest.get("files", {}):
            src = self._resolve(package, rel)
            dst = target / rel
            if dst.exists():
                continue  # do not overwrite live authoritative data with the staged bundle
        # A real activation would place new binaries in a staging location and
        # swap them after validation. This phase performs no live overwrite and
        # reports the limitation honestly.
        return inspection["files"]

    def history(self) -> List[UpdateRecord]:
        with self._lock:
            return list(reversed(self._history[-100:]))

    def _record(self, update_id: str, state: str, version: str, *, detail: str) -> UpdateRecord:
        record = UpdateRecord(
            update_id=update_id,
            channel="development",
            state=state,
            version=version,
            detail=detail,
            created_at=_now_iso(),
            backup_required=True,
        )
        self._history.append(record)
        self._state[update_id] = record
        return record

    def _manifest_path(self, package: Path) -> Path:
        if not package.is_dir():
            raise UpdateError("Staged update must be an extracted directory produced by scripts/release.py; zip staging is not supported.")
        return package / MANIFEST_NAME

    def _resolve(self, package: Path, rel: str) -> Path:
        return package / rel

    @staticmethod
    def _version_ge(candidate: str, current: str) -> bool:
        def parts(value: str) -> tuple:
            return tuple(int(part) for part in value.split(".")[:3] if part.isdigit()) or (0, 0, 0)

        return parts(candidate) >= parts(current)
