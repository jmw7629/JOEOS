"""Safe filesystem access bound to a registered project root.

Enforces a strict project boundary: absolute paths, parent traversal, and
symlink escapes are rejected. Secret-bearing files and content are masked.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .models import (
    DirectoryListing,
    DocumentRevision,
    DocumentState,
    DocumentWriteRequest,
    DocumentWriteResult,
    FileEntry,
)
from .secrets import SecretProtector, content_fingerprint, is_secret_path

TEXT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".json", ".md",
    ".txt", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".bash", ".zsh",
    ".swift", ".rs", ".go", ".c", ".h", ".cpp", ".hpp", ".java", ".kt", ".rb",
    ".php", ".sql", ".graphql", ".mjs", ".cjs", ".vue", ".svelte", ".svg",
    ".xml", ".csv", ".lock", ".gitignore", ".env.example",
}

MAX_TEXT_BYTES = 2_000_000
MAX_LISTING_ENTRIES = 2048

IGNORED_DIRECTORIES = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".next", ".turbo", ".cache", ".svn", ".hg"}


def language_for(name: str) -> Optional[str]:
    suffix = Path(name).suffix.lower()
    mapping = {
        ".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript",
        ".tsx": "typescript", ".html": "html", ".css": "css", ".json": "json",
        ".md": "markdown", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
        ".sh": "shell", ".bash": "shell", ".swift": "swift", ".rs": "rust",
        ".go": "go", ".c": "c", ".h": "c", ".cpp": "cpp", ".java": "java",
        ".kt": "kotlin", ".rb": "ruby", ".sql": "sql", ".svg": "svg",
        ".xml": "xml", ".vue": "vue", ".svelte": "svelte", ".mjs": "javascript",
        ".cjs": "javascript",
    }
    return mapping.get(suffix)


class PathBoundaryError(ValueError):
    pass


class FileConflictError(ValueError):
    pass


class FilesystemService:
    def __init__(
        self,
        root_provider: Callable[[str], Path],
        connection_factory: Callable[[], sqlite3.Connection],
        secret_protector: SecretProtector,
    ) -> None:
        self._root_provider = root_provider
        self._connection_factory = connection_factory
        self._protector = secret_protector

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_state (
                    project_id TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    modified_at TEXT,
                    sha256 TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, rel_path)
                );
                """
            )

    def resolve(self, project_id: str, rel_path: str) -> Path:
        root = self._root_provider(project_id).resolve()
        if "\x00" in rel_path:
            raise PathBoundaryError("Invalid path.")
        if not rel_path or rel_path in {".", "./"}:
            return root
        candidate = (root / rel_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            raise PathBoundaryError("Path escapes the project boundary.")
        if candidate.is_symlink():
            resolved = candidate.resolve(strict=False)
            try:
                resolved.relative_to(root)
            except ValueError:
                raise PathBoundaryError("Symlink escapes the project boundary.")
        return candidate

    def list_directory(self, project_id: str, rel_path: str = "", include_hidden: bool = False) -> DirectoryListing:
        directory = self.resolve(project_id, rel_path)
        if not directory.is_dir():
            raise PathBoundaryError("Not a directory.")
        entries: list[FileEntry] = []
        truncated = False
        try:
            children = sorted(directory.iterdir(), key=lambda item: (item.is_file(), item.name.lower()))
        except OSError as exc:
            raise PathBoundaryError("Directory is not readable.") from exc
        for child in children:
            name = child.name
            if child.is_dir() and name in IGNORED_DIRECTORIES:
                continue
            hidden = name.startswith(".")
            if hidden and not include_hidden and name not in {".gitignore", ".env.example"}:
                continue
            try:
                stat = child.stat()
                is_dir = child.is_dir()
                is_link = child.is_symlink()
            except OSError:
                continue
            entries.append(
                FileEntry(
                    name=name,
                    path=str(child.relative_to(directory)),
                    kind=("directory" if is_dir else "symlink" if is_link else "file"),
                    size=0 if is_dir else stat.st_size,
                    modified_at=_iso_from_epoch(stat.st_mtime),
                    language=None if is_dir else language_for(name),
                    git_state="clean",
                    is_secret=is_secret_path(name),
                    hidden=hidden,
                )
            )
            if len(entries) >= MAX_LISTING_ENTRIES:
                truncated = True
                break
        return DirectoryListing(project_id=project_id, directory=rel_path or ".", entries=tuple(entries), truncated=truncated)

    def read_document(self, project_id: str, rel_path: str) -> DocumentState:
        target = self.resolve(project_id, rel_path)
        if target.is_dir():
            raise PathBoundaryError("Path is a directory, not a file.")
        try:
            if target.stat().st_size > MAX_TEXT_BYTES:
                raise PathBoundaryError("File is too large to open as text.")
            raw = target.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            raise PathBoundaryError("File is binary or not UTF-8 text.")
        except OSError as exc:
            raise PathBoundaryError("File is not readable.") from exc
        rel = str(target.relative_to(self._root_provider(project_id).resolve()))
        masked, masked_count = self._protector.mask_text(raw, rel)
        revision = self._revision(project_id, rel_path, target)
        self._store_revision(project_id, rel_path, revision)
        return DocumentState(path=rel, content=masked, masked_secrets=masked_count, revision=revision)

    def write_document(self, project_id: str, request: DocumentWriteRequest) -> DocumentWriteResult:
        target = self.resolve(project_id, request.path)
        root = self._root_provider(project_id).resolve()
        rel = str(target.relative_to(root))
        if is_secret_path(rel) or target.name.startswith(".env"):
            raise PathBoundaryError("Secret-bearing files require explicit approval to edit.")
        if target.is_dir():
            raise PathBoundaryError("Path is a directory.")
        stored = self._load_revision(project_id, rel)
        if stored is not None and request.base_revision and request.base_revision != stored.sha256:            return DocumentWriteResult(
                path=rel,
                saved=False,
                conflict=True,
                conflict_message="The file changed elsewhere. Reload and review before overwriting.",
            )
        try:
            target.write_text(request.content, encoding="utf-8")
        except OSError as exc:
            raise PathBoundaryError("File could not be written.") from exc
        revision = self._revision(project_id, rel, target)
        self._store_revision(project_id, rel, revision)
        return DocumentWriteResult(path=rel, saved=True, revision=revision)

    def _revision(self, project_id: str, rel_path: str, target: Path) -> DocumentRevision:
        try:
            stat = target.stat()
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return DocumentRevision(size=0, modified_at=None, sha256=content_fingerprint(""))
        return DocumentRevision(
            size=stat.st_size,
            modified_at=_iso_from_epoch(stat.st_mtime),
            sha256=content_fingerprint(content),
        )

    def _store_revision(self, project_id: str, rel_path: str, revision: DocumentRevision) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO document_state(project_id, rel_path, size, modified_at, sha256, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, rel_path) DO UPDATE SET
                    size = excluded.size, modified_at = excluded.modified_at,
                    sha256 = excluded.sha256, updated_at = excluded.updated_at
                """,
                (project_id, rel_path, revision.size, revision.modified_at, revision.sha256, _utc_now()),
            )

    def _load_revision(self, project_id: str, rel_path: str) -> Optional[DocumentRevision]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT size, modified_at, sha256 FROM document_state WHERE project_id = ? AND rel_path = ?",
                (project_id, rel_path),
            ).fetchone()
        if row is None:
            return None
        return DocumentRevision(size=row["size"], modified_at=row["modified_at"], sha256=row["sha256"])


def _iso_from_epoch(epoch: float) -> Optional[str]:
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
