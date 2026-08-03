"""Authoritative project registry with evidence-based detection and trust."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .models import CharacteristicEvidence, ProjectRecord, TrustState

ID_SAFE = re.compile(r"^[a-z0-9_-]+$")

EVIDENCE_RULES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("package.json",), "npm"),
    (("pnpm-lock.yaml",), "pnpm"),
    (("yarn.lock",), "yarn"),
    (("bun.lockb", "bun.lock", "bunfig.toml"), "bun"),
    (("pyproject.toml",), "poetry"),
    (("requirements.txt",), "pip"),
    (("Pipfile",), "pipenv"),
    (("Cargo.toml",), "cargo"),
    (("go.mod",), "go"),
    (("Package.swift",), "swift"),
    (("pom.xml", "build.gradle", "build.gradle.kts"), "java"),
    (("Gemfile",), "ruby"),
    (("composer.json",), "composer"),
    (("Dockerfile", "docker-compose.yml", "docker-compose.yaml"), "docker"),
    ((".github/workflows",), "github_actions"),
    (("vite.config.js", "vite.config.ts"), "vite"),
    (("next.config.js", "next.config.mjs", "next.config.ts"), "nextjs"),
    (("svelte.config.js",), "svelte"),
    (("Makefile",), "make"),
    (("CMakeLists.txt",), "cmake"),
    (("project.yml", "project.yaml"), "xcode"),
)


class ProjectNotFoundError(KeyError):
    pass


class ProjectPathError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def detect_characteristics(root: Path) -> Tuple[CharacteristicEvidence, ...]:
    evidence: List[CharacteristicEvidence] = []
    try:
        git = (root / ".git").exists()
    except OSError:
        git = False
    if git:
        evidence.append(
            CharacteristicEvidence(characteristic="git_repository", source_file=".git", confidence="reported")
        )
    for candidates, characteristic in EVIDENCE_RULES:
        for candidate in candidates:
            candidate_path = root / candidate
            try:
                exists = candidate_path.exists()
            except OSError:
                exists = False
            if exists:
                evidence.append(
                    CharacteristicEvidence(
                        characteristic=characteristic,
                        source_file=candidate,
                        confidence="reported",
                    )
                )
                break
    _detect_source_languages(root, evidence)
    return tuple(evidence)


def _detect_source_languages(root: Path, evidence: List[CharacteristicEvidence]) -> None:
    sources = {
        "python": ".py",
        "typescript": ".ts",
        "javascript": ".js",
        "swift": ".swift",
        "rust": ".rs",
        "go": ".go",
        "java": ".java",
        "kotlin": ".kt",
        "ruby": ".rb",
        "c": ".c",
        "cpp": ".cpp",
        "shell": ".sh",
        "sql": ".sql",
        "html": ".html",
        "css": ".css",
        "markdown": ".md",
    }
    detected: List[str] = []
    try:
        children = list(root.iterdir())
    except OSError:
        children = []
    for child in children:
        if not child.is_file():
            continue
        for characteristic, suffix in sources.items():
            if child.name.endswith(suffix):
                detected.append(characteristic)
                break
    existing = {item.characteristic for item in evidence}
    for characteristic in detected:
        if characteristic not in existing:
            evidence.append(
                CharacteristicEvidence(
                    characteristic=characteristic,
                    source_file="source files",
                    confidence="inferred",
                )
            )


def fingerprint_project(root: Path) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    parts: List[str] = [str(root.resolve())]
    try:
        parts.append("git=" + _git_remote(root))
    except (OSError, ValueError):
        parts.append("git=unknown")
    try:
        root_stat = root.stat()
        parts.append("mtime=%d" % int(root_stat.st_mtime))
        parts.append("ino=%d" % root_stat.st_ino)
    except OSError:
        warnings.append("Project root is not currently accessible.")
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]
    return digest, warnings


def _git_remote(root: Path) -> str:
    git_dir = root / ".git"
    config = git_dir / "config"
    if not config.exists():
        return ""
    text = config.read_text(encoding="utf-8", errors="replace")
    match = re.search(r'(?im)^\s*url\s*=\s*(.+?)\s*$', text)
    if match:
        first = [line for line in text.splitlines() if re.search(r"(?i)\burl\s*=", line)]
        if first:
            return first[0].split("=", 1)[1].strip()
    return ""


class ProjectService:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        event_sink: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._event_sink = event_sink

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    trust_state TEXT NOT NULL DEFAULT 'untrusted'
                        CHECK(trust_state IN ('untrusted', 'session', 'trusted')),
                    fingerprint TEXT NOT NULL,
                    characteristics_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL,
                    healthy INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def register(self, path: str, name: Optional[str] = None) -> ProjectRecord:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ProjectPathError("The project path is not an accessible directory: %s" % path)
        characteristics = detect_characteristics(root)
        fingerprint, warnings = fingerprint_project(root)
        project_id = _slugify(name or root.name)
        now = _now()
        try:
            with self._connection_factory() as connection:
                connection.execute(
                    """
                    INSERT INTO projects (
                        project_id, name, path, trust_state, fingerprint,
                        characteristics_json, warnings_json, healthy, created_at, updated_at
                    ) VALUES (?, ?, ?, 'untrusted', ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        name = excluded.name,
                        updated_at = excluded.updated_at
                    """,
                    (
                        project_id,
                        root.name,
                        str(root),
                        fingerprint,
                        _json([item.model_dump() for item in characteristics]),
                        _json(warnings),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ProjectPathError("A project with that identity already exists.") from exc
        if self._event_sink:
            self._event_sink("info", "projects", "%s project registered." % root.name)
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT project_id FROM projects WHERE path = ?", (str(root),)
            ).fetchone()
        return self.get(row["project_id"] if row is not None else project_id)

    def list(self) -> Tuple[ProjectRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM projects ORDER BY name ASC").fetchall()
        return tuple(self._record(row) for row in rows)

    def get(self, project_id: str) -> ProjectRecord:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ProjectNotFoundError(project_id)
        return self._record(row)

    def set_trust(self, project_id: str, state: TrustState) -> ProjectRecord:
        if state not in {"untrusted", "session", "trusted"}:
            raise ValueError("Unknown trust state.")
        now = _now()
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE projects SET trust_state = ?, updated_at = ? WHERE project_id = ?",
                (state, now, project_id),
            )
            if cursor.rowcount == 0:
                raise ProjectNotFoundError(project_id)
        if self._event_sink:
            self._event_sink("info", "projects", "%s trust set to %s." % (project_id, state))
        return self.get(project_id)

    def remove(self, project_id: str) -> None:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "DELETE FROM projects WHERE project_id = ?", (project_id,)
            )
            if cursor.rowcount == 0:
                raise ProjectNotFoundError(project_id)
        if self._event_sink:
            self._event_sink("info", "projects", "%s removed from JoeOS (files preserved)." % project_id)

    def root_path(self, project_id: str) -> Path:
        record = self.get(project_id)
        root = Path(record.path).expanduser().resolve()
        if not root.is_dir():
            raise ProjectPathError("Project directory is not accessible: %s" % record.path)
        return root

    @staticmethod
    def _record(row: sqlite3.Row) -> ProjectRecord:
        return ProjectRecord(
            project_id=row["project_id"],
            name=row["name"],
            path=row["path"],
            trust_state=row["trust_state"],
            fingerprint=row["fingerprint"],
            characteristics=tuple(
                CharacteristicEvidence(**item)
                for item in _load_object(row["characteristics_json"])
            ),
            warnings=tuple(_load_object(row["warnings_json"])),
            healthy=bool(row["healthy"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not slug or not ID_SAFE.fullmatch(slug):
        return "project-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return slug


def _json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load_object(value: str) -> List[Dict[str, object]]:
    import json

    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []
