"""Command and terminal execution with trust, risk, and approval gating.

Commands only run inside an approved project root, never with a shell, with a
short timeout and bounded output. High-risk or network-touching commands are
blocked or require explicit approval.
"""

from __future__ import annotations

import shlex
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .models import (
    CommandContract,
    CommandResult,
    CommandValidation,
    RiskLevel,
    TrustState,
)

COMMAND_TIMEOUT = 15
MAX_OUTPUT_CHARS = 100_000
OUTPUT_RETENTION = 1000

APPROVAL_REQUIRED = (
    ("git", "push"),
    ("git", "fetch"),
    ("git", "pull"),
    ("git", "remote"),
    ("git", "clone"),
    ("git", "submodule"),
    ("pip", "install"),
    ("npm", "install"),
    ("npm", "run"),
    ("yarn", "install"),
    ("pnpm", "install"),
    ("uv", "add"),
    ("uv", "remove"),
    ("curl",),
    ("wget",),
    ("ssh",),
    ("scp",),
    ("rsync",),
    ("rm", "-rf"),
    ("rm", "-fr"),
)

BLOCKED = (
    ("sudo",),
    ("su",),
    ("docker",),
    ("reboot",),
    ("shutdown",),
    ("systemctl",),
    ("halt",),
    ("poweroff",),
    ("mkfs",),
    ("fdisk",),
    ("chmod", "777"),
    ("chmod", "a+w"),
    ("rm", "-rf", "/"),
    ("rm", "-rf", "/*"),
)


class CommandError(RuntimeError):
    def __init__(self, message: str, exit_code: Optional[int] = None):
        super().__init__(message)
        self.exit_code = exit_code


class CommandService:
    def __init__(
        self,
        root_provider: Callable[[str], Path],
        trust_provider: Callable[[str], TrustState],
        activity_recorder: Callable[[str, str, str], None],
    ) -> None:
        self._root_provider = root_provider
        self._trust_provider = trust_provider
        self._record = activity_recorder

    def validate(self, project_id: str, command: str) -> CommandValidation:
        tokens = self._tokens(command)
        if not tokens:
            return self._validation(project_id, command, [], allowed=False, blocked_reason="Empty command.")
        risk = self._classify_risk(tokens)
        approval_required = self._requires_approval(tokens)
        blocked = self._is_blocked(tokens)
        trust_state = self._trust_provider(project_id)
        trusted = trust_state in {"trusted", "session"}
        network_required = self._touches_network(tokens)
        allowed = not blocked and not (approval_required and not trusted)
        reason = "Blocked by security policy." if blocked else "Requires explicit approval." if approval_required and not trusted else None
        return self._validation(project_id, command, tokens, allowed=allowed, risk=risk, approval_required=approval_required, network_required=network_required, trust_required=True, blocked_reason=reason)

    def execute(self, project_id: str, command: str, *, approved: bool = False) -> CommandResult:
        validation = self.validate(project_id, command)
        if not validation.allowed:
            raise CommandError(validation.blocked_reason or "Command is not allowed.", -1)
        if validation.approval_required and not approved:
            raise CommandError("Command requires explicit approval.", -1)
        root = self._root_provider(project_id)
        started = datetime.now(timezone.utc)
        try:
            result = subprocess.run(
                [validation.contract.executable] + list(validation.contract.args),
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
                env=self._env(),
            )
        except subprocess.TimeoutExpired:
            self._record(project_id, "command", "timeout")
            raise CommandError("Command timed out.", -1) from None
        except OSError as exc:
            self._record(project_id, "command", "spawn-failed")
            raise CommandError("Command could not start: %s" % exc, -1) from exc
        finished = datetime.now(timezone.utc)
        duration_ms = int((finished - started).total_seconds() * 1000)
        stdout = _bounded(result.stdout)
        stderr = _bounded(result.stderr)
        state = "succeeded" if result.returncode == 0 else "failed"
        self._record(project_id, "command", "ok" if state == "succeeded" else "error")
        return CommandResult(
            execution_id=validation.contract.execution_id,
            state=state,
            exit_code=result.returncode,
            stdout=stdout,
            stderr=stderr,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            duration_ms=duration_ms,
            error_code=None if result.returncode == 0 else str(result.returncode),
            risk=validation.risk,
        )

    def _validation(
        self,
        project_id: str,
        command: str,
        tokens: Sequence[str],
        *,
        allowed: bool,
        risk: RiskLevel = "low",
        approval_required: bool = False,
        network_required: bool = False,
        trust_required: bool = True,
        blocked_reason: Optional[str] = None,
    ) -> CommandValidation:
        executable = tokens[0] if tokens else ""
        args = tuple(tokens[1:]) if tokens else ()
        contract = CommandContract(
            execution_id=uuid.uuid4().hex[:12],
            project_id=project_id,
            executable=executable,
            args=args,
            shell=False,
            timeout_seconds=COMMAND_TIMEOUT,
            risk=risk,
            requires_approval=approval_required,
            trust_required=trust_required,
            network_required=network_required,
            output_retention=OUTPUT_RETENTION,
        )
        return CommandValidation(
            contract=contract,
            risk=risk,
            approval_required=approval_required,
            trust_required=trust_required,
            network_required=network_required,
            allowed=allowed,
            blocked_reason=blocked_reason,
        )

    def _tokens(self, command: str) -> List[str]:
        if not command or command.strip() == "":
            return []
        try:
            return shlex.split(command)
        except ValueError:
            raise CommandError("Command could not be parsed.", -1)

    def _classify_risk(self, tokens: Sequence[str]) -> RiskLevel:
        if not tokens:
            return "low"
        if self._is_blocked(tokens):
            return "high"
        if self._is_destructive(tokens):
            return "high"
        if self._touches_network(tokens):
            return "medium"
        return "low"

    def _requires_approval(self, tokens: Sequence[str]) -> bool:
        return any(_prefix(tokens, rule) for rule in APPROVAL_REQUIRED)

    def _is_blocked(self, tokens: Sequence[str]) -> bool:
        return any(_prefix(tokens, rule) for rule in BLOCKED)

    def _is_destructive(self, tokens: Sequence[str]) -> bool:
        return any(
            _prefix(tokens, rule)
            for rule in (
                ("git", "clean"),
                ("git", "reset"),
                ("git", "checkout"),
                ("git", "revert"),
                ("git", "branch", "-D"),
                ("git", "push", "--force"),
            )
        )

    def _touches_network(self, tokens: Sequence[str]) -> bool:
        network_programs = {
            "curl", "wget", "git", "pip", "pip3", "npm", "yarn", "pnpm", "npx",
            "ssh", "scp", "rsync", "uv", "poetry",
        }
        return tokens and tokens[0] in network_programs

    def _env(self) -> dict:
        return {"PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8", "HOME": str(Path.home()), "TERM": "dumb"}


def _prefix(tokens: Sequence[str], rule: Tuple[str, ...]) -> bool:
    return list(tokens)[: len(rule)] == list(rule)


def _bounded(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n... [output truncated]"
