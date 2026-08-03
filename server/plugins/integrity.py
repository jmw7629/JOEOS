"""Package integrity for the JoeOS Plugin Platform.

Every installed package carries an integrity record: SHA-256 hashes over the
canonical file inventory, the manifest, and the whole package. Content is
verified before activation and whenever integrity is checked. Any unexpected
mismatch disables the plugin and marks it compromised instead of proceeding.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Tuple

INVENTORY_FILE = "inventory.json"
MANIFEST_FILE = "manifest.json"

_ALGORITHM = "sha256"


class IntegrityError(RuntimeError):
    pass


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_inventory(package_dir: str) -> Tuple[Dict[str, str], str]:
    """Compute {relative_path: sha256} over a plugin directory.

    Only regular files are hashed; symlinks are skipped (they are rejected
    during install anyway). The inventory file itself is excluded so the root
    hash stays stable whether or not ``inventory.json`` has been written.
    """
    root = Path(package_dir).resolve()
    if not root.is_dir():
        raise IntegrityError("plugin package directory does not exist.")
    inventory: Dict[str, str] = {}
    for current, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in {".git", "__pycache__"}]
        for name in files:
            if name == INVENTORY_FILE:
                continue
            path = Path(current) / name
            if path.is_symlink():
                continue
            relative = str(path.relative_to(root)).replace(os.sep, "/")
            inventory[relative] = _file_sha256(path)
    payload = json.dumps(inventory, sort_keys=True, separators=(",", ":"))
    return inventory, _root_hash(payload)


def _root_hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_inventory_file(package_dir: str) -> str:
    inventory, root_hash = compute_inventory(package_dir)
    Path(package_dir, INVENTORY_FILE).write_text(
        json.dumps({"algorithm": _ALGORITHM, "root_hash": root_hash, "files": inventory}, sort_keys=True),
        encoding="utf-8",
    )
    return root_hash


def verify_inventory(package_dir: str, expected_root_hash: str) -> bool:
    """Verify that current content matches the recorded inventory root hash."""
    inventory, root_hash = compute_inventory(package_dir)
    del inventory
    return _constant_time_compare(root_hash, expected_root_hash)


def verify_file_set(package_dir: str, manifest: dict) -> Tuple[bool, str]:
    """Verify that a plugin directory is safe to load and matches its manifest.

    Returns (ok, reason). Rejects symlinks, path traversal, unexpected archive
    members, and missing entry points.
    """
    root = Path(package_dir).resolve()
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.is_file():
        return False, "manifest.json is missing."
    entry = manifest.get("entry_point") or {}
    module = str(entry.get("module") or "").replace(".", "/") + ".py"
    if not (root / module).is_file():
        return False, "entry point module is missing from the package."
    for current, directories, files in os.walk(root):
        directories[:] = [name for name in directories if name not in {".git", "__pycache__"}]
        for name in files:
            path = Path(current) / name
            if path.is_symlink():
                return False, "package contains a symbolic link."
            try:
                relative = path.resolve().relative_to(root)
            except ValueError:
                return False, "package contains a path outside its root."
            if ".." in str(relative) or (Path(current).resolve().name in {".git", "__pycache__"}):
                return False, "package contains an unexpected path."
            if path.name.endswith((".pyc", ".pyo")):
                return False, "package contains a compiled artifact."
    return True, "ok"


def _constant_time_compare(left: str, right: str) -> bool:
    return hashlib.sha256(left.encode("utf-8")).digest() == hashlib.sha256(
        right.encode("utf-8")
    ).digest()