"""Dependency Resolver for the JoeOS Plugin Platform.

Resolves declared plugin dependencies (required and optional), detects
circles, disables propagation, and quarantines. Dependency resolution never
inherits permissions: each plugin gets its own permission review.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .compatibility import plain_version_matches


class DependencyResolutionError(RuntimeError):
    pass


def resolve_dependencies(
    *,
    plugin_id: str,
    dependencies: Sequence,
    version_of: Callable[[str], Optional[str]],
    state_of: Callable[[str], str],
) -> Tuple[List[str], List[str]]:
    """Return (blocks, warnings) for a plugin's declared dependencies.

    ``version_of`` yields the installed version for a plugin id (or None).
    ``state_of`` yields the current lifecycle/health state.
    """
    blocks: List[str] = []
    warnings: List[str] = []
    for dependency in dependencies:
        installed = version_of(dependency.plugin_id)
        if installed is None:
            if dependency.optional:
                warnings.append("optional dependency %s is not installed." % dependency.plugin_id)
            else:
                blocks.append("missing required dependency %s." % dependency.plugin_id)
            continue
        if not plain_version_matches(installed, dependency.version_range):
            if dependency.optional:
                warnings.append(
                    "optional dependency %s version %s is outside %s."
                    % (dependency.plugin_id, installed, dependency.version_range)
                )
            else:
                blocks.append(
                    "dependency %s %s does not satisfy %s."
                    % (dependency.plugin_id, installed, dependency.version_range)
                )
            continue
        state = state_of(dependency.plugin_id)
        if state in {"quarantined", "disabled_after_crash", "incompatible"}:
            if dependency.optional:
                warnings.append("optional dependency %s is %s." % (dependency.plugin_id, state))
            else:
                blocks.append("dependency %s is %s." % (dependency.plugin_id, state))
    return blocks, warnings


def detect_circular_references(dependencies: Dict[str, Sequence]) -> Tuple[str, ...]:
    """Return any plugin ids that participate in a dependency cycle."""
    visiting: set = set()
    visited: set = set()
    stack: list = []
    cycles: set = set()

    def visit(node: str) -> None:
        if node in visiting:
            cycle_start = stack.index(node) if node in stack else 0
            for item in stack[cycle_start:]:
                cycles.add(item)
            cycles.add(node)
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        for child in dependencies.get(node, ()) or ():
            visit(child)
        stack.pop()
        visiting.discard(node)
        visited.add(node)

    for node in dependencies:
        visit(node)
    return tuple(sorted(cycles))


class PluginDependencyResolver:
    """Registry-aware dependency resolver with circular-reference detection."""

    def __init__(
        self,
        records: Callable[[], Dict[str, object]],
        state_of: Callable[[str], str],
    ) -> None:
        self._records_provider = records
        self._state_of = state_of

    def version_of(self, plugin_id: str) -> Optional[str]:
        record = self._records_provider().get(plugin_id)
        if record is None:
            return None
        return getattr(record, "version", None)

    def resolve(self, plugin_id: str) -> Tuple[List[str], List[str]]:
        records = self._records_provider()
        plugin = records.get(plugin_id)
        if plugin is None:
            raise DependencyResolutionError("plugin %s is not installed." % plugin_id)
        dependencies = getattr(plugin, "manifest", None).dependencies if getattr(plugin, "manifest", None) else []
        return resolve_dependencies(
            plugin_id=plugin_id,
            dependencies=dependencies,
            version_of=self.version_of,
            state_of=self._state_of,
        )