"""Engineering workspace: unified facade over projects, files, git, secrets,
commands, and search, with an activity log and root-bounds enforcement."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from .commands import CommandService, CommandValidation
from .filesystem import FilesystemService
from .git import GitService
from .models import (
    ActivityEntry,
    CommandResult,
    DirectoryListing,
    DocumentState,
    DocumentWriteRequest,
    DocumentWriteResult,
    GitStatus,
    ProjectRecord,
    ProjectEnvelope,
    SearchEnvelope,
    SecretScanResult,
    TrustState,
)
from .projects import ProjectService
from .search import SearchService
from .secrets import SecretProtector


class EngineeringService:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        event_sink: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._protector = SecretProtector()
        self.projects = ProjectService(connection_factory, event_sink)
        self.filesystem = FilesystemService(self.projects.root_path, connection_factory, self._protector)
        self._searcher = SearchService(self.projects.root_path)
        self.commands = CommandService(
            self.projects.root_path,
            self._trust_provider,
            self.record_activity,
        )
        self._git_services: dict = {}
        self.prepare()

    def prepare(self) -> None:
        self.projects.prepare()
        self.filesystem.prepare()
        self._create_activity_table()

    def _create_activity_table(self) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS engineering_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _trust_provider(self, project_id: str) -> TrustState:
        return self.projects.get(project_id).trust_state

    def _git(self, project_id: str) -> GitService:
        root = self.projects.root_path(project_id)
        service = self._git_services.get(project_id)
        if service is None:
            service = GitService(root, self._protector)
            self._git_services[project_id] = service
        return service

    def register_project(self, name: str, root_path: str) -> ProjectRecord:
        return self.projects.register(root_path, name)

    def list_projects(self) -> ProjectEnvelope:
        return ProjectEnvelope(projects=self.projects.list())

    def get_project(self, project_id: str) -> ProjectRecord:
        return self.projects.get(project_id)

    def set_project_trust(self, project_id: str, state: TrustState) -> ProjectRecord:
        return self.projects.set_trust(project_id, state)

    def remove_project(self, project_id: str) -> None:
        self.projects.remove(project_id)
        self._git_services.pop(project_id, None)

    def list_directory(self, project_id: str, rel_path: str = "", include_hidden: bool = False) -> DirectoryListing:
        self._require_project(project_id)
        return self.filesystem.list_directory(project_id, rel_path, include_hidden)

    def read_document(self, project_id: str, rel_path: str) -> DocumentState:
        self._require_project(project_id)
        return self.filesystem.read_document(project_id, rel_path)

    def write_document(self, project_id: str, request: DocumentWriteRequest) -> DocumentWriteResult:
        self._require_project(project_id)
        return self.filesystem.write_document(project_id, request)

    def git_status(self, project_id: str) -> GitStatus:
        self._require_project(project_id)
        return self._git(project_id).status().model_copy(update={"project_id": project_id})

    def git_diff(self, project_id: str, path: Optional[str] = None, staged: bool = False):
        self._require_project(project_id)
        return self._git(project_id).diff(path, staged)

    def git_stage(self, project_id: str, paths: List[str]) -> None:
        self._require_project(project_id)
        self._git(project_id).stage(paths)
        self.record_activity(project_id, "git", "staged")

    def git_unstage(self, project_id: str, paths: List[str]) -> None:
        self._require_project(project_id)
        self._git(project_id).unstage(paths)
        self.record_activity(project_id, "git", "unstaged")

    def git_commit(self, project_id: str, message: str, *, approved: bool = False):
        self._require_project(project_id)
        result = self._git(project_id).commit(message, approved=approved)
        self.record_activity(project_id, "git", "committed")
        return result.model_copy(update={"project_id": project_id})

    def scan_secrets(self, project_id: str) -> SecretScanResult:
        self._require_project(project_id)
        root = self.projects.root_path(project_id)
        return self._protector.scan_repository(root)

    def validate_command(self, project_id: str, command: str) -> CommandValidation:
        self._require_project(project_id)
        return self.commands.validate(project_id, command)

    def execute_command(self, project_id: str, command: str, *, approved: bool = False) -> CommandResult:
        self._require_project(project_id)
        return self.commands.execute(project_id, command, approved=approved)

    def search(self, project_id: str, query: str, *, file_pattern: Optional[str] = None) -> SearchEnvelope:
        self._require_project(project_id)
        return self._searcher.search(project_id, query, file_pattern=file_pattern)

    def record_activity(self, project_id: str, kind: str, status: str, detail: str = "") -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO engineering_activity(project_id, kind, status, detail, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (project_id, kind, status, detail, _now()),
            )

    def activity(self, project_id: str, limit: int = 20) -> Tuple[ActivityEntry, ...]:
        self._require_project(project_id)
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, kind, status, detail, created_at
                FROM engineering_activity
                WHERE project_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (project_id, max(1, min(200, limit))),
            ).fetchall()
        return tuple(ActivityEntry(**dict(row)) for row in rows)

    def _require_project(self, project_id: str) -> None:
        self.projects.get(project_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
