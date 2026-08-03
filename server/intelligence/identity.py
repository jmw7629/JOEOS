"""Project identity and repository fingerprint derivation.

Identity facts come from Git and the filesystem. The fingerprint is a stable
hash built from selected components; it is stable across branches (it excludes
branch/commit state) so moving the repository does not change identity.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from .detect import detect_build_system, detect_frameworks, detect_languages, detect_package_manager, detect_test_system
from .models import (
    FingerprintComponent,
    ProjectIdentity,
    Provenance,
    RepositoryFingerprint,
)

_FINGERPRINT_FIELDS = ("name", "remote_url", "top_level_files", "language_manifest")


class IdentityService:
    def __init__(self, project_service) -> None:
        self._projects = project_service

    def derive(self, project_id: str, *, root_path: Optional[str] = None) -> ProjectIdentity:
        record = self._projects.get(project_id)
        root_path_value = root_path or record.path
        root = Path(root_path_value)
        now = _now()
        git = self._git_facts(root_path_value)
        files = self._top_level_files(root_path_value)
        language_detections = detect_languages(root)
        framework_detections = detect_frameworks(root)
        languages = tuple(d.language for d in language_detections if d.language != "unknown")
        frameworks = tuple(d.framework for d in framework_detections if d.framework != "none")
        package_manager = detect_package_manager(root)
        build_system = detect_build_system(root)
        test_system = detect_test_system(root)
        fingerprint = self._fingerprint(project_id, record.name, git.remote_url, files, languages)
        return ProjectIdentity(
            project_id=project_id,
            name=record.name,
            root=root_path_value,
            repository_root=git.repository_root,
            remote_url=git.remote_url,
            default_branch=git.default_branch,
            active_branch=git.active_branch,
            current_commit=git.current_commit,
            dirty=git.dirty,
            fingerprint=fingerprint.fingerprint,
            project_type=self._project_type(languages, frameworks),
            languages=tuple(sorted(languages)),
            frameworks=tuple(sorted(frameworks)),
            package_manager=package_manager,
            build_system=build_system,
            test_system=test_system,
            registered_at=record.created_at or now,
        )

    def fingerprint(self, project_id: str, name: str, remote_url: Optional[str], root: str) -> RepositoryFingerprint:
        files = self._top_level_files(root)
        languages = tuple(d.language for d in detect_languages(Path(root)) if d.language != "unknown")
        return self._fingerprint(project_id, name, remote_url, files, languages)

    def _fingerprint(
        self, project_id: str, name: str, remote_url: Optional[str], top_level: Tuple[str, ...], languages: Tuple[str, ...]
    ) -> RepositoryFingerprint:
        now = _now()
        components: List[FingerprintComponent] = []
        components.append(
            FingerprintComponent(
                name="name",
                value=name,
                provenance=Provenance(kind="file_system", source="project registration", detected_at=now),
            )
        )
        if remote_url:
            components.append(
                FingerprintComponent(
                    name="remote_url",
                    value=remote_url,
                    provenance=Provenance(kind="git", source="git remote get-url origin", detected_at=now),
                )
            )
        components.append(
            FingerprintComponent(
                name="top_level_files",
                value="|".join(sorted(top_level))[:240],
                stable_across_branches=False,
                provenance=Provenance(kind="file_system", source="root directory listing", detected_at=now),
            )
        )
        if languages:
            components.append(
                FingerprintComponent(
                    name="languages",
                    value="|".join(sorted(languages))[:240],
                    provenance=Provenance(kind="classification", source="language detection", detected_at=now),
                )
            )
        digest = hashlib.sha256("|".join(c.value for c in components).encode("utf-8")).hexdigest()
        return RepositoryFingerprint(
            project_id=project_id,
            fingerprint=digest[:32],
            components=tuple(components),
            generated_at=now,
        )

    def _git_facts(self, root: str) -> "GitFacts":
        try:
            remote = _run(["git", "-C", root, "remote", "get-url", "origin"])
        except (subprocess.SubprocessError, OSError):
            remote = None
        try:
            default_branch = _run(["git", "-C", root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
        except (subprocess.SubprocessError, OSError):
            default_branch = None
        if default_branch and default_branch.startswith("refs/remotes/origin/"):
            default_branch = default_branch[len("refs/remotes/origin/"):]
        try:
            active_branch = _run(["git", "-C", root, "branch", "--show-current"])
        except (subprocess.SubprocessError, OSError):
            active_branch = None
        try:
            current_commit = _run(["git", "-C", root, "rev-parse", "--short", "HEAD"])
        except (subprocess.SubprocessError, OSError):
            current_commit = None
        dirty = False
        try:
            result = subprocess.run(
                ["git", "-C", root, "status", "--porcelain"],
                capture_output=True, text=True, timeout=15,
            )
            dirty = result.returncode == 0 and bool(result.stdout.strip())
        except (subprocess.SubprocessError, OSError):
            dirty = False
        repository_root = None
        try:
            repository_root = _run(["git", "-C", root, "rev-parse", "--show-toplevel"])
        except (subprocess.SubprocessError, OSError):
            repository_root = None
        return GitFacts(remote, default_branch, active_branch, current_commit, dirty, repository_root)

    def _top_level_files(self, root: str) -> Tuple[str, ...]:
        try:
            import os

            entries = sorted(os.listdir(root))
        except OSError:
            return ()
        return tuple(e for e in entries if not e.startswith("."))

    def _project_type(self, languages: Tuple[str, ...], frameworks: Tuple[str, ...]) -> str:
        combined = " ".join(languages).lower() + " " + " ".join(frameworks).lower()
        if any(f in combined for f in ("react", "vue", "svelte", "next")):
            return "web_frontend"
        if "fastapi" in combined or "flask" in combined or "django" in combined:
            return "web_backend"
        if "pytest" in combined or "unittest" in combined:
            return "python_test_suite"
        if any(f in combined for f in ("joeos", "os", "platform", "backend", "workspace")):
            return "platform"
        return "application"


class GitFacts:
    def __init__(
        self,
        remote_url: Optional[str],
        default_branch: Optional[str],
        active_branch: Optional[str],
        current_commit: Optional[str],
        dirty: bool,
        repository_root: Optional[str],
    ) -> None:
        self.remote_url = remote_url
        self.default_branch = default_branch
        self.active_branch = active_branch
        self.current_commit = current_commit
        self.dirty = dirty
        self.repository_root = repository_root


def _run(args: List[str]) -> Optional[str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
