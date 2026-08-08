"""AgentFabric bridge for autonomous operations.

Executes an AutomationRun through the EXACT interactive AgentFabric path:
AutomationRun -> AgentRun -> AgentVersion -> ProviderRegistry -> ModelRegistry
-> Ollama -> delegation/TaskGraph/ToolBroker -> result. No automation-specific
model invocation exists. Automations gain zero extra authority: the same
principal, ToolBroker, policy, approval, and execution boundaries apply.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from .models import AutomationDefinition, AutomationRun
from .service import AutonomousService

logger = logging.getLogger(__name__)

AGENT_KEYS = {
    "auto": "joeos.joe",
    "joe": "joeos.joe",
    "architect": "joeos.architect",
    "builder": "joeos.builder",
    "researcher": "joeos.researcher",
    "verifier": "joeos.verifier",
    "security": "joeos.security",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentFabricAutomationExecutor:
    """Runs an automation occurrence through the control-plane AgentFabric.

    The injected ``action_service`` is the authoritative ActionService. Runs
    are created with the automation owner principal so capabilities, tools,
    policy, and approvals behave identically to an interactive agent run."""

    def __init__(
        self,
        action_service,
        *,
        principal_resolver: Optional[Callable[[str], Dict]] = None,
        default_model: str = "qwen2.5-coder:1.5b",
    ) -> None:
        self._action_service = action_service
        self._principal_resolver = principal_resolver
        self._default_model = default_model

    def _principal_for(self, definition: AutomationDefinition) -> Dict:
        """Resolve the automation owner principal from authoritative identity.

        The control plane stores ids as UUID objects and compares them directly,
        so we return UUIDs to match the interactive principal shape."""
        from uuid import UUID
        try:
            user_id = UUID(definition.owner_principal_id)
            org_id = UUID(definition.organization_id)
            ws_id = UUID(definition.workspace_id)
        except (ValueError, TypeError):
            user_id = definition.owner_principal_id
            org_id = definition.organization_id
            ws_id = definition.workspace_id
        if self._principal_resolver is not None:
            try:
                resolved = self._principal_resolver(definition.owner_principal_id)
                if resolved:
                    return resolved
            except Exception:  # pragma: no cover - defensive
                pass
        return {
            "session_id": None,
            "device_id": None,
            "user": {"id": user_id, "display_name": "", "status": "active"},
            "organization": {"id": org_id},
            "workspace": {"id": ws_id, "name": ""},
            "roles": ["joeos.owner"],
            "capabilities": [],
        }

    def _agent_id(self, principal: Dict, agent_ref: str) -> Optional[str]:
        if agent_ref == "council":
            return None
        key = AGENT_KEYS.get(agent_ref, AGENT_KEYS["auto"])
        for agent in self._action_service.list_agents(principal):
            if agent["key"] == key and agent["status"] == "active":
                return agent["id"]
        # Fall back to any active agent of the same key across org (defensive).
        return None

    async def execute(self, definition: AutomationDefinition, run: AutomationRun,
                      service: AutonomousService) -> Dict:
        """Create + execute a real AgentRun for the occurrence. Returns the
        control-plane run payload (provider/model/result)."""
        principal = self._principal_for(definition)
        agent_id = self._agent_id(principal, definition.agent_ref)
        if agent_id is None:
            raise RuntimeError("no active agent for ref %s" % definition.agent_ref)

        conversation_id = uuid.uuid4()
        message_id = uuid.uuid4()
        objective = (
            "Background automation: %s\n"
            "Trigger: %s (scheduled %s)\n"
            "Objective: %s" % (
                definition.name, run.trigger_kind, run.scheduled_for or "manual", definition.objective,
            )
        )[:4000]

        run_payload = self._action_service.start_agent_run(
            principal,
            agent_id=agent_id,
            conversation_id=conversation_id,
            message_id=message_id,
            model_preference=self._default_model if definition.agent_ref in ("joe", "auto", "architect") else None,
            objective=objective,
        )
        executed = await self._action_service.execute_agent_run(principal, run_payload["id"])
        return {
            "agent_run_id": str(executed["id"]),
            "status": executed.get("status"),
            "provider_key": executed.get("provider_key"),
            "model_key": executed.get("model_key"),
            "result": executed.get("result", ""),
            "failure": executed.get("failure", ""),
        }
