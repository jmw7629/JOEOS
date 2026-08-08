"""Engineering Campaign service: authoritative orchestration of the existing
agent fabric.

The campaign service owns durable campaign/work-package/attempt/checkpoint/
blocker/heartbeat state and the roadmap queue. It enforces the autonomy policy,
grants execution to injected stage handlers (in production, the ActionService
agent executor; in tests, deterministic adapters), and never self-grants
capabilities. Every state transition is validated by the state machine and
recorded against a checkpoint revision.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .models import (
    BlockReason,
    CampaignDefinition,
    CampaignRecord,
    CampaignState,
    EngineeringAttemptRecord,
    EngineeringBlockerRecord,
    EngineeringCheckpointRecord,
    GateResult,
    RoadmapEnvelope,
    RoadmapEntry,
    StageName,
    WatchdogHeartbeatRecord,
    WorkPackageDefinition,
    WorkPackageRecord,
)
from .state_machine import (
    gate_for_stage,
    next_stage,
    normalize_stage_order,
    package_state_for_stage,
    resolve_dependencies,
    validate_stage_sequence,
)
from .storage import CampaignStore
from .autonomy import get_autonomy_policy

CAMPAIGN_READ_CAP = "engineering.campaign.read"
CAMPAIGN_MANAGE_CAP = "engineering.campaign.manage"
CAMPAIGN_START_CAP = "engineering.campaign.start"
CAMPAIGN_PAUSE_CAP = "engineering.campaign.pause"
CAMPAIGN_CANCEL_CAP = "engineering.campaign.cancel"
PACKAGE_READ_CAP = "engineering.package.read"
PACKAGE_MANAGE_CAP = "engineering.package.manage"
BLOCKER_RESOLVE_CAP = "engineering.blocker.resolve"

# Package states the campaign worker may advance through the state machine.
# All mid-pipeline states are executable (each maps to a non-terminal stage);
# `queued` requires an explicit operator/eligibility start and terminal states
# are never advanced.
_EXECUTABLE_PACKAGE_STATES = frozenset({
    "eligible", "planning", "planned", "implementing", "validating",
    "reviewing", "committing", "integrating", "pushed",
})

DEFAULT_ROADMAP_STAGE_ORDER = (
    "eligibility",
    "plan",
    "worktree",
    "implement",
    "validate",
    "review",
    "commit",
    "integrate",
    "push",
    "complete",
)


class CampaignError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


class CampaignService:
    def __init__(
        self,
        store: CampaignStore,
        *,
        event_sink: Optional[Callable[[str, str, str], None]] = None,
        stage_handler: Optional[Callable[[Dict, CampaignRecord, WorkPackageRecord, StageName, int], Dict]] = None,
        now: Callable[[], int] = now_ms,
    ) -> None:
        self._store = store
        self._events = event_sink or (lambda level, source, message: None)
        self._stage_handler = stage_handler
        self._now = now

    def prepare(self) -> None:
        self._store.prepare()

    def recover_after_restart(self) -> int:
        """Mark running attempts abandoned and requeue their packages after a
        backend restart. Returns the number of packages requeued."""
        recovered = 0
        for campaign in self._store.list_campaigns():
            if campaign.state != "active":
                continue
            for package in self._store.list_work_packages(campaign.campaign_id):
                if package.state in ("planning", "implementing", "validating", "reviewing",
                                     "committing", "integrating", "pushed"):
                    for attempt in self._store.list_attempts(package.package_id):
                        if attempt.state == "running":
                            self._store.finish_attempt(
                                attempt.attempt_id, state="abandoned",
                                summary="backend restart recovered campaign", evidence=(),
                            )
                    self._store.update_work_package(
                        package.package_id, state="eligible",
                        current_stage="eligibility", error_detail="recovered after restart",
                    )
                    recovered += 1
        return recovered

    def _require(self, principal: Dict, capability: str) -> None:
        if capability not in principal.get("capabilities", []):
            raise CampaignError(403, "capability_denied",
                                "The principal lacks capability %s." % capability)

    def _emit(self, principal: Dict, event: str, campaign_id: Optional[str] = None,
              package_id: Optional[str] = None, data: Optional[Dict] = None) -> None:
        self._events("info", "campaign", "%s campaign=%s package=%s data=%s" % (
            event, campaign_id or "-", package_id or "-",
            json.dumps(data or {}, default=str)))

    # ------------------------------------------------------------------
    # Campaigns
    # ------------------------------------------------------------------

    def create_campaign(self, principal: Dict, definition: CampaignDefinition) -> CampaignRecord:
        self._require(principal, CAMPAIGN_MANAGE_CAP)
        if self._store.get_campaign_by_key(definition.key) is not None:
            raise CampaignError(409, "campaign_exists",
                                "A campaign with key %s already exists." % definition.key)
        record = CampaignRecord(
            campaign_id="camp-%s" % _new_id(),
            key=definition.key, title=definition.title,
            description=definition.description,
            repository_path=definition.repository_path,
            base_branch=definition.base_branch,
            integration_branch=definition.integration_branch,
            autonomy_policy_key=definition.autonomy_policy_key,
            state="proposed", current_stage="queued",
            worktree_root=definition.worktree_root,
            max_parallel_packages=definition.max_parallel_packages,
            max_attempts_per_package=definition.max_attempts_per_package,
            heartbeat_timeout_ms=definition.heartbeat_timeout_ms,
            revision=1, created_by=principal["user"]["id"],
            created_at=now_iso(), updated_at=now_iso(),
        )
        self._store.create_campaign(record)
        self._emit(principal, "campaign.created", campaign_id=record.campaign_id,
                   data={"key": definition.key})
        return record

    def get_campaign(self, principal: Dict, campaign_id: str) -> CampaignRecord:
        self._require(principal, CAMPAIGN_READ_CAP)
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        return record

    def list_campaigns(self, principal: Dict) -> Tuple[CampaignRecord, ...]:
        self._require(principal, CAMPAIGN_READ_CAP)
        return self._store.list_campaigns()

    def start_campaign(self, principal: Dict, campaign_id: str) -> CampaignRecord:
        self._require(principal, CAMPAIGN_START_CAP)
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        if not self._campaign_can_activate(record):
            raise CampaignError(409, "campaign_not_startable",
                                "Only proposed or paused campaigns can be started.")
        if record.autonomy_policy_key and not record.autonomy_policy_key.startswith("joeos.engineering."):
            raise CampaignError(409, "invalid_autonomy_policy",
                                "Autonomy policy key must be a joeos.engineering.* policy.")
        policy = get_autonomy_policy(record.autonomy_policy_key)
        if policy is None:
            raise CampaignError(409, "autonomy_policy_missing",
                                "The autonomy policy is not registered in the campaign catalog.")
        if record.max_parallel_packages > policy.limits.max_parallel_packages:
            raise CampaignError(409, "autonomy_policy_limit_exceeded",
                                "max_parallel_packages exceeds the autonomy policy limit.")
        if record.max_attempts_per_package > policy.limits.max_attempts_per_package:
            raise CampaignError(409, "autonomy_policy_limit_exceeded",
                                "max_attempts_per_package exceeds the autonomy policy limit.")
        if record.heartbeat_timeout_ms < policy.limits.heartbeat_timeout_ms:
            raise CampaignError(409, "autonomy_policy_limit_exceeded",
                                "heartbeat_timeout_ms is tighter than the autonomy policy requires.")
        updated = self._store.update_campaign_state(
            campaign_id, state="active", current_stage="eligibility", revision=1,
            last_heartbeat_at=now_iso(),
        )
        self._emit(principal, "campaign.started", campaign_id=campaign_id)
        return updated  # type: ignore[return-value]

    def pause_campaign(self, principal: Dict, campaign_id: str) -> CampaignRecord:
        self._require(principal, CAMPAIGN_PAUSE_CAP)
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        if record.state != "active":
            raise CampaignError(409, "campaign_not_active", "Only active campaigns can be paused.")
        updated = self._store.update_campaign_state(campaign_id, state="paused")
        self._emit(principal, "campaign.paused", campaign_id=campaign_id)
        return updated  # type: ignore[return-value]

    def resume_campaign(self, principal: Dict, campaign_id: str) -> CampaignRecord:
        self._require(principal, CAMPAIGN_START_CAP)
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        if record.state not in ("paused", "blocked"):
            raise CampaignError(409, "campaign_not_paused", "Only paused or blocked campaigns can resume.")
        updated = self._store.update_campaign_state(
            campaign_id, state="active", current_stage="eligibility", revision=1,
            last_heartbeat_at=now_iso(),
        )
        self._emit(principal, "campaign.resumed", campaign_id=campaign_id)
        return updated  # type: ignore[return-value]

    def cancel_campaign(self, principal: Dict, campaign_id: str) -> CampaignRecord:
        self._require(principal, CAMPAIGN_CANCEL_CAP)
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        if record.state in ("completed", "cancelled", "failed"):
            raise CampaignError(409, "campaign_terminal", "The campaign is already terminal.")
        updated = self._store.update_campaign_state(
            campaign_id, state="cancelled", completion_summary="cancelled by operator",
        )
        self._emit(principal, "campaign.cancelled", campaign_id=campaign_id)
        return updated  # type: ignore[return-value]

    def heartbeat(self, principal: Dict, campaign_id: str, worker: str = "watchdog",
                  detail: str = "") -> WatchdogHeartbeatRecord:
        self._require(principal, CAMPAIGN_READ_CAP)
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        if record.state != "active":
            raise CampaignError(409, "campaign_not_active", "Only active campaigns accept heartbeats.")
        beat = WatchdogHeartbeatRecord(
            heartbeat_id="hb-%s" % _new_id(), campaign_id=campaign_id,
            recorded_at=now_iso(), worker=worker[:80], detail=detail[:2000],
        )
        self._store.record_heartbeat(beat)
        return beat

    def watchdog_state(self, principal: Dict, campaign_id: str) -> Dict[str, Any]:
        self._require(principal, CAMPAIGN_READ_CAP)
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        beat = self._store.latest_heartbeat(campaign_id)
        if beat is None:
            return {"expired": record.last_heartbeat_at is None, "healthy": False,
                    "last_heartbeat_at": None, "timeout_ms": record.heartbeat_timeout_ms}
        try:
            last_ms = int(datetime.fromisoformat(beat.recorded_at).timestamp() * 1000)
        except ValueError:
            last_ms = 0
        expired = (self._now() - last_ms) > record.heartbeat_timeout_ms
        return {"expired": expired, "healthy": not expired,
                "last_heartbeat_at": beat.recorded_at, "timeout_ms": record.heartbeat_timeout_ms}

    # ------------------------------------------------------------------
    # Work packages + roadmap
    # ------------------------------------------------------------------

    def import_roadmap(self, principal: Dict, campaign_id: str, entries: Sequence[RoadmapEntry]) -> RoadmapEnvelope:
        self._require(principal, CAMPAIGN_MANAGE_CAP)
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        warnings: List[str] = []
        package_map: Dict[str, List[str]] = {}
        for entry in entries:
            package_map[entry.key] = list(entry.dependencies)
        error = resolve_dependencies(package_map)
        if error:
            raise CampaignError(422, "invalid_roadmap", error)
        normalized = []
        for entry in entries:
            stage_error = validate_stage_sequence(normalize_stage_order(entry.stage_order))
            if stage_error:
                warnings.append("%s: %s" % (entry.key, stage_error))
            normalized.append(entry.model_copy(
                update={"stage_order": normalize_stage_order(entry.stage_order)}))
        self._store.replace_roadmap(campaign_id, normalized)
        self._emit(principal, "roadmap.imported", campaign_id=campaign_id,
                   data={"entries": len(normalized)})
        return RoadmapEnvelope(entries=tuple(normalized), loaded_from="import",
                               warnings=tuple(warnings))

    def roadmap(self, principal: Dict, campaign_id: str) -> RoadmapEnvelope:
        self._require(principal, CAMPAIGN_READ_CAP)
        return RoadmapEnvelope(entries=self._store.roadmap(campaign_id))

    def create_work_package(self, principal: Dict, campaign_id: str,
                            definition: WorkPackageDefinition) -> WorkPackageRecord:
        self._require(principal, PACKAGE_MANAGE_CAP)
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        if self._store.get_work_package_by_key(campaign_id, definition.key) is not None:
            raise CampaignError(409, "package_exists",
                                "A package with key %s already exists." % definition.key)
        stage_order = normalize_stage_order(definition.stage_order)
        return self._store.create_work_package(campaign_id, definition.model_copy(
            update={"stage_order": stage_order}))

    def get_work_package(self, principal: Dict, package_id: str) -> WorkPackageRecord:
        self._require(principal, PACKAGE_READ_CAP)
        package = self._store.get_work_package(package_id)
        if package is None:
            raise CampaignError(404, "package_not_found", "The work package does not exist.")
        return package

    def list_work_packages(self, principal: Dict, campaign_id: str) -> Tuple[WorkPackageRecord, ...]:
        self._require(principal, PACKAGE_READ_CAP)
        self._store.get_campaign(campaign_id)  # existence check
        return self._store.list_work_packages(campaign_id)

    def start_work_package(self, principal: Dict, package_id: str,
                           stage_handler: Optional[Callable[..., Any]] = None) -> WorkPackageRecord:
        self._require(principal, PACKAGE_MANAGE_CAP)
        package = self._store.get_work_package(package_id)
        if package is None:
            raise CampaignError(404, "package_not_found", "The work package does not exist.")
        campaign = self._store.get_campaign(package.campaign_id)
        if campaign is None or campaign.state != "active":
            raise CampaignError(409, "campaign_not_active",
                                "The owning campaign must be active to start a package.")
        if package.state == "completed":
            raise CampaignError(409, "package_completed", "The package is already completed.")
        if not self._dependencies_satisfied(package, campaign.campaign_id):
            raise CampaignError(409, "dependencies_pending",
                                "Not all dependencies are completed yet.")
        self._record_checkpoint(campaign.campaign_id, package.package_id, "stage", "eligibility",
                                note="package eligibility confirmed")
        updated = self._store.update_work_package(
            package_id, state="eligible", current_stage="eligibility", last_gate="eligibility")
        self._emit(principal, "package.eligible", campaign_id=campaign.campaign_id,
                   package_id=package_id)
        return updated  # type: ignore[return-value]

    def advance_package(self, principal: Dict, package_id: str, *,
                        stage_handler: Optional[Callable[..., Any]] = None) -> Dict[str, Any]:
        """Advance a package one stage, invoking the injected stage handler when
        the stage is executable. Returns the package and the gate result."""
        self._require(principal, PACKAGE_MANAGE_CAP)
        package = self._store.get_work_package(package_id)
        if package is None:
            raise CampaignError(404, "package_not_found", "The work package does not exist.")
        campaign = self._store.get_campaign(package.campaign_id)
        if campaign is None or campaign.state != "active":
            raise CampaignError(409, "campaign_not_active",
                                "The owning campaign must be active to advance a package.")
        stage_order = tuple(package.stage_order) or DEFAULT_ROADMAP_STAGE_ORDER
        current = package.current_stage
        nxt = next_stage(current, stage_order)
        if nxt is None:
            completed = self._store.update_work_package(
                package_id, state="completed", current_stage="complete", last_gate="push")
            self._record_checkpoint(campaign.campaign_id, package_id, "stage", "complete",
                                    note="package completed")
            self._emit(principal, "package.completed", campaign_id=campaign.campaign_id,
                       package_id=package_id)
            return {"package": completed, "gate": GateResult(gate="push", passed=True,
                                                             detail="package completed")}
        gate_name = gate_for_stage(nxt)
        handler = stage_handler or self._stage_handler
        evidence: List[str] = []
        passed = True
        detail = ""
        if handler is not None:
            try:
                outcome = handler(principal, campaign, package, nxt, package.attempts + 1)
                passed = bool(outcome.get("passed", True))
                detail = str(outcome.get("detail", "") or "")
                evidence = [str(e) for e in outcome.get("evidence", [])][:64]
            except Exception as exc:  # pragma: no cover - defensive
                passed = False
                detail = "stage handler error: %s" % exc
        if not passed:
            state_target = "blocked"
            self._store.update_work_package(
                package_id, state=state_target, current_stage=current,
                error_detail=detail, last_gate=gate_name)
            blocker = EngineeringBlockerRecord(
                blocker_id="blk-%s" % _new_id(), campaign_id=campaign.campaign_id,
                package_id=package_id, reason="gate_failed",
                detail=detail[:4000], state="open", created_at=now_iso(),
            )
            self._store.create_blocker(blocker)
            self._emit(principal, "package.blocked", campaign_id=campaign.campaign_id,
                       package_id=package_id, data={"gate": gate_name})
            return {"package": self._store.get_work_package(package_id),
                    "gate": GateResult(gate=gate_name or "plan", passed=False, detail=detail,
                                       evidence=tuple(evidence), blocker_created=True)}
        self._store.update_work_package(
            package_id, state=package_state_for_stage(nxt), current_stage=nxt,
            last_gate=gate_name)
        self._record_checkpoint(campaign.campaign_id, package_id, "stage", nxt,
                                note="advanced to %s" % nxt)
        self._emit(principal, "package.advanced", campaign_id=campaign.campaign_id,
                   package_id=package_id, data={"stage": nxt})
        return {"package": self._store.get_work_package(package_id),
                "gate": GateResult(gate=gate_name or "plan", passed=True, detail=detail,
                                   evidence=tuple(evidence))}

    def raise_blocker(self, principal: Dict, package_id: str, reason: BlockReason,
                      detail: str) -> EngineeringBlockerRecord:
        self._require(principal, PACKAGE_MANAGE_CAP)
        package = self._store.get_work_package(package_id)
        if package is None:
            raise CampaignError(404, "package_not_found", "The work package does not exist.")
        blocker = EngineeringBlockerRecord(
            blocker_id="blk-%s" % _new_id(), campaign_id=package.campaign_id,
            package_id=package_id, reason=reason, detail=detail[:4000],
            state="open", created_at=now_iso(),
        )
        self._store.create_blocker(blocker)
        self._store.update_work_package(package_id, state="blocked")
        self._emit(principal, "blocker.raised", campaign_id=package.campaign_id,
                   package_id=package_id, data={"reason": reason})
        return blocker

    def resolve_blocker(self, principal: Dict, blocker_id: str, resolution: str) -> EngineeringBlockerRecord:
        self._require(principal, BLOCKER_RESOLVE_CAP)
        blocker = self._store.get_blocker(blocker_id)
        if blocker is None:
            raise CampaignError(404, "blocker_not_found", "The blocker does not exist.")
        if blocker.state != "open":
            raise CampaignError(409, "blocker_not_open", "Only open blockers can be resolved.")
        updated = self._store.resolve_blocker(
            blocker_id, resolved_by=principal["user"]["id"], resolution=resolution[:4000])
        assert updated is not None
        if blocker.package_id:
            self._store.update_work_package(
                blocker.package_id, state="eligible", current_stage="eligibility",
                error_detail=None)
        return updated

    # ------------------------------------------------------------------
    # Attempts + checkpoints
    # ------------------------------------------------------------------

    def begin_attempt(self, principal: Dict, package_id: str) -> EngineeringAttemptRecord:
        self._require(principal, PACKAGE_MANAGE_CAP)
        package = self._store.get_work_package(package_id)
        if package is None:
            raise CampaignError(404, "package_not_found", "The work package does not exist.")
        campaign = self._store.get_campaign(package.campaign_id)
        if campaign is None:
            raise CampaignError(404, "campaign_not_found", "The campaign does not exist.")
        if package.attempts + 1 > campaign.max_attempts_per_package:
            raise CampaignError(409, "max_attempts_exceeded",
                                "The package exceeded its attempt budget.")
        attempt = EngineeringAttemptRecord(
            attempt_id="att-%s" % _new_id(), package_id=package_id,
            campaign_id=package.campaign_id, attempt_number=package.attempts + 1,
            state="running", started_by=principal["user"]["id"],
            started_at=now_iso(), evidence=(),
        )
        self._store.create_attempt(attempt)
        return attempt

    def finish_attempt(self, principal: Dict, attempt_id: str, *, state: str,
                       summary: Optional[str] = None,
                       evidence: Sequence[str] = ()) -> EngineeringAttemptRecord:
        self._require(principal, PACKAGE_MANAGE_CAP)
        attempt = self._store.get_attempt(attempt_id)
        if attempt is None:
            raise CampaignError(404, "attempt_not_found", "The attempt does not exist.")
        if state not in ("succeeded", "failed", "abandoned"):
            raise CampaignError(422, "invalid_attempt_state",
                                "Attempt must end as succeeded, failed, or abandoned.")
        updated = self._store.finish_attempt(attempt_id, state=state, summary=summary,
                                             evidence=evidence)
        assert updated is not None
        return updated

    def attempts(self, principal: Dict, package_id: str) -> Tuple[EngineeringAttemptRecord, ...]:
        self._require(principal, PACKAGE_READ_CAP)
        return self._store.list_attempts(package_id)

    def checkpoints(self, principal: Dict, campaign_id: str) -> Tuple[EngineeringCheckpointRecord, ...]:
        self._require(principal, CAMPAIGN_READ_CAP)
        return self._store.list_checkpoints(campaign_id)

    def blockers(self, principal: Dict, campaign_id: str) -> Tuple[EngineeringBlockerRecord, ...]:
        self._require(principal, CAMPAIGN_READ_CAP)
        return self._store.list_blockers(campaign_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _campaign_can_activate(self, record: CampaignRecord) -> bool:
        return record.state in ("proposed", "paused")

    def worker_tick(self, *, worker: str = "campaign-worker",
                    stage_handler: Optional[Callable[..., Any]] = None) -> int:
        """Advance one stage for every eligible package across active campaigns.

        The campaign worker calls this each tick. Selection honours the
        per-campaign concurrency cap and package dependencies; only packages in
        an executable state are touched. The stage handler defaults to the one
        injected at construction; when None the state machine advances without
        executing work (safe-by-default). Returns the number of stages advanced.
        """
        principal = self._worker_principal(worker)
        advanced = 0
        for campaign in self._store.list_campaigns():
            if campaign.state != "active":
                continue
            packages = self._store.list_work_packages(campaign.campaign_id)
            candidates = [p for p in packages
                          if p.state in _EXECUTABLE_PACKAGE_STATES
                          and self._dependencies_satisfied(p, campaign.campaign_id)]
            for package in candidates[:campaign.max_parallel_packages]:
                try:
                    self.advance_package(
                        principal, package.package_id,
                        stage_handler=stage_handler or self._stage_handler,
                    )
                except Exception as error:  # pragma: no cover - defensive
                    self._emit("error", "campaign", "worker advance failed for %s: %s"
                               % (package.package_id, error))
                    continue
                advanced += 1
        return advanced

    def _worker_principal(self, worker: str) -> Dict:
        return {
            "session_id": None,
            "device_id": None,
            "user": {"id": "worker:%s" % worker, "display_name": worker},
            "organization": {"id": None},
            "workspace": {"id": None},
            "roles": ["joeos.engineering.worker"],
            "capabilities": [PACKAGE_MANAGE_CAP],
        }

    def _dependencies_satisfied(self, package: WorkPackageRecord, campaign_id: str) -> bool:
        for dep in package.dependencies:
            other = self._store.get_work_package_by_key(campaign_id, dep)
            if other is None or other.state != "completed":
                return False
        return True

    def _record_checkpoint(self, campaign_id: str, package_id: Optional[str], kind: str,
                           stage: str, note: str) -> EngineeringCheckpointRecord:
        snapshot = _digest({
            "campaign": campaign_id, "package": package_id, "kind": kind,
            "stage": stage, "note": note, "ts": self._now(),
        })
        record = EngineeringCheckpointRecord(
            checkpoint_id="cp-%s" % _new_id(), campaign_id=campaign_id,
            package_id=package_id, kind=kind, stage=stage, revision=1,
            state_snapshot_digest=snapshot, note=note, created_at=now_iso(),
        )
        self._store.create_checkpoint(record)
        return record


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex[:24]
