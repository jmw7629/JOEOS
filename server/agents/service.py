"""AgentsService facade: one authoritative entry point into the multi-agent
collaboration and organizational intelligence platform.

The facade composes the individual services and keeps their connections bound
to a single versioned SQLite database. It never invents activity: every result
is derived from stored state.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .budget import BudgetService
from .collaboration import CollaborationService
from .detection import DetectionService
from .governance import GovernanceService
from .health import HealthService
from .memory_proposals import MemoryProposalService
from .missions import MissionService
from .models import AgentsOverview, MissionEnvelope, OrgHealthRecord
from .organization import OrganizationService
from .routing import RoutingService
from .storage import AgentsStorage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentsService:
    def __init__(self, data_dir: str) -> None:
        self.storage = AgentsStorage(data_dir)
        self.storage.prepare()
        self.organization = OrganizationService(self._connection_factory)
        self.missions = MissionService(self._connection_factory, self.organization)
        self.collaboration = CollaborationService(self._connection_factory)
        self.governance = GovernanceService(self._connection_factory)
        self.budget = BudgetService(self._connection_factory)
        self.routing = RoutingService(self._connection_factory)
        self.detection = DetectionService(self._connection_factory)
        self.health = HealthService(self._connection_factory)
        self.memory_proposals = MemoryProposalService(self._connection_factory)

    def _connection_factory(self):
        connection = self.storage.connect()
        return _BorrowedConnection(connection)

    # ---- organization & agents ----

    def get_or_create_organization(self):
        return self.organization.get_or_create_organization()

    def create_organization(self, name: str, purpose: str = ""):
        return self.organization.create_organization(name, purpose=purpose)

    def overview(self) -> AgentsOverview:
        organization = self.organization.get_or_create_organization()
        return AgentsOverview(
            organization=organization,
            units=self.organization.units(),
            roles=self.organization.roles(),
            agents=self.organization.agents(),
            missions=self.missions.missions(limit=64),
            health=self.health.compute_health(),
            generated_at=_now(),
        )

    def mission_envelope(self, mission_id: str) -> Optional[MissionEnvelope]:
        mission = self.missions.get_mission(mission_id)
        if mission is None:
            return None
        return MissionEnvelope(
            mission=mission,
            charter=self.missions.charter(mission_id),
            plan=self.missions.plan_for(mission_id),
            graph=self.missions.graph(mission_id),
            generated_at=_now(),
        )

    def current_health(self) -> OrgHealthRecord:
        return self.health.compute_health()

    def storage_stats(self) -> dict:
        return {
            "path": self.storage.path(),
            "size_bytes": self.storage.size_bytes(),
            "version": 1,
        }

    def backup(self) -> Optional[str]:
        return self.storage.backup_to(str(Path(self.storage.path()).parent))


class _BorrowedConnection:
    """Context manager wrapper that never closes the shared connection."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> sqlite3.Connection:
        return self._connection

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()