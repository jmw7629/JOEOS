"""Durable autonomous operations scheduler.

A long-lived asyncio task inside the backend process (like the campaign
worker). Each tick:

1. finds due definitions
2. claims each due occurrence with a durable lease
3. creates an AutomationRun (deduplicated by occurrence key)
4. executes through the AgentFabric executor
5. persists the final result
6. advances the next occurrence
7. handles bounded retries for retryable failures

If the worker dies, the lease expires and the next pass recovers non-terminal
runs safely (never re-runs a terminal AgentRun). The browser is never required.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

from .executor import AgentFabricAutomationExecutor
from .models import AutomationDefinition, AutomationRun
from .service import AutonomousService
from .scheduling import next_occurrence

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_expiry(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class AutonomousScheduler:
    def __init__(
        self,
        service: AutonomousService,
        executor: AgentFabricAutomationExecutor,
        *,
        tick_interval_seconds: float = 20.0,
        lease_seconds: int = 300,
    ) -> None:
        self._service = service
        self._executor = executor
        self._tick_interval_seconds = max(1.0, float(tick_interval_seconds))
        self._lease_seconds = max(30, int(lease_seconds))
        self._stop = asyncio.Event()

    async def run(self) -> None:
        logger.info("autonomous scheduler started (tick %.1fs)", self._tick_interval_seconds)
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception as error:  # pragma: no cover - defensive
                logger.exception("autonomous scheduler tick failed: %s", error)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._tick_interval_seconds)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> int:
        now = _now_iso()
        # Recover expired leases first so a crashed worker's work can proceed.
        self._service.recover_after_restart()
        due = self._service.due_now()
        processed = 0
        for definition in due:
            try:
                processed += self._process_due(definition, now)
            except Exception as error:  # pragma: no cover - defensive
                logger.exception("scheduler failed for %s: %s", definition.id, error)
        return processed

    def _process_due(self, definition: AutomationDefinition, now: str) -> int:
        scheduled_for = definition.next_run_at or now
        run = self._service.claim_and_run(definition, scheduled_for)
        if run is None or run.state in ("succeeded", "failed", "cancelled"):
            # Already terminal or duplicate; advance and move on.
            self._service.advance_definition(definition.id)
            return 0
        claimed = self._service.claim_run(
            run.id, worker="scheduler", now_iso=now,
            lease_expires_iso=_lease_expiry(self._lease_seconds),
        )
        if not claimed:
            return 0
        run = self._service.get_run(run.id)
        try:
            outcome = self._executor.execute(definition, run, self._service)
            status = outcome.get("status")
            if status == "succeeded":
                self._service.complete_run(
                    run.id, state="succeeded",
                    result_summary=outcome.get("result", "")[:12000],
                    agent_run_id=outcome.get("agent_run_id", ""),
                    provider_key=outcome.get("provider_key", ""),
                    model_key=outcome.get("model_key", ""),
                )
            else:
                self._handle_failure(definition, run, outcome)
            self._service.advance_definition(definition.id)
            return 1
        except Exception as error:  # pragma: no cover - defensive
            logger.exception("automation run failed: %s", error)
            self._handle_failure(definition, run, {"failure": "executor_error", "status": "failed"})
            self._service.advance_definition(definition.id)
            return 1

    def _handle_failure(self, definition: AutomationDefinition, run: AutomationRun, outcome: Dict) -> None:
        error_category = str(outcome.get("failure") or outcome.get("status") or "failed")
        retryable = self._service.is_retryable(error_category, definition)
        attempt = run.attempt + 1
        if retryable and attempt <= definition.retry_policy.max_attempts:
            backoff = min(
                definition.retry_policy.backoff_seconds
                * (definition.retry_policy.backoff_factor ** (attempt - 1)),
                definition.retry_policy.max_backoff_seconds,
            )
            next_retry = (datetime.now(timezone.utc) + timedelta(seconds=backoff)).isoformat()
            self._service.update_run_with_retry(
                run.id, attempt=attempt, error_category=error_category,
                next_retry_at=next_retry,
                agent_run_id=outcome.get("agent_run_id", ""),
                provider_key=outcome.get("provider_key", ""),
                model_key=outcome.get("model_key", ""),
            )
            self._service.notify_retry(run)
        else:
            self._service.complete_run(
                run.id, state="failed", error_category=error_category,
                result_summary=str(outcome.get("result", ""))[:12000],
                agent_run_id=outcome.get("agent_run_id", ""),
                provider_key=outcome.get("provider_key", ""),
                model_key=outcome.get("model_key", ""),
            )
            self._service.notify_failure(run)
