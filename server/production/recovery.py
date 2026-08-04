"""Recovery Coordinator, Safe Mode, and Repair Mode.

Safe Mode starts with a minimal trusted set (core shell, security, settings,
database) and explicitly restricts third-party plugins, workflows, agents,
cloud providers, remote clients, model preload, and optional indexing. Repair
Mode exposes detection-driven operations that always preserve authoritative
data. Recovery state is persisted and surfaced honestly.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from .models import RecoveryState


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecoveryCoordinator:
    def __init__(self, connection_factory: Optional[Callable[[], sqlite3.Connection]] = None, *, crash_loop_threshold: int = 3) -> None:
        self._factory = connection_factory
        self._threshold = max(2, int(crash_loop_threshold))
        self._lock = threading.RLock()
        self._safe_mode = False
        self._repair_mode = False
        self._crash_window: List[float] = []
        if self._factory is not None:
            self._prepare()

    def _prepare(self) -> None:
        with self._factory() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS production_recovery (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS production_crashes (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, component TEXT NOT NULL)"
            )
            connection.commit()

    def enter_safe_mode(self, reason: str = "") -> bool:
        with self._lock:
            self._safe_mode = True
            self._persist("safe_mode", "1")
            self._persist("safe_mode_reason", reason)
        return True

    def exit_safe_mode(self) -> bool:
        with self._lock:
            self._safe_mode = False
            self._persist("safe_mode", "0")
        return True

    def enter_repair_mode(self, reason: str = "") -> bool:
        with self._lock:
            self._repair_mode = True
            self._persist("repair_mode", "1")
            self._persist("repair_mode_reason", reason)
        return True

    def exit_repair_mode(self) -> bool:
        with self._lock:
            self._repair_mode = False
            self._persist("repair_mode", "0")
        return True

    def record_crash(self, component: str) -> bool:
        """Record a crash and report whether the crash-loop threshold is hit."""
        import time
        now = time.monotonic()
        with self._lock:
            self._crash_window = [stamp for stamp in self._crash_window if now - stamp < 300.0]
            self._crash_window.append(now)
            if self._factory is not None:
                with self._factory() as connection:
                    connection.execute("INSERT INTO production_crashes (at, component) VALUES (?, ?)", (_now_iso(), component[:80]))
                    connection.commit()
            return len(self._crash_window) >= self._threshold

    def crash_loop_detected(self) -> bool:
        import time
        now = time.monotonic()
        with self._lock:
            self._crash_window = [stamp for stamp in self._crash_window if now - stamp < 300.0]
            return len(self._crash_window) >= self._threshold

    def clear_crash_window(self) -> None:
        with self._lock:
            self._crash_window = []

    def state(self, *, low_disk: bool = False, interrupted_update: bool = False, interrupted_migration: bool = False) -> RecoveryState:
        with self._lock:
            return RecoveryState(
                safe_mode=self._safe_mode,
                repair_mode=self._repair_mode,
                crash_loop_detected=self.crash_loop_detected(),
                interrupted_update=interrupted_update,
                interrupted_migration=interrupted_migration,
                low_disk=low_disk,
                detail="Safe Mode restricts third-party plugins, workflows, agents, cloud providers, remote clients, and model preload.",
                generated_at=_now_iso(),
            )

    def safe_mode_restrictions(self) -> Dict[str, bool]:
        return {
            "third_party_plugins_disabled": self._safe_mode,
            "workflows_paused": self._safe_mode,
            "agents_paused": self._safe_mode,
            "cloud_providers_disabled": self._safe_mode,
            "remote_clients_restricted": self._safe_mode,
            "mobile_clients_restricted": self._safe_mode,
            "wearables_restricted": self._safe_mode,
            "model_preload_disabled": self._safe_mode,
            "optional_indexing_paused": self._safe_mode,
        }

    def _persist(self, key: str, value: str) -> None:
        if self._factory is None:
            return
        try:
            with self._factory() as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO production_recovery (key, value) VALUES (?, ?)", (key, value)
                )
                connection.commit()
        except sqlite3.Error:
            pass
