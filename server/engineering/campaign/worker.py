"""Durable campaign worker loop.

Runs as a long-lived asyncio task inside the backend process. Each tick it
selects eligible work packages from active campaigns and advances them one stage
at a time through the authoritative campaign state machine (`worker_tick`),
dispatching executable stages to the injected stage handler. All state is
persisted in the campaign store, so a worker restart simply resumes from the
checkpointed package states. The worker never creates its own agent framework;
it only drives the persisted campaign state machine.

The stage handler is injected: production wires it to the runner executors
(git worktrees, opencode, dev commands, apple build). Tests inject a
deterministic handler.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, Optional

from .models import CampaignRecord, WorkPackageRecord
from .service import CampaignService

logger = logging.getLogger(__name__)

StageHandler = Callable[
    [Dict, CampaignRecord, WorkPackageRecord, str, int], Dict
]


class CampaignWorker:
    """Polls active campaigns and advances eligible packages one stage per tick.

    One package advances at most one stage per tick so the loop stays responsive
    and observable. All advancement goes through `CampaignService.worker_tick`,
    which owns selection, concurrency, dependency, and persistence rules.
    """

    def __init__(
        self,
        service: CampaignService,
        *,
        tick_interval_seconds: float = 30.0,
        stage_handler: Optional[StageHandler] = None,
    ) -> None:
        self._service = service
        self._tick_interval_seconds = max(1.0, float(tick_interval_seconds))
        self._stage_handler = stage_handler
        self._stop = asyncio.Event()

    async def run(self) -> None:
        logger.info("campaign worker started (tick %.1fs)",
                    self._tick_interval_seconds)
        while not self._stop.is_set():
            try:
                await self.tick_async()
            except Exception as error:  # pragma: no cover - defensive
                logger.exception("campaign worker tick failed: %s", error)
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._tick_interval_seconds)
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> int:
        """Run one synchronous tick. Returns the number of stages advanced."""
        return self._service.worker_tick(stage_handler=self._stage_handler)

    async def tick_async(self) -> int:
        """Run one async tick, awaiting async stage handlers (the Director)."""
        return await self._service.worker_tick_async(stage_handler=self._stage_handler)
