"""Controlled, read-first Git service.

All Git commands run with explicit argument lists (never a shell) inside the
project root with bounded output and timeouts. Destructive operations require
approval; commits are blocked when staged changes contain likely secrets.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from .models import (
    CommitResult,
    DiffEntry,
    DiffHunk,
    GitFileState,
    GitStatus,
    SecretMatch,
)
from .secrets import SecretProtector

GIT_TIMEOUT = 30


class GitError(RuntimeError):
    def __init__(self, message: str, exit_code: Optional[int] = None):
        super().__init__(message)
        self.exit_code = exit_code


class GitService:
    def __init__(
        self,
        root: Path,
        secret_protector: SecretProtector,
        *,
        timeout_seconds: int = GIT_TIMEOUT,
    ) -> None:
        self._root = root
        self._protector = secret_protector
        self._timeout = timeout_seconds

    def is_repository(self) -> bool:
        return (self._root / ".git").exists() or self._run(["rev-parse", "--is-inside-work-tree"], check=False).returncode == 0

    def status(self) -> GitStatus:
        if not self.is_repository():
            raise GitError("This project is not a Git repository.")
        branch, detached = self._branch()
        ahead, behind = self._ahead_behind()
        staged = self._porcelain(staged=True)
        unstaged = self._porcelain(unstaged=True)
        untracked = self._porcelain(untracked=True)
        conflicted = self._porcelain(conflicted=True)
        last_commit = self._last_commit()
        staged_secrets = self._staged_secret_matches()
        return GitStatus(
            project_id="",
            branch=branch,
            detached=detached,
            ahead=ahead,
            behind=behind,
            staged=tuple(staged),
            unstaged=tuple(unstaged),
            untracked=tuple(untracked),
            conflicted=tuple(conflicted),
            last_commit=last_commit,
            secret_matches=tuple(staged_secrets),
        )

    def diff(self, path: Optional[str] = None, staged: bool = False) -> Tuple[DiffEntry, ...]:
        if not self.is_repository():
            raise GitError("This project is not a Git repository.")
        args = ["diff", "--no-color", "--unified=2"]
        if staged:
            args.append("--cached")
        if path:
            args.append("--")
            args.append(path)
        output = self._run(args).stdout
        return self._parse_diff(output)

    def stage(self, paths: Sequence[str]) -> None:
        self._require_clean_paths(paths)
        result = self._run(["add", "--"] + list(paths), check=False)
        if result.returncode != 0:
            raise GitError("Staging failed: %s" % _last_line(result.stderr), result.returncode)

    def unstage(self, paths: Sequence[str]) -> None:
        result = self._run(["restore", "--staged", "--"] + list(paths), check=False)
        if result.returncode != 0:
            raise GitError("Unstaging failed: %s" % _last_line(result.stderr), result.returncode)

    def commit(self, message: str, *, approved: bool = False) -> CommitResult:
        if not message or not message.strip():
            raise GitError("A commit message is required.")
        if not approved:
            raise GitError("Creating a commit requires explicit user approval.")
        staged_secrets = self._staged_secret_matches()
        if staged_secrets:
            raise GitError(
                "Commit blocked: likely secrets were detected in staged changes. Resolve them first.",
            )
        result = self._run(
            ["commit", "-m", message.strip()[:200]],
            check=False,
        )
        if result.returncode != 0:
            raise GitError("Commit failed: %s" % _last_line(result.stderr), result.returncode)
        commit_id = _last_line(result.stdout) or "unknown"
        return CommitResult(project_id="", commit=commit_id[:40], summary=message.strip()[:200], secret_matches=tuple(staged_secrets), committed=True)

    def log(self, limit: int = 20) -> Tuple[str, ...]:
        output = self._run(["log", "--oneline", "-n", str(max(1, min(100, limit)))]).stdout
        return tuple(line for line in output.splitlines() if line.strip())

    def _branch(self) -> Tuple[Optional[str], bool]:
        result = self._run(["rev-parse", "--abbrev-ref", "HEAD"], check=False)
        if result.returncode == 0 and result.stdout.strip() and result.stdout.strip() != "HEAD":
            return result.stdout.strip(), False
        symbolic = self._run(["symbolic-ref", "--short", "-q", "HEAD"], check=False)
        if symbolic.returncode == 0 and symbolic.stdout.strip():
            return symbolic.stdout.strip(), False
        head = self._run(["rev-parse", "--short", "HEAD"], check=False)
        return head.stdout.strip() or None, True

    def _ahead_behind(self) -> Tuple[int, int]:
        result = self._run(["rev-list", "--left-right", "--count", "HEAD...@{upstream}"], check=False)
        if result.returncode != 0:
            return 0, 0
        parts = result.stdout.split()
        if len(parts) != 2:
            return 0, 0
        try:
            return int(parts[0]), int(parts[1])
        except ValueError:
            return 0, 0

    def _porcelain(self, *, staged: bool = False, unstaged: bool = False, untracked: bool = False, conflicted: bool = False) -> List[str]:
        result = self._run(["status", "--porcelain=v1", "--untracked-files=all"], check=False)
        paths: List[str] = []
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            code, path = line[:2], line[3:]
            if conflicted and "U" in code:
                paths.append(path)
            elif staged and code[0] not in " ?" and not code.startswith("??"):
                paths.append(path)
            elif unstaged and code[0] in " ?" and not code.startswith("??") and code[1] not in " ?":
                paths.append(path)
            elif untracked and code.startswith("??"):
                paths.append(path)
        return paths

    def _last_commit(self) -> Optional[str]:
        result = self._run(["log", "-1", "--format=%h %s"], check=False)
        return result.stdout.strip() or None

    def _staged_secret_matches(self) -> List[SecretMatch]:
        result = self._run(["diff", "--cached", "--no-color"], check=False)
        if result.returncode != 0:
            return []
        matches: List[SecretMatch] = []
        current_path = ""
        for line in result.stdout.splitlines():
            if line.startswith("+++ b/"):
                current_path = line[6:]
            if not line.startswith("+"):
                continue
            addition = line[1:]
            if not addition.strip():
                continue
            for match in self._protector.scan_text(addition, current_path, source="staged-git"):
                matches.append(match)
        return matches[:64]

    @staticmethod
    def _parse_diff(output: str) -> Tuple[DiffEntry, ...]:
        entries: List[DiffEntry] = []
        pending_path: Optional[str] = None
        pending_state: GitFileState = "modified"
        additions = 0
        deletions = 0
        hunks: List[DiffHunk] = []
        for line in output.splitlines():
            if line.startswith("diff --git"):
                if pending_path is not None:
                    entries.append(
                        DiffEntry(
                            path=pending_path,
                            state=pending_state,
                            additions=additions,
                            deletions=deletions,
                            hunks=tuple(hunks),
                        )
                    )
                pending_path = None
                pending_state = "modified"
                additions = 0
                deletions = 0
                hunks = []
                if "new file mode" in line or "new file" in line:
                    pending_state = "added"
                elif "deleted file mode" in line:
                    pending_state = "deleted"
                continue
            if line.startswith("new file mode"):
                pending_state = "added"
                continue
            if line.startswith("deleted file mode"):
                pending_state = "deleted"
                continue
            if line.startswith("+++ b/") and pending_path is None:
                pending_path = line[6:]
                continue
            if pending_path is None:
                continue
            if line.startswith("@@") and len(hunks) < 4096:
                hunks.append(DiffHunk(line=len(hunks) + 1, type="context", text=line))
            elif line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                deletions += 1
        if pending_path is not None:
            entries.append(
                DiffEntry(
                    path=pending_path,
                    state=pending_state,
                    additions=additions,
                    deletions=deletions,
                    hunks=tuple(hunks),
                )
            )
        return tuple(entries)

    def _require_clean_paths(self, paths: Sequence[str]) -> None:
        for path in paths:
            if not path or "\x00" in path or path.startswith("/") or ".." in path.split("/"):
                raise GitError("Unsafe path for Git operation.")

    def _run(self, args: Sequence[str], check: bool = True) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=str(self._root),
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env={"PATH": _safe_path(), "LC_ALL": "C"},
            )
        except subprocess.TimeoutExpired:
            raise GitError("Git command timed out.", -1) from None
        except OSError as exc:
            raise GitError("Git is not available on this host.") from exc
        if check and result.returncode != 0:
            raise GitError("Git command failed: %s" % _last_line(result.stderr), result.returncode)
        return result


def _safe_path() -> str:
    import os

    return os.environ.get("PATH", "/usr/bin:/bin")


def _last_line(text: str) -> str:
    return text.strip().splitlines()[-1] if text.strip() else ""
