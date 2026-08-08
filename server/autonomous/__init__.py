"""Autonomous operations platform.

Durable, agent-based automations that execute through the existing AgentFabric.
"""

from .executor import AgentFabricAutomationExecutor
from .models import (
    AutomationDefinition,
    AutomationDefinitionCreate,
    AutomationRun,
    RetryPolicySpec,
    NotificationPolicySpec,
    TriggerSpec,
    RecurrenceSpec,
)
from .notifier import AutomationNotifier
from .scheduler import AutonomousScheduler
from .service import AutonomousError, AutonomousService
from .storage import AutonomousStore
from .router import router as autonomous_router

__all__ = [
    "AgentFabricAutomationExecutor",
    "AutomationDefinition",
    "AutomationDefinitionCreate",
    "AutomationNotifier",
    "AutomationRun",
    "AutonomousError",
    "AutonomousScheduler",
    "AutonomousService",
    "AutonomousStore",
    "NotificationPolicySpec",
    "RecurrenceSpec",
    "RetryPolicySpec",
    "TriggerSpec",
    "autonomous_router",
]
