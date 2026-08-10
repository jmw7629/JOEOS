"""ModuleCatalog — authoritative module registry.

Ships the product's built-in modules (declarative, cross-platform) and provides
the API shape clients consume. Persisted user modules (created via the module
builder) are stored as validated manifests and layered on top of the built-ins.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from .manifest import ManifestValidationError, ModuleManifest, module_manifest_from

_SCHEMA = """
CREATE TABLE IF NOT EXISTS module_definitions (
    module_id TEXT PRIMARY KEY,
    manifest TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'builtin',   -- builtin | user | workspace
    owner_id TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    superseded INTEGER NOT NULL DEFAULT 0
);
"""


class ModuleCatalog:
    """Validated, persisted registry of ModuleManifest definitions.

    Built-in modules are seeded on prepare() and are always available. User and
    workspace modules are validated manifests stored in the catalog database;
    unknown component types are rejected at write time so clients never receive
    an unrenderable definition.
    """

    def __init__(self, data_dir: str) -> None:
        path = Path(data_dir)
        path.mkdir(parents=True, exist_ok=True)
        self._db_path = path / "modules.db"

        def connect() -> sqlite3.Connection:
            connection = sqlite3.connect(str(self._db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 10000")
            return connection

        self._connect = connect

    def prepare(self) -> None:
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    # ---- built-in seed ---------------------------------------------------

    def seed_builtin(self, manifest: ModuleManifest) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO module_definitions(module_id, manifest, scope, owner_id, created_at, updated_at, superseded) "
                "VALUES (?, ?, 'builtin', '', 0, 0, 0)",
                (manifest.id, module_manifest_to_json(manifest)),
            )

    # ---- CRUD ------------------------------------------------------------

    def list(self, *, include_hidden: bool = False) -> List[ModuleManifest]:
        rows = self._connect().execute(
            "SELECT manifest FROM module_definitions WHERE superseded = 0 ORDER BY updated_at DESC"
        ).fetchall()
        manifests: List[ModuleManifest] = []
        for row in rows:
            try:
                manifest = module_manifest_from(row["manifest"])
            except ManifestValidationError:
                continue
            if not include_hidden and manifest.visibility == "hidden":
                continue
            manifests.append(manifest)
        return manifests

    def get(self, module_id: str) -> Optional[ModuleManifest]:
        row = self._connect().execute(
            "SELECT manifest FROM module_definitions WHERE module_id = ? AND superseded = 0",
            (module_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return module_manifest_from(row["manifest"])
        except ManifestValidationError:
            return None

    def put(self, manifest: ModuleManifest, *, scope: str = "user", owner_id: str = "") -> ModuleManifest:
        """Upsert a validated user/workspace module. Rejects invalid manifests."""
        if scope not in ("user", "workspace"):
            raise ManifestValidationError("module scope must be user or workspace")
        with self._connect() as connection:
            connection.execute(
                "UPDATE module_definitions SET superseded = 1 WHERE module_id = ? AND superseded = 0",
                (manifest.id,),
            )
            connection.execute(
                "INSERT INTO module_definitions(module_id, manifest, scope, owner_id, created_at, updated_at, superseded) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
                (manifest.id, module_manifest_to_json(manifest), scope, owner_id, 0, 0),
            )
        return manifest

    def remove(self, module_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE module_definitions SET superseded = 1 WHERE module_id = ? AND superseded = 0",
                (module_id,),
            )
        return cursor.rowcount > 0


def module_manifest_to_json(manifest: ModuleManifest) -> str:
    import json as _json
    return _json.dumps(manifest.to_dict(), sort_keys=True)
