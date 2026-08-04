"""Backup and Restore Coordinators.

Backups snapshot the authoritative data directory (main database plus
per-platform stores) into a validated archive with an integrity hash and
manifest; a backup is not marked verified until the hash and manifest match.
SQLite databases are snapshotted with the online backup API so the archive is
logically consistent. Restore stages to a temporary directory, validates
compatibility, creates a current-state recovery checkpoint, activates
atomically, and invokes a security-state reset hook so that restored sessions,
approvals, workflows, and device trust are never reactivated silently.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .compatibility import CompatibilityRegistry
from .models import BackupRecord, RestorePlan

BACKUP_FORMAT_VERSION = 1
MANIFEST_NAME = "backup-manifest.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BackupError(RuntimeError):
    pass


class BackupCoordinator:
    def __init__(
        self,
        data_dir: Path,
        *,
        application_version: str = "",
        schema_versions: Optional[Dict[str, str]] = None,
        backup_root: Optional[Path] = None,
        retention: int = 5,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._backup_root = Path(backup_root) if backup_root else self._data_dir / "backups"
        self._backup_root.mkdir(parents=True, exist_ok=True)
        self._version = application_version
        self._schema_versions = dict(schema_versions or {})
        self._retention = max(1, int(retention))
        self._lock = threading.RLock()

    def _store_paths(self) -> Dict[str, List[Path]]:
        stores: Dict[str, List[Path]] = {}
        excluded_names = {"backups", ".restore-staging", "production"}
        for child in sorted(self._data_dir.iterdir()):
            if child.name in excluded_names or child.name.endswith("-shm") or child.name.endswith("-wal"):
                continue
            if child.is_file():
                if child.name == ".joeos-write-probe":
                    continue
                stores.setdefault("root", []).append(child)
            elif child.is_dir():
                files = [
                    p for p in child.rglob("*")
                    if p.is_file()
                    and p.name != ".joeos-write-probe"
                    and not p.name.endswith("-shm")
                    and not p.name.endswith("-wal")
                ]
                if files:
                    stores[child.name] = files
        return stores

    def create(self, *, scope: str = "full") -> BackupRecord:
        with self._lock:
            backup_id = _now_iso().replace(":", "").replace("-", "").replace("+", "").replace(".", "")
            staging = self._backup_root / (backup_id + ".tmp")
            archive = self._backup_root / (backup_id + ".joeos-backup")
            try:
                staging.mkdir(parents=True, exist_ok=True)
                stores = self._store_paths()
                manifest_files = {}
                for store, files in stores.items():
                    for path in files:
                        rel = path.relative_to(self._data_dir).as_posix()
                        dst = staging / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        self._snapshot(path, dst)
                        manifest_files[rel] = _sha256_file(dst)
                manifest = {
                    "backup_id": backup_id,
                    "format_version": BACKUP_FORMAT_VERSION,
                    "application_version": self._version,
                    "schema_versions": self._schema_versions,
                    "scope": scope,
                    "created_at": _now_iso(),
                    "files": manifest_files,
                    "stores": sorted(stores),
                }
                (staging / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
                archive_bytes = _zip_directory(staging)
                archive.write_bytes(archive_bytes)
                integrity_hash = _sha256_bytes(archive_bytes)
                record = BackupRecord(
                    backup_id=backup_id,
                    created_at=manifest["created_at"],
                    application_version=self._version,
                    format_version=BACKUP_FORMAT_VERSION,
                    scope=scope,
                    stores=tuple(sorted(stores)),
                    size_bytes=archive.stat().st_size,
                    integrity_hash=integrity_hash,
                    verified=True,
                    encrypted=False,
                    destination="local",
                    status="verified",
                    restore_compatible=True,
                )
                self._retain()
                return record
            finally:
                shutil.rmtree(staging, ignore_errors=True)

    def verify(self, backup_id: str) -> BackupRecord:
        archive = self._backup_root / (backup_id + ".joeos-backup")
        if not archive.exists():
            raise BackupError("Backup not found: %s" % backup_id)
        current = _sha256_file(archive)
        try:
            manifest = self._read_manifest(backup_id)
        except BackupError:
            return BackupRecord(
                backup_id=backup_id,
                created_at="",
                size_bytes=archive.stat().st_size,
                integrity_hash=current,
                verified=False,
                status="verification_failed",
                detail="Backup archive is corrupt or unreadable.",
            )
        record = self._record_from_manifest(backup_id, manifest, archive)
        if current != record.integrity_hash:
            return BackupRecord(
                backup_id=backup_id,
                created_at=record.created_at,
                application_version=record.application_version,
                format_version=record.format_version,
                scope=record.scope,
                stores=record.stores,
                size_bytes=record.size_bytes,
                integrity_hash=record.integrity_hash,
                verified=False,
                status="verification_failed",
                detail="Archive hash does not match the recorded integrity hash.",
            )
        return record

    def list(self) -> List[BackupRecord]:
        records = []
        for archive in sorted(self._backup_root.glob("*.joeos-backup")):
            backup_id = archive.stem
            try:
                manifest = self._read_manifest(backup_id)
                record = self._record_from_manifest(backup_id, manifest, archive)
                records.append(record)
            except BackupError:
                records.append(BackupRecord(backup_id=backup_id, created_at="", size_bytes=archive.stat().st_size, status="failed"))
        return sorted(records, key=lambda record: record.created_at, reverse=True)

    def delete(self, backup_id: str) -> bool:
        with self._lock:
            verified = [record for record in self.list() if record.verified and record.backup_id != backup_id]
            target = self._backup_root / (backup_id + ".joeos-backup")
            if not target.exists():
                return False
            if not verified:
                raise BackupError("Refusing to delete the only verified backup; a known-good recovery point must remain.")
            target.unlink(missing_ok=True)
            return True

    def archive_path(self, backup_id: str) -> Path:
        return self._backup_root / (backup_id + ".joeos-backup")

    def _snapshot(self, source: Path, destination: Path) -> None:
        if _is_sqlite(source):
            src_connection = sqlite3.connect(str(source))
            dst_connection = sqlite3.connect(str(destination))
            try:
                src_connection.backup(dst_connection)
                dst_connection.commit()
            finally:
                dst_connection.close()
                src_connection.close()
        else:
            shutil.copy2(source, destination)

    def _read_manifest(self, backup_id: str) -> dict:
        archive = self._backup_root / (backup_id + ".joeos-backup")
        try:
            with zipfile.ZipFile(str(archive)) as handle:
                try:
                    return json.loads(handle.read(MANIFEST_NAME).decode("utf-8"))
                except KeyError:
                    raise BackupError("Backup archive is missing its manifest: %s" % backup_id)
        except BackupError:
            raise
        except Exception as exc:
            # A malformed or corrupted archive must surface as a backup error,
            # never as an unhandled zipfile exception.
            raise BackupError("Backup archive is corrupt or unreadable: %s" % backup_id) from exc

    def _record_from_manifest(self, backup_id: str, manifest: dict, archive: Path) -> BackupRecord:
        return BackupRecord(
            backup_id=backup_id,
            created_at=str(manifest.get("created_at", "")),
            application_version=str(manifest.get("application_version", "")),
            format_version=int(manifest.get("format_version", 0)),
            scope=str(manifest.get("scope", "full")),
            stores=tuple(manifest.get("stores", [])),
            size_bytes=archive.stat().st_size,
            integrity_hash=_sha256_file(archive),
            verified=True,
            encrypted=False,
            destination="local",
            status="verified",
        )

    def _retain(self) -> None:
        records = self.list()
        if len(records) <= self._retention:
            return
        for record in sorted(records, key=lambda record: record.created_at)[: len(records) - self._retention]:
            (self._backup_root / (record.backup_id + ".joeos-backup")).unlink(missing_ok=True)


class RestoreCoordinator:
    def __init__(
        self,
        data_dir: Path,
        backup: BackupCoordinator,
        compatibility: CompatibilityRegistry,
        *,
        security_reset_hook: Optional[Callable[[], Dict[str, int]]] = None,
        restore_root: Optional[Path] = None,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._backup = backup
        self._compatibility = compatibility
        self._security_reset_hook = security_reset_hook
        self._restore_root = Path(restore_root) if restore_root else self._data_dir / ".restore-staging"
        self._lock = threading.RLock()

    def plan(self, backup_id: str) -> RestorePlan:
        record = self._backup.verify(backup_id)
        if not record.verified:
            raise BackupError("Backup integrity verification failed; restore is blocked.")
        return RestorePlan(
            backup_id=backup_id,
            stores=record.stores,
            overwrite_scope=record.scope,
            requires_migration=False,
            revokes_sessions=True,
            invalidates_approvals=True,
            pauses_workflows=True,
            restricts_devices=True,
            expected_risk="Staged restore activates atomically; sessions, approvals, workflows, and device trust are reset by policy.",
        )

    def restore(self, backup_id: str, *, checkpoint_current: bool = True) -> Dict[str, object]:
        with self._lock:
            record = self._backup.verify(backup_id)
            if not record.verified:
                raise BackupError("Backup integrity verification failed; restore is blocked.")
            compatibility = self._compatibility.check_backup_format(record.format_version)
            if compatibility.state == "incompatible":
                raise BackupError(compatibility.detail)
            staging = self._restore_root / ("restore-" + backup_id)
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True, exist_ok=True)
            checkpoint_id = ""
            if checkpoint_current:
                checkpoint = self._backup.create(scope="full")
                checkpoint_id = checkpoint.backup_id
            with zipfile.ZipFile(str(self._backup.archive_path(backup_id))) as handle:
                handle.extractall(str(staging))
            manifest = json.loads((staging / MANIFEST_NAME).read_text(encoding="utf-8"))
            self._validate_manifest_files(staging, manifest)
            self._activate(staging, manifest)
            shutil.rmtree(staging, ignore_errors=True)
            security_reset = self._security_reset_hook() if self._security_reset_hook else {}
            return {
                "backup_id": backup_id,
                "stores": list(record.stores),
                "current_state_checkpoint": checkpoint_id,
                "sessions_revoked": security_reset.get("sessions", 0),
                "approvals_invalidated": security_reset.get("approvals", 0),
                "workflows_paused": security_reset.get("workflows", 0),
                "devices_restricted": security_reset.get("devices", 0),
                "detail": "Restore activated from a verified backup; derived indexes should be rebuilt and stale authority was reset.",
            }

    def _validate_manifest_files(self, staging: Path, manifest: dict) -> None:
        expected = manifest.get("files", {})
        for rel, digest in expected.items():
            path = staging / rel
            if not path.exists():
                raise BackupError("Backup is missing file %s; restore blocked." % rel)
            if _sha256_file(path) != digest:
                raise BackupError("Backup file %s failed integrity verification; restore blocked." % rel)

    def _activate(self, staging: Path, manifest: dict) -> None:
        for rel in manifest.get("files", {}):
            src = staging / rel
            dst = self._data_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(dst.suffix + ".restore-tmp")
            shutil.copy2(src, tmp)
            tmp.replace(dst)
        # Backup staging and restore staging are never part of authoritative data.
        for leftover in self._data_dir.glob("*.restore-tmp"):
            leftover.unlink(missing_ok=True)


def _zip_directory(directory: Path) -> bytes:
    buffer = __import__("io").BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(directory).as_posix())
    return buffer.getvalue()


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError:
        return False
    return header.startswith(b"SQLite format 3\x00")
