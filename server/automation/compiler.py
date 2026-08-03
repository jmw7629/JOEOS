"""Workflow Validator and Compiler for the JoeOS Automation Platform.

Validation is strict and never executes workflow code. The compiler turns a
validated definition into an executable plan with entry point, reachability,
cycle handling for bounded loops, parallel groups, and joins. Compilation is
not permission approval; permissions are enforced at execution time.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

from .models import EdgeConfig, NodeConfig, WorkflowDefinition

NON_RETRYABLE_ACTION_ERRORS = frozenset(
    {
        "invalid_input",
        "permission_denied",
        "cancelled",
        "unsupported_capability",
        "secret_unavailable",
        "approval_denied",
    }
)


class ValidationError(RuntimeError):
    pass


def validate_definition(definition: WorkflowDefinition) -> None:
    """Validate a workflow definition without executing anything."""
    nodes = {node.id: node for node in definition.nodes}
    edges = definition.edges

    if not definition.name.strip():
        raise ValidationError("workflow name is required.")
    for trigger in definition.triggers:
        if trigger.type in {"scheduled", "interval", "weekly", "monthly", "weekdays"} and trigger.schedule is None:
            raise ValidationError("trigger %s requires a schedule." % trigger.trigger_id)
        if trigger.type == "event" and not trigger.event_class:
            raise ValidationError("event trigger %s requires an event class." % trigger.trigger_id)

    for node in definition.nodes:
        if node.retry.max_attempts < 1 or node.retry.max_attempts > 20:
            raise ValidationError("node %s has an invalid retry limit." % node.id)
        if node.loop is not None:
            if node.loop.max_iterations < 1 or node.loop.max_iterations > 1000:
                raise ValidationError("node %s loop limit is out of range." % node.id)
            if not node.loop.item_source and node.type == "loop":
                raise ValidationError("loop node %s requires an item source." % node.id)

    source_ids = {edge.source for edge in edges}
    target_ids = {edge.target for edge in edges}
    branch_nodes = {"condition", "switch", "parallel", "join", "loop"}
    for node_id, node in nodes.items():
        if node.type in branch_nodes:
            continue
        if node.type != "end" and node_id not in source_ids:
            if node.type == "start":
                continue
            raise ValidationError("node %s has no outgoing edge (unreachable or dead end)." % node_id)
        if node.type == "start" and node_id in target_ids:
            raise ValidationError("the start node cannot have incoming edges.")

    ends = [node for node in definition.nodes if node.type == "end"]
    if not ends:
        raise ValidationError("workflow has no end node.")

    # Detect unbounded cycles (non-loop cycles).
    for node_id, node in nodes.items():
        if node.loop is None:
            visited = _dfs_bounded(node_id, nodes, edges)
            del visited

    _detect_unbounded_cycles(nodes, edges)


def _dfs_bounded(start: str, nodes: Dict[str, NodeConfig], edges: Sequence[EdgeConfig]) -> Set[str]:
    visited: Set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        for edge in edges:
            if edge.source == current:
                stack.append(edge.target)
    return visited


def _detect_unbounded_cycles(nodes: Dict[str, NodeConfig], edges: Sequence[EdgeConfig]) -> None:
    adjacency: Dict[str, List[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    state: Dict[str, int] = {}

    def visit(node_id: str, path: List[str]) -> None:
        state[node_id] = 1
        path.append(node_id)
        for target in adjacency.get(node_id, ()):
            if target == node_id:
                node = nodes.get(node_id)
                if node is None or node.loop is None:
                    raise ValidationError("self-loop on node %s without a bounded loop." % node_id)
                continue
            if state.get(target) == 1:
                cycle = path[path.index(target):] + [target]
                cycle_nodes = [nodes.get(item) for item in cycle]
                if not any(item is not None and item.loop is not None for item in cycle_nodes):
                    raise ValidationError("unbounded cycle detected: %s" % " -> ".join(cycle))
                continue
            if state.get(target, 0) == 0:
                visit(target, path)
        path.pop()
        state[node_id] = 2

    for node_id in nodes:
        if state.get(node_id, 0) == 0:
            visit(node_id, [])


class CompiledPlan:
    def __init__(
        self,
        *,
        workflow_id: str,
        version: str,
        entry: str,
        nodes: Dict[str, NodeConfig],
        edges: List[EdgeConfig],
        parallel_groups: Tuple[Tuple[str, ...], ...],
        side_effects: Tuple[str, ...],
        required_permissions: Tuple[str, ...],
    ) -> None:
        self.workflow_id = workflow_id
        self.version = version
        self.entry = entry
        self.nodes = nodes
        self.edges = edges
        self.parallel_groups = parallel_groups
        self.side_effects = side_effects
        self.required_permissions = required_permissions

    def successors(self, node_id: str) -> List[EdgeConfig]:
        return [edge for edge in self.edges if edge.source == node_id]


def compile_workflow(definition: WorkflowDefinition) -> CompiledPlan:
    """Compile a validated definition into an executable plan."""
    validate_definition(definition)
    nodes = {node.id: node for node in definition.nodes}
    edges = list(definition.edges)
    start = next(node for node in definition.nodes if node.type == "start")
    side_effects: List[str] = []
    for node in definition.nodes:
        if node.type == "action" and node.action:
            side_effects.append(node.action)
        side_effects.extend(node.side_effects)
    required = list(dict.fromkeys(definition.required_permissions))
    parallel_groups: List[Tuple[str, ...]] = []
    for node in definition.nodes:
        if node.type == "parallel" and node.parallel_nodes:
            parallel_groups.append(tuple(node.parallel_nodes))
    return CompiledPlan(
        workflow_id=definition.workflow_id,
        version=definition.version,
        entry=start.id,
        nodes=nodes,
        edges=edges,
        parallel_groups=tuple(parallel_groups),
        side_effects=tuple(sorted(set(side_effects))),
        required_permissions=tuple(required),
    )


def is_retryable_error(error_code: str, retryable_errors: Sequence[str]) -> bool:
    if error_code in NON_RETRYABLE_ACTION_ERRORS:
        return False
    return error_code in retryable_errors