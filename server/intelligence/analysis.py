"""Change-impact analysis and explainable risk findings.

Impact is always expressed as likelihood buckets with a confidence, never as
false certainty. Findings cite the exact evidence that produced them.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from .models import (
    ChangeImpact,
    FileClassification,
    ImpactLikelihood,
    RiskFinding,
    RiskSeverity,
)

_MAX_BFS_DEPTH = 8


class AnalysisService:
    def __init__(self) -> None:
        pass

    def change_impact(
        self,
        project_id: str,
        target_rel_path: str,
        forward: Dict[str, Set[str]],
        reverse: Dict[str, Set[str]],
        classification: Dict[str, FileClassification],
        file_id: Dict[str, str],
    ) -> Tuple[ChangeImpact, ...]:
        """Estimate the reach of changes to `target_rel_path`.

        A change to the target affects the files that import it (its reverse
        neighbors) and, transitively, their importers.
        """
        if target_rel_path not in file_id:
            return ()
        visited: Set[str] = set()
        frontier = set(reverse.get(target_rel_path, ()))
        results: List[ChangeImpact] = []
        depth = 0
        while frontier and depth <= _MAX_BFS_DEPTH:
            depth += 1
            next_frontier: Set[str] = set()
            for path in frontier:
                if path in visited or path == target_rel_path:
                    continue
                visited.add(path)
                likelihood = _likelihood(depth, path, forward, reverse)
                results.append(
                    ChangeImpact(
                        project_id=project_id,
                        target=target_rel_path,
                        impacted_file_id=file_id.get(path, ""),
                        impacted_path=path,
                        relationship="imports (direct chain)" if depth == 1 else "imports (transitive)",
                        confidence="reported" if depth == 1 else "inferred",
                        depth=depth,
                        likelihood=likelihood,
                        recommended_validation=_validation(path, classification),
                    )
                )
                next_frontier.update(reverse.get(path, ()))
            frontier = next_frontier
        return tuple(sorted(results, key=lambda r: (r.depth, r.impacted_path)))

    def risk_findings(
        self,
        project_id: str,
        *,
        secrets: Tuple[str, ...],
        parse_failures: Tuple[str, ...],
        unresolved: Tuple[str, ...],
        graph_cycles: Tuple[Tuple[str, ...], ...],
        stale: Tuple[str, ...],
        hotspots: Tuple[str, ...],
        generated_files: Tuple[str, ...],
        now: str,
    ) -> Tuple[RiskFinding, ...]:
        findings: List[RiskFinding] = []
        if secrets:
            findings.append(
                RiskFinding(
                    risk_id=_risk_id("secrets", project_id),
                    project_id=project_id,
                    category="secret_bearing_files_present",
                    severity="high",
                    confidence="reported",
                    evidence=tuple(secrets),
                    affected_items=tuple(secrets),
                    mitigation="These files are excluded from indexing; verify they are not committed.",
                    review_required=True,
                    generated_at=now,
                )
            )
        if parse_failures:
            findings.append(
                RiskFinding(
                    risk_id=_risk_id("parse_failures", project_id),
                    project_id=project_id,
                    category="parse_failures",
                    severity="medium",
                    confidence="reported",
                    evidence=tuple(parse_failures[:32]),
                    affected_items=tuple(parse_failures[:32]),
                    mitigation="Check files with broken syntax or unsupported constructs.",
                    review_required=False,
                    generated_at=now,
                )
            )
        if unresolved:
            findings.append(
                RiskFinding(
                    risk_id=_risk_id("unresolved_references", project_id),
                    project_id=project_id,
                    category="unresolved_references",
                    severity="medium",
                    confidence="inferred",
                    evidence=tuple(unresolved[:32]),
                    affected_items=tuple(unresolved[:32]),
                    mitigation="References to missing files may indicate broken imports.",
                    review_required=False,
                    generated_at=now,
                )
            )
        if graph_cycles:
            findings.append(
                RiskFinding(
                    risk_id=_risk_id("dependency_cycles", project_id),
                    project_id=project_id,
                    category="dependency_cycles",
                    severity="medium",
                    confidence="reported",
                    evidence=tuple(",".join(c) for c in graph_cycles[:8]),
                    affected_items=tuple(c[0] for c in graph_cycles[:8]),
                    mitigation="Cyclic imports increase coupling and risk of runtime issues.",
                    review_required=True,
                    generated_at=now,
                )
            )
        if hotspots:
            findings.append(
                RiskFinding(
                    risk_id=_risk_id("hotspots", project_id),
                    project_id=project_id,
                    category="change_hotspots",
                    severity="info",
                    confidence="reported",
                    evidence=tuple(hotspots[:8]),
                    affected_items=tuple(hotspots[:8]),
                    mitigation="High-churn files benefit from focused test coverage.",
                    review_required=False,
                    generated_at=now,
                )
            )
        if stale:
            findings.append(
                RiskFinding(
                    risk_id=_risk_id("stale_index", project_id),
                    project_id=project_id,
                    category="stale_index_entries",
                    severity="low",
                    confidence="reported",
                    evidence=tuple(stale[:32]),
                    affected_items=tuple(stale[:32]),
                    mitigation="Run an incremental reindex to refresh parse results.",
                    review_required=False,
                    generated_at=now,
                )
            )
        if generated_files:
            findings.append(
                RiskFinding(
                    risk_id=_risk_id("generated_sources", project_id),
                    project_id=project_id,
                    category="generated_sources_tracked",
                    severity="info",
                    confidence="reported",
                    evidence=tuple(generated_files[:16]),
                    affected_items=tuple(generated_files[:16]),
                    mitigation="Generated files in source trees are usually excluded from review.",
                    review_required=False,
                    generated_at=now,
                )
            )
        return tuple(findings)


def _likelihood(depth: int, path: str, forward: Dict[str, Set[str]], reverse: Dict[str, Set[str]]) -> ImpactLikelihood:
    if depth == 1:
        return "direct"
    inbound = reverse.get(path, set())
    if len(inbound) >= 3:
        return "likely"
    return "possible"


def _validation(path: str, classification: Dict[str, FileClassification]) -> Tuple[str, ...]:
    checks: List[str] = []
    cls = classification.get(path)
    if cls == "test":
        checks.append("run related test suite")
    if cls == "route" or cls == "source":
        checks.append("smoke test the serving path")
    if cls == "script":
        checks.append("run shellcheck or equivalent")
    return tuple(checks[:8])


def _risk_id(kind: str, project_id: str) -> str:
    import hashlib

    return hashlib.sha256(("%s:%s" % (project_id, kind)).encode("utf-8")).hexdigest()[:24]
