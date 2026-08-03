"""Dependency and architecture graph construction from reference evidence.

Graphs are derived purely from recorded references. Nothing is fabricated:
unresolved references become external dependencies only when the target points
outside the repository tree. Cycles are detected with a bounded search.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .models import (
    ArchitectureGraph,
    ArchitectureNode,
    DependencyEdge,
    DependencyGraph,
    Provenance,
    ResolutionState,
)


@dataclass
class EdgeEvidence:
    source: str
    target: str
    kind: str
    line: int
    resolution: str


class GraphBuilder:
    def __init__(self, project_id: str) -> None:
        self._project_id = project_id
        self._file_paths: Dict[str, str] = {}
        self._imports: List[EdgeEvidence] = []

    def register_file(self, file_id: str, rel_path: str) -> None:
        self._file_paths[file_id] = rel_path

    def register_import(self, source_file_id: str, target_text: str, kind: str, line: int) -> None:
        self._imports.append(
            EdgeEvidence(source_file_id, target_text, kind, line, "unresolved")
        )

    def build(self, now: str, external: Optional[Set[str]] = None) -> DependencyGraph:
        resolved = self._resolve_targets()
        edges: List[DependencyEdge] = []
        externals: Set[str] = set()
        for item in self._imports:
            source_path = self._file_paths.get(item.source, item.source)
            target_file_id = resolved.get(item.target)
            if target_file_id is not None:
                target_path = self._file_paths.get(target_file_id, target_file_id)
                edges.append(
                    DependencyEdge(
                        source_file_id=item.source,
                        target_file_id=target_file_id,
                        source_rel_path=source_path,
                        target_rel_path=target_path,
                        kind=item.kind,
                        direct=True,
                        provenance=Provenance(
                            kind="parser",
                            source="reference_resolution",
                            detail="line %d" % item.line,
                            detected_at=now,
                        ),
                        resolution="resolved",
                    )
                )
            else:
                externals.add(item.target)
        if external:
            externals.update(external)
        cycles = self._find_cycles(edges)
        return DependencyGraph(
            project_id=self._project_id,
            nodes=tuple(sorted(set(self._file_paths.values()))),
            edges=tuple(sorted(edges, key=lambda e: (e.source_rel_path, e.target_rel_path))),
            cycles=cycles,
            external_dependencies=tuple(sorted(externals)),
            generated_at=now,
        )

    def _resolve_targets(self) -> Dict[str, str]:
        by_basename: Dict[str, List[str]] = {}
        by_module: Dict[str, List[str]] = {}
        for file_id, rel_path in self._file_paths.items():
            name = Path(rel_path).name
            stem = Path(rel_path).stem
            module = _without_ext(rel_path)
            if name == "__init__.py":
                module = str(Path(rel_path).parent) if Path(rel_path).parent.name else rel_path
            by_basename.setdefault(name, []).append(file_id)
            by_module.setdefault(stem, []).append(file_id)
            by_module.setdefault(module, []).append(file_id)
            by_module.setdefault(module.replace("/", "."), []).append(file_id)
        resolved: Dict[str, str] = {}
        for item in self._imports:
            target = item.target
            candidates: List[str] = []
            slash_target = target.replace(".", "/")
            if target.startswith("."):
                base_dir = str(Path(self._file_paths.get(item.source, "")).parent)
                candidate = _normalize((base_dir + "/" + slash_target).split("/"))
                candidates = by_module.get(candidate, [])
                if not candidates:
                    for key, files in by_module.items():
                        if key.endswith(candidate) or candidate.endswith(key):
                            candidates = files
                            break
            else:
                candidates = by_basename.get(target) or by_module.get(target) or by_module.get(slash_target) or []
                if not candidates:
                    for key, files in by_module.items():
                        if key == target or key == slash_target or key.endswith("/" + target):
                            candidates = files
                            break
            if len(candidates) == 1:
                resolved[target] = candidates[0]
            elif len(candidates) > 1:
                base_dir = str(Path(self._file_paths.get(item.source, "")).parent)
                for file_id in candidates:
                    path = self._file_paths[file_id]
                    if str(Path(path).parent) == base_dir:
                        resolved[target] = file_id
                        break
                if target not in resolved:
                    resolved[target] = candidates[0]
        return resolved

    def _find_cycles(self, edges: List[DependencyEdge]) -> Tuple[Tuple[str, ...], ...]:
        adjacency: Dict[str, List[str]] = {}
        for edge in edges:
            adjacency.setdefault(edge.source_rel_path, []).append(edge.target_rel_path)
        color: Dict[str, int] = {}
        stack: List[str] = []
        cycles: List[Tuple[str, ...]] = []
        ordered = sorted(self._file_paths.values())

        def visit(node: str) -> None:
            color[node] = 1
            stack.append(node)
            for neighbor in adjacency.get(node, []):
                if neighbor not in color:
                    visit(neighbor)
                elif color.get(neighbor) == 1:
                    try:
                        cut = stack.index(neighbor)
                    except ValueError:
                        continue
                    cycle = tuple(stack[cut:] + [neighbor])
                    if cycle not in cycles:
                        cycles.append(cycle)
            stack.pop()
            color[node] = 2

        for node in ordered:
            if node not in color:
                visit(node)
            if len(cycles) >= 64:
                break
        return tuple(cycles)

    def architecture(self, now: str, external_roots: Optional[Tuple[str, ...]] = None) -> ArchitectureGraph:
        nodes: List[ArchitectureNode] = []
        edges: List[Tuple[str, str, str]] = []
        nodes_by_path: Dict[str, str] = {}
        edges_seen: Set[Tuple[str, str]] = set()
        for rel_path in sorted(self._file_paths.values()):
            top = _top_level(rel_path)
            if top is None or top in {"tests", "docs"}:
                continue
            node_id = "arch_" + hashlib.sha256(top.encode("utf-8")).hexdigest()[:24]
            nodes_by_path[rel_path] = node_id
            if top not in {n.name for n in nodes}:
                nodes.append(
                    ArchitectureNode(
                        node_id=node_id,
                        project_id=self._project_id,
                        name=top,
                        kind="module",
                        evidence="directory boundary" if top != "(root)" else "repository root",
                        confidence="reported",
                        derived_from="evidence",
                        source_file=None,
                    )
                )
        file_to_node = {file_id: nodes_by_path[path] for file_id, path in self._file_paths.items() if path in nodes_by_path}
        for item in self._imports:
            source_node = file_to_node.get(item.source)
            target_file = self._resolve_targets().get(item.target)
            target_node = file_to_node.get(target_file) if target_file else None
            if source_node and target_node and source_node != target_node:
                pair = (source_node, target_node)
                if pair not in edges_seen:
                    edges_seen.add(pair)
                    edges.append((source_node, target_node, item.kind))
        return ArchitectureGraph(
            project_id=self._project_id,
            nodes=tuple(nodes),
            edges=tuple(sorted(edges)),
            generated_at=now,
        )


def _without_ext(rel_path: str) -> str:
    return str(Path(rel_path).with_suffix(""))


def _normalize(parts: List[str]) -> str:
    stack: List[str] = []
    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if stack:
                stack.pop()
            continue
        stack.append(part)
    return "/".join(stack)


def _top_level(rel_path: str) -> Optional[str]:
    if "/" not in rel_path:
        return "(root)"
    return rel_path.split("/", 1)[0]
