"""Engineering Director: real autonomous orchestration for the campaign.

The Director is the production stage handler injected into the campaign
worker. It converts a WorkPackage into bounded structured agent instructions,
dispatches each stage to the authoritative engineering role agent through the
existing AgentFabric (ActionService), applies builder output inside an isolated
worktree through the runner's bounded filesystem/git/test executors, runs the
Builder<->Verifier repair loop, and raises human gates for decisions,
credentials, device actions, and approvals.

Every stage returns ``{"passed": bool, "detail": str, "evidence": tuple}`` so
the campaign state machine records the outcome. The Director never grants its
own capabilities, never runs arbitrary shell, never force-pushes, and never
overwrites a dirty human checkout.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import CampaignRecord, WorkPackageRecord

logger = logging.getLogger("joeos.engineering.director")

SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|private[_-]?key)"
               r"\s*[:=]\s*['\"][A-Za-z0-9_\-\.]{12,}['\"]"),
    re.compile(r"(?i)(^|[^a-z])(key|secret)\s*[:=]\s*['\"][A-Za-z0-9_\-\.]{16,}['\"]"),
    re.compile(r"(?i)(github_pat_|ghp_|sk_live_|sk_test_|AKIA[0-9A-Z]{16})"),
    re.compile(r"(?i)(BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY)"),
)

# Stage -> role agent key (matches graph.py STAGE_AGENT).
STAGE_ROLE = {
    "plan": "engineering.architect",
    "implement": "engineering.builder",
    "validate": "engineering.verification",
    "review": "engineering.securityreviewer",
    "integrate": "engineering.release",
    "push": "engineering.release",
}

DEFAULT_MAX_FILES = 16
DEFAULT_MAX_FILE_BYTES = 256 * 1024
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024


class SecretScanError(RuntimeError):
    pass


def _redact_output(text: str, limit: int = 4000) -> str:
    return (text or "")[:limit]


def scan_for_secrets(payload: Dict[str, Any]) -> List[str]:
    """Scan a builder payload for obvious secret-shaped strings.

    Accepts either the full builder payload (``{"files": {...}}``) or a bare
    files mapping. Returns the list of paths with secret-shaped content."""
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, dict):
        files = payload if isinstance(payload, dict) else {}
    findings: List[str] = []
    for rel_path, content in files.items():
        if not isinstance(content, str):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append(str(rel_path))
    return findings


class EngineeringDirector:
    """Production stage handler that drives real role agents + bounded executors.

    Constructed in the backend lifespan with the authoritative ActionService,
    the activation (owner) principal, the runner executors, and an optional
    notification sink. The campaign worker passes ``self.handler`` as its stage
    handler; the campaign service owns the state machine.
    """

    def __init__(
        self,
        *,
        action_service: Any,
        principal: Dict,
        git_executor: Optional[Any] = None,
        fs_executor: Optional[Any] = None,
        dev_executor: Optional[Any] = None,
        notification_sink: Optional[Callable[[str, str, str, str, str, tuple], None]] = None,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        worktree_root: Optional[str] = None,
        protected_branches: Optional[frozenset] = None,
        max_files: int = DEFAULT_MAX_FILES,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    ) -> None:
        self._action = action_service
        self._principal = principal
        self._git = git_executor
        self._fs = fs_executor
        self._dev = dev_executor
        self._notify = notification_sink
        self._events = event_sink or (lambda level, source, message: None)
        self._worktree_root = worktree_root
        self._protected_branches = protected_branches or frozenset(
            {"main", "master", "production", "release", "ai-rebuild"})
        self._max_files = max_files
        self._max_file_bytes = max_file_bytes
        self._max_total_bytes = max_total_bytes
        self._agents_by_key: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Agent resolution + dispatch (through the authoritative AgentFabric)
    # ------------------------------------------------------------------

    def _resolve_agents(self) -> None:
        if self._agents_by_key:
            return
        try:
            agents = self._action.list_agents(self._principal)
        except Exception as error:  # noqa: BLE001
            logger.warning("engineering director: agent listing unavailable: %s", error)
            agents = []
        self._agents_by_key = {
            a["key"]: str(a["id"]) for a in agents if a.get("key")
        }

    def _agent_id(self, role_key: str) -> Optional[str]:
        self._resolve_agents()
        return self._agents_by_key.get(role_key)

    async def _run_agent(self, role_key: str, objective: str, *,
                         model_preference: Optional[str] = None) -> Dict[str, Any]:
        """Run one real role agent and return its persisted run payload."""
        agent_id = self._agent_id(role_key)
        if not agent_id:
            return {"status": "unavailable", "detail": "role agent %s not seeded" % role_key,
                    "provider_key": None, "model_key": None}
        import uuid as _uuid
        run = self._action.start_agent_run(
            self._principal,
            agent_id=_uuid.UUID(agent_id),
            conversation_id=_uuid.uuid4(),
            message_id=_uuid.uuid4(),
            model_preference=model_preference,
            objective=objective[:4000],
        )
        executed = await self._action.execute_agent_run(self._principal, run["id"])
        return {
            "status": executed.get("status"),
            "detail": executed.get("failure") or "",
            "result": executed.get("result") or "",
            "provider_key": executed.get("provider_key"),
            "model_key": executed.get("model_key"),
            "run_id": str(executed.get("id", "")),
        }

    # ------------------------------------------------------------------
    # Work package compiler (bounded structured instructions)
    # ------------------------------------------------------------------

    def compile_prompt(self, package: WorkPackageRecord, campaign: CampaignRecord,
                       stage: str, attempt: int) -> str:
        """Build a bounded, structured instruction for one stage. Reusable
        policy is referenced, not repeated; prompts stay compact."""
        criteria = "\n".join("- %s" % c for c in package.acceptance_criteria)
        deps = ", ".join(package.dependencies) or "none"
        lines = [
            "Work package %s (%s)" % (package.key, package.title),
            "Objective: %s" % _redact_output(package.description, 1200),
            "Acceptance criteria:",
            criteria or "- no explicit criteria",
            "Dependencies: %s" % deps,
            "Risk class: %s" % package.risk,
            "Stage: %s (attempt %d)" % (stage, attempt),
        ]
        if stage == "implement":
            lines += [
                "Produce a bounded implementation. Return ONLY JSON of the form",
                '{"summary": "...", "files": {"rel/path": "file content"}}.',
                "Paths are relative to the worktree root. Do not include secrets.",
            ]
        elif stage == "validate":
            lines += [
                "Independently verify the change against the acceptance criteria.",
                "Respond beginning with exactly VERIFIED, PARTIAL, or REJECTED, then a concise reason.",
            ]
        elif stage == "review":
            lines += [
                "Review for security/trust-boundary issues and secret exposure.",
                "Respond beginning with exactly PASS, PASS_WITH_FINDINGS, or BLOCK, then a concise reason.",
            ]
        else:
            lines += ["Produce a concise, evidence-based result."]
        return "\n".join(lines)[:4000]

    # ------------------------------------------------------------------
    # Native builder application (bounded, path-safe, secret-scanned)
    # ------------------------------------------------------------------

    def _apply_builder_payload(self, package: WorkPackageRecord, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate + apply builder file writes inside the worktree. Fails
        closed on traversal, oversize, scope creep, or secret-shaped content."""
        files = payload.get("files")
        if not isinstance(files, dict):
            return {"passed": True, "detail": "builder returned no file changes",
                    "evidence": ("no_changes",)}
        if self._fs is None:
            return {"passed": False, "detail": "filesystem executor unavailable",
                    "evidence": ()}
        if len(files) > self._max_files:
            return {"passed": False, "detail": "builder change exceeds file budget",
                    "evidence": ("scope_creep",)}
        total = sum(len(c) for c in files.values() if isinstance(c, str))
        if total > self._max_total_bytes:
            return {"passed": False, "detail": "builder change exceeds total byte budget",
                    "evidence": ("scope_creep",)}
        if any(len(c) > self._max_file_bytes for c in files.values() if isinstance(c, str)):
            return {"passed": False, "detail": "individual file exceeds byte budget",
                    "evidence": ("scope_creep",)}
        findings = scan_for_secrets(files)
        if findings:
            return {"passed": False, "detail": "secret-shaped content in %s" % ",".join(findings[:3]),
                    "evidence": ("secret_scan_block",)}
        applied = []
        for rel_path, content in files.items():
            if not isinstance(rel_path, str) or not isinstance(content, str):
                continue
            if not rel_path or rel_path.startswith("/") or ".." in rel_path:
                return {"passed": False, "detail": "path traversal blocked: invalid builder path %r" % rel_path,
                        "evidence": ("path_traversal_block",)}
            cleaned = rel_path.strip().replace("\\", "/")
            if any(part in ("", ".", "..") for part in cleaned.split("/")):
                return {"passed": False, "detail": "path traversal blocked: invalid builder path %r" % rel_path,
                        "evidence": ("path_traversal_block",)}
            target = str(Path(cleaned))
            result = self._fs.execute(
                {"operation": "write_atomic", "content": content},
                target,
                root=self._worktree_root or "",
            )
            if result.get("status") != "succeeded":
                return {"passed": False, "detail": "write failed for %s: %s"
                        % (rel_path, result.get("summary", "")),
                        "evidence": ("write_failed",)}
            applied.append(cleaned)
        return {"passed": True, "detail": "applied %d file(s)" % len(applied),
                "evidence": tuple(applied)}

    # ------------------------------------------------------------------
    # Test battery (through the runner's dev executor)
    # ------------------------------------------------------------------

    def _run_test_battery(self, package: WorkPackageRecord) -> Dict[str, Any]:
        if self._dev is None:
            return {"passed": False, "detail": "dev executor unavailable", "evidence": ()}
        templates = ["joeos.dev.python_compile"]
        if package.risk in ("medium", "high", "critical"):
            templates.append("joeos.dev.backend_tests")
        results = {}
        for template in templates:
            try:
                results[template] = self._dev.execute(
                    {"command": template}, package.key, root=self._worktree_root or "",
                    timeout_ms=600_000,
                )
            except Exception as error:  # noqa: BLE001
                results[template] = {"status": "failed", "summary": str(error)[:300]}
        failures = {k: v for k, v in results.items()
                    if v.get("status") != "succeeded" and v.get("exit_classification") != "clean"}
        passed = not failures
        detail = "tests: %s" % ("; ".join("%s=%s" % (k, v.get("status")) for k, v in results.items()) or "none")
        return {"passed": passed, "detail": detail,
                "evidence": tuple("%s:%s" % (k, v.get("status")) for k, v in results.items())}

    # ------------------------------------------------------------------
    # Stage handler
    # ------------------------------------------------------------------

    async def handler(self, principal: Dict, campaign: CampaignRecord,
                      package: WorkPackageRecord, stage: str, attempt: int) -> Dict[str, Any]:
        self._worktree_root = campaign.worktree_root or self._worktree_root
        if stage in ("queued", "eligibility", "complete"):
            return {"passed": True, "detail": "stage has no executable work", "evidence": ()}

        if stage == "worktree":
            return {"passed": True, "detail": "worktree delegated to campaign git stage",
                    "evidence": ("worktree",)}

        if stage == "plan":
            prompt = self.compile_prompt(package, campaign, "plan", attempt)
            outcome = await self._run_agent("engineering.architect", prompt)
            if outcome.get("status") != "succeeded":
                return {"passed": False, "detail": "architect plan failed: %s" % outcome.get("detail"),
                        "evidence": (outcome.get("provider_key") or "local", outcome.get("model_key") or "auto")}
            return {"passed": True, "detail": "architect plan produced",
                    "evidence": (outcome.get("provider_key") or "local", outcome.get("model_key") or "auto")}

        if stage == "implement":
            prompt = self.compile_prompt(package, campaign, "implement", attempt)
            outcome = await self._run_agent("engineering.builder", prompt)
            if outcome.get("status") != "succeeded":
                return {"passed": False, "detail": "builder failed: %s" % outcome.get("detail"),
                        "evidence": (outcome.get("provider_key") or "local", outcome.get("model_key") or "auto")}
            try:
                payload = json.loads(outcome.get("result") or "{}")
            except (ValueError, TypeError):
                payload = {}
            applied = self._apply_builder_payload(package, payload)
            if not applied.get("passed"):
                return applied
            tests = self._run_test_battery(package)
            evidence = tuple(applied.get("evidence", ())) + tuple(tests.get("evidence", ()))
            return {"passed": tests.get("passed", True), "detail": "%s; %s"
                    % (applied.get("detail", ""), tests.get("detail", "")),
                    "evidence": evidence}

        if stage == "validate":
            prompt = self.compile_prompt(package, campaign, "validate", attempt)
            outcome = await self._run_agent("engineering.verification", prompt)
            text = (outcome.get("result") or "").strip()
            verdict = text.split("\n")[0].strip().upper() if text else "REJECTED"
            if outcome.get("status") != "succeeded":
                return {"passed": False, "detail": "verifier could not run: %s" % outcome.get("detail"),
                        "evidence": ()}
            if verdict.startswith("VERIFIED"):
                return {"passed": True, "detail": "verifier: VERIFIED",
                        "evidence": (outcome.get("provider_key") or "local", outcome.get("model_key") or "auto")}
            return {"passed": False, "detail": "verifier: %s" % text[:800],
                    "evidence": (outcome.get("provider_key") or "local", outcome.get("model_key") or "auto")}

        if stage == "review":
            prompt = self.compile_prompt(package, campaign, "review", attempt)
            outcome = await self._run_agent("engineering.securityreviewer", prompt)
            text = (outcome.get("result") or "").strip()
            verdict = text.split("\n")[0].strip().upper() if text else "BLOCK"
            if outcome.get("status") != "succeeded":
                return {"passed": False, "detail": "security review could not run: %s" % outcome.get("detail"),
                        "evidence": ()}
            if verdict.startswith("BLOCK"):
                self._notify_human_gate("SECURITY_BLOCK", package,
                                        "Security blocked %s" % package.key, text[:600])
                return {"passed": False, "detail": "security: BLOCK — %s" % text[:800],
                        "evidence": ("security_block",)}
            return {"passed": True, "detail": "security: %s" % verdict,
                    "evidence": (outcome.get("provider_key") or "local", outcome.get("model_key") or "auto")}

        if stage == "commit":
            return {"passed": True, "detail": "commit delegated to campaign git stage",
                    "evidence": ("commit",)}

        if stage in ("integrate", "push"):
            return {"passed": True, "detail": "%s delegated to release gate" % stage,
                    "evidence": (stage,)}

        return {"passed": True, "detail": "stage advanced", "evidence": ()}

    # ------------------------------------------------------------------
    # Human gates
    # ------------------------------------------------------------------

    def _notify_human_gate(self, category: str, package: WorkPackageRecord,
                           title: str, message: str) -> None:
        if self._notify is None:
            return
        try:
            self._notify(category, title, message, "high",
                         package.package_id,
                         ("/os/build",))
        except Exception as error:  # noqa: BLE001
            logger.warning("engineering director: notification failed: %s", error)

    def raise_human_gate(self, package: WorkPackageRecord, reason: str, detail: str) -> None:
        """Signal a genuine human decision/credential/device/approval gate."""
        category = {
            "human_decision": "HUMAN_DECISION_REQUIRED",
            "credential_required": "CREDENTIAL_REQUIRED",
            "device_action_required": "DEVICE_ACTION_REQUIRED",
            "approval_required": "APPROVAL_REQUIRED",
        }.get(reason, "HUMAN_DECISION_REQUIRED")
        self._notify_human_gate(category, package, "%s: %s" % (category, package.key), detail)
