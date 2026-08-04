"""Model Resource Manager.

Tracks the inventory and load state of AI models using real runtime state from
the authoritative AI runtime (Lemonade). Footprints are either runtime-reported
or clearly labeled ``unmeasured``; VRAM that the runtime does not expose stays
``unknown``. Enforces maximum resident models, an idle-unload policy, resource
preflight, and controlled unload (never during an active request without
cancellation coordination). OOM loads mark the model ``resource_blocked`` and
require a user override instead of endlessly retrying.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from .models import ModelResourceState


class ModelResourceManager:
    def __init__(self, *, max_resident: int = 2, idle_unload_seconds: float = 300.0, now_provider=None) -> None:
        self._max_resident = max(1, int(max_resident))
        self._idle_unload = max(0.0, float(idle_unload_seconds))
        self._now = now_provider or time.monotonic
        self._lock = threading.RLock()
        self._models: Dict[str, Dict[str, object]] = {}
        self._loaded_count = 0
        self._load_blocked = 0
        self._last_unload = 0.0

    def sync_inventory(self, models: List[dict]) -> None:
        """Adopt authoritative inventory from the AI runtime."""
        now = self._now()
        with self._lock:
            seen = set()
            for model in models:
                model_id = str(model.get("id", "")).strip()
                if not model_id:
                    continue
                seen.add(model_id)
                existing = self._models.get(model_id)
                if existing is None:
                    self._models[model_id] = {
                        "model_id": model_id,
                        "runtime": str(model.get("runtime") or model.get("recipe") or ""),
                        "state": "installed",
                        "active_requests": 0,
                        "queue_depth": 0,
                        "last_use": None,
                        "pinned": False,
                        "estimated_memory_mb": _safe_float(model.get("memory_mb") or model.get("size_mb")),
                        "actual_memory_mb": None,
                        "footprint_source": "runtime" if model.get("memory_mb") is not None else "unmeasured",
                        "discovered_at": now,
                    }
                else:
                    if existing.get("state") in ("failed", "resource_blocked") and existing.get("last_error"):
                        existing["state"] = "installed"
                    existing["runtime"] = str(model.get("runtime") or model.get("recipe") or existing["runtime"])
                    if model.get("memory_mb") is not None:
                        existing["actual_memory_mb"] = _safe_float(model["memory_mb"])
                        existing["footprint_source"] = "runtime"
            for model_id in list(self._models):
                if model_id not in seen:
                    del self._models[model_id]
            self._recompute_loaded()

    def mark_loaded(self, model_id: str, runtime_reported_memory_mb: Optional[float] = None) -> None:
        with self._lock:
            state = self._models.get(model_id)
            if state is None:
                state = self._ensure(model_id)
            if state.get("state") in ("loaded", "busy"):
                return
            if self._loaded_count >= self._max_resident and not state.get("pinned"):
                self._load_blocked += 1
                state["state"] = "resource_blocked"
                return
            if runtime_reported_memory_mb is not None:
                state["actual_memory_mb"] = _safe_float(runtime_reported_memory_mb)
                state["footprint_source"] = "runtime"
            state["state"] = "loaded"
            state["last_use"] = _now_iso()
            self._recompute_loaded()

    def mark_busy(self, model_id: str) -> None:
        with self._lock:
            state = self._models.get(model_id)
            if state is None:
                return
            state["state"] = "busy"
            state["active_requests"] = int(state.get("active_requests", 0)) + 1
            state["last_use"] = _now_iso()

    def mark_idle(self, model_id: str) -> None:
        with self._lock:
            state = self._models.get(model_id)
            if state is None:
                return
            state["active_requests"] = max(0, int(state.get("active_requests", 0)) - 1)
            if state["active_requests"] == 0:
                state["state"] = "idle" if state.get("state") == "busy" else state.get("state")

    def mark_failed(self, model_id: str, *, out_of_memory: bool = False) -> None:
        with self._lock:
            state = self._models.get(model_id)
            if state is None:
                state = self._ensure(model_id)
            if out_of_memory:
                state["state"] = "resource_blocked"
                state["last_error"] = "out_of_memory"
            else:
                state["state"] = "failed"
                state["last_error"] = "load_failed"
            self._recompute_loaded()

    def unload_idle(self, *, force: bool = False) -> List[str]:
        """Unload idle models, respecting pinned models and active requests."""
        now = self._now()
        unloaded = []
        with self._lock:
            for model_id, state in self._models.items():
                if state.get("pinned"):
                    continue
                if int(state.get("active_requests", 0)) > 0:
                    continue
                if state.get("state") not in ("idle", "loaded"):
                    continue
                if force:
                    unloaded.append(model_id)
                    continue
                idle_since = state.get("idle_since")
                if idle_since is None:
                    continue
                if now - idle_since >= self._idle_unload:
                    unloaded.append(model_id)
            for model_id in unloaded:
                self._models[model_id]["state"] = "installed"
                self._models[model_id]["actual_memory_mb"] = None
                self._models[model_id].pop("idle_since", None)
            if unloaded:
                self._last_unload = now
            self._recompute_loaded()
        return unloaded

    def pin(self, model_id: str, pinned: bool) -> bool:
        with self._lock:
            state = self._models.get(model_id)
            if state is None:
                return False
            state["pinned"] = bool(pinned)
            return True

    def set_queue_depth(self, model_id: str, depth: int) -> None:
        with self._lock:
            state = self._models.get(model_id)
            if state is not None:
                state["queue_depth"] = max(0, int(depth))

    def mark_idle_since(self, model_id: str) -> None:
        with self._lock:
            state = self._models.get(model_id)
            if state is not None:
                state["idle_since"] = self._now()
                state["last_use_ts"] = self._now()

    def states(self) -> List[ModelResourceState]:
        with self._lock:
            return [
                ModelResourceState(
                    model_id=model_id,
                    runtime=str(state.get("runtime", "")),
                    state=str(state.get("state", "unknown")),
                    active_requests=int(state.get("active_requests", 0)),
                    queue_depth=int(state.get("queue_depth", 0)),
                    last_use=str(state.get("last_use") or ""),
                    pinned=bool(state.get("pinned", False)),
                    estimated_memory_mb=float(state.get("estimated_memory_mb", 0.0)),
                    actual_memory_mb=state.get("actual_memory_mb"),
                    footprint_source=str(state.get("footprint_source", "unmeasured")),
                )
                for model_id, state in sorted(self._models.items())
            ]

    def loaded_count(self) -> int:
        with self._lock:
            return self._loaded_count

    def blocked_count(self) -> int:
        with self._lock:
            return sum(1 for state in self._models.values() if state.get("state") == "resource_blocked")

    def max_resident(self) -> int:
        with self._lock:
            return self._max_resident

    def idle_unload_seconds(self) -> float:
        with self._lock:
            return self._idle_unload

    def _ensure(self, model_id: str) -> dict:
        state = self._models.get(model_id)
        if state is None:
            state = {
                "model_id": model_id,
                "runtime": "",
                "state": "installed",
                "active_requests": 0,
                "queue_depth": 0,
                "last_use": None,
                "pinned": False,
                "estimated_memory_mb": 0.0,
                "actual_memory_mb": None,
                "footprint_source": "unmeasured",
                "discovered_at": self._now(),
            }
            self._models[model_id] = state
        return state

    def _recompute_loaded(self) -> None:
        self._loaded_count = sum(
            1 for state in self._models.values() if state.get("state") in ("loaded", "busy", "idle")
        )


def _safe_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
