"""JoeOS Automation and Workflow Platform.

A local-first, human-governed automation engine. Workflows are versioned,
validated, compiled, and executed through a real state machine with bounded
loops and parallelism, retries and timeouts, approvals and user input,
idempotency and deduplication, concurrency and locks, rate limits,
compensation, pause/resume/cancel, recovery, dry run, simulation, testing,
timezone-aware schedules, and real run history and traces.

A workflow never receives authority merely because it exists; permissions are
declared, granted, and enforced. Secrets are brokered and never exposed.
"""

from .actions import ActionError, ActionRegistry
from .compiler import CompiledPlan, compile_workflow, validate_definition
from .expressions import ExpressionError, evaluate_condition, evaluate_expression
from .history import RunHistory, WorkflowHealthService
from .models import (
    ApprovalRequest,
    ConcurrencyPolicy,
    EdgeConfig,
    FailurePolicy,
    LoopConfig,
    NodeConfig,
    Recurrence,
    ResourcePolicy,
    RetryPolicy,
    RunRecord,
    ScheduleRecord,
    TimeoutPolicy,
    TriggerConfig,
    UserInputRequest,
    VariableDef,
    WorkflowDefinition,
    WorkflowOverview,
    WorkflowRecord,
    WorkflowVersion,
)
from .permissions import WorkflowPermissionGuard
from .router import router as automation_router
from .schedules import ScheduleError, ScheduleService
from .secrets import WorkflowSecretBroker
from .service import AutomationService
from .storage import AutomationStorage
from .triggers import TriggerRegistry
from .workflows import WorkflowError, WorkflowRegistry, parse_definition

__all__ = [
    "ActionError",
    "ActionRegistry",
    "ApprovalRequest",
    "AutomationService",
    "AutomationStorage",
    "automation_router",
    "CompiledPlan",
    "ConcurrencyPolicy",
    "EdgeConfig",
    "ExpressionError",
    "FailurePolicy",
    "LoopConfig",
    "NodeConfig",
    "Recurrence",
    "ResourcePolicy",
    "RetryPolicy",
    "RunHistory",
    "RunRecord",
    "ScheduleError",
    "ScheduleRecord",
    "ScheduleService",
    "TimeoutPolicy",
    "TriggerConfig",
    "TriggerRegistry",
    "UserInputRequest",
    "VariableDef",
    "WorkflowDefinition",
    "WorkflowError",
    "WorkflowHealthService",
    "WorkflowOverview",
    "WorkflowPermissionGuard",
    "WorkflowRecord",
    "WorkflowRegistry",
    "WorkflowSecretBroker",
    "WorkflowVersion",
    "compile_workflow",
    "evaluate_condition",
    "evaluate_expression",
    "parse_definition",
    "validate_definition",
]