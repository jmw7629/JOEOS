"""Hybrid retrieval: exact, symbol, dependency, and structured search.

No embedding model is required. Retrieval combines filename/content exact
matches, symbol matches, and reference (dependency) hits into a ranked,
explainable result set. Semantic embedding results can be added later behind
the same envelope without changing the contract.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Dict, List, Optional, Tuple

from .models import (
    ContextPack,
    ContextPackItem,
    Provenance,
    RetrievalEnvelope,
    RetrievalResult,
)

_SYMBOL_WEIGHT = 0.6
_FILENAME_WEIGHT = 0.9
_CONTENT_WEIGHT = 0.4
_REFERENCE_WEIGHT = 0.35


class RetrievalService:
    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    def search(self, project_id: str, query: str, *, limit: int = 25) -> RetrievalEnvelope:
        started = time.monotonic()
        query_lower = query.lower().strip()
        if not query_lower:
            return RetrievalEnvelope(project_id=project_id, query=query, results=(), seconds=0.0)
        tokens = self._tokens(query_lower)
        scored: Dict[str, Dict[str, object]] = {}
        with self._connection_factory() as connection:
            self._score_symbols(connection, project_id, tokens, scored)
            self._score_files(connection, project_id, query_lower, tokens, scored)
            self._score_references(connection, project_id, tokens, scored)
        results: List[RetrievalResult] = []
        for target, data in sorted(scored.items(), key=lambda item: float(item[1]["score"]), reverse=True)[:limit]:
            factors = sorted(data["factors"])
            results.append(
                RetrievalResult(
                    result_id=_id(project_id, target),
                    project_id=project_id,
                    kind=str(data["kind"]),
                    target=target,
                    score=min(1.0, float(data["score"])),
                    ranking_factors=tuple(factors[:8]),
                    provenance=Provenance(kind="parser", source="structured retrieval", detected_at=_now()),
                    freshness="current" if not data.get("stale") else "stale",
                    navigation_target=target,
                )
            )
        return RetrievalEnvelope(
            project_id=project_id,
            query=query,
            results=tuple(results),
            truncated=len(scored) > limit,
            hybrid=True,
            seconds=round(time.monotonic() - started, 4),
        )

    def context_pack(self, project_id: str, objective: str, targets: Tuple[str, ...], *, limit: int = 64) -> ContextPack:
        items: List[ContextPackItem] = []
        seen = set()
        with self._connection_factory() as connection:
            for target in targets:
                if target in seen:
                    continue
                seen.add(target)
                item = self._file_item(connection, project_id, target, limit)
                if item is not None:
                    items.append(item)
                for ref in self._related(connection, project_id, target, limit):
                    if ref.target in seen:
                        continue
                    seen.add(ref.target)
                    items.append(ref)
                if len(items) >= limit:
                    break
        return ContextPack(
            pack_id=_id(project_id, objective, time.time_ns()),
            project_id=project_id,
            objective=objective,
            items=tuple(items),
            generated_at=_now(),
        )

    def _score_symbols(self, connection, project_id: str, tokens: Tuple[str, ...], scored: Dict[str, Dict[str, object]]) -> None:
        for token in tokens[:4]:
            rows = connection.execute(
                """
                SELECT DISTINCT s.file_id, f.rel_path, s.name, s.confidence, f.stale
                FROM intelligence_symbols s
                JOIN intelligence_files f ON f.file_id = s.file_id
                WHERE s.project_id = ? AND (lower(s.name) LIKE ? OR lower(s.qualified_name) LIKE ?)
                LIMIT 200
                """,
                (project_id, "%" + token + "%", "%" + token + "%"),
            ).fetchall()
            for row in rows:
                self._bump(scored, row["rel_path"], _SYMBOL_WEIGHT, "symbol match", row["stale"], "symbol", row["name"])

    def _score_files(self, connection, project_id: str, query_lower: str, tokens: Tuple[str, ...], scored: Dict[str, Dict[str, object]]) -> None:
        rows = connection.execute(
            """
            SELECT rel_path, file_name, language, classification, stale
            FROM intelligence_files
            WHERE project_id = ? AND (lower(rel_path) LIKE ? OR lower(file_name) LIKE ?)
            LIMIT 400
            """,
            (project_id, "%" + query_lower + "%", "%" + query_lower + "%"),
        ).fetchall()
        for row in rows:
            if query_lower in row["rel_path"].lower() or query_lower in row["file_name"].lower():
                self._bump(scored, row["rel_path"], _FILENAME_WEIGHT, "filename match", row["stale"], "file", "")
            else:
                self._bump(scored, row["rel_path"], _CONTENT_WEIGHT, "partial filename match", row["stale"], "file", "")

    def _score_references(self, connection, project_id: str, tokens: Tuple[str, ...], scored: Dict[str, Dict[str, object]]) -> None:
        for token in tokens[:4]:
            rows = connection.execute(
                """
                SELECT DISTINCT f.rel_path, f.stale, r.target_text
                FROM intelligence_references r
                JOIN intelligence_files f ON f.file_id = r.source_file_id
                WHERE r.project_id = ? AND lower(r.target_text) LIKE ?
                LIMIT 200
                """,
                (project_id, "%" + token + "%"),
            ).fetchall()
            for row in rows:
                self._bump(scored, row["rel_path"], _REFERENCE_WEIGHT, "reference match", row["stale"], "reference", row["target_text"])

    def _file_item(self, connection, project_id: str, target: str, limit: int) -> Optional[ContextPackItem]:
        row = connection.execute(
            """
            SELECT rel_path, file_name, classification, language, stale
            FROM intelligence_files WHERE project_id = ? AND rel_path = ?
            """,
            (project_id, target),
        ).fetchone()
        if row is None:
            return None
        return ContextPackItem(
            item_id=_id(project_id, "file", target),
            kind="file",
            target=row["rel_path"],
            source="file inventory",
            reason="explicit target for objective",
            token_estimate=_estimate(512),
            confidence="reported",
            freshness="current" if not row["stale"] else "stale",
        )

    def _related(self, connection, project_id: str, target: str, limit: int) -> List[ContextPackItem]:
        row = connection.execute(
            "SELECT file_id FROM intelligence_files WHERE project_id = ? AND rel_path = ?",
            (project_id, target),
        ).fetchone()
        if row is None:
            return []
        file_id = row["file_id"]
        items: List[ContextPackItem] = []
        edges = connection.execute(
            """
            SELECT target_rel_path FROM intelligence_dependency_edges
            WHERE project_id = ? AND source_file_id = ? LIMIT ?
            """,
            (project_id, file_id, limit),
        ).fetchall()
        for edge in edges:
            items.append(
                ContextPackItem(
                    item_id=_id(project_id, "dep", edge["target_rel_path"]),
                    kind="dependency",
                    target=edge["target_rel_path"],
                    source="dependency graph",
                    reason="imported by target file",
                    token_estimate=_estimate(256),
                    confidence="reported",
                )
            )
        return items

    def _bump(self, scored: Dict[str, Dict[str, object]], target: str, weight: float, factor: str, stale: bool, kind: str, name: str) -> None:
        entry = scored.setdefault(
            target,
            {"score": 0.0, "factors": set(), "stale": bool(stale), "kind": kind, "name": name},
        )
        entry["score"] = min(1.0, float(entry["score"]) + weight)
        entry["factors"].add(factor)

    def _tokens(self, query_lower: str) -> Tuple[str, ...]:
        tokens = re.split(r"[\s/._-]+", query_lower)
        return tuple(t for t in tokens if len(t) >= 2)[:6]


def _id(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _estimate(tokens: int) -> int:
    return max(1, tokens * 4)
