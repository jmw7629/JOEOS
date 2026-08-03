"""Intelligence platform facade: identity, inventory, graphs, git intel,
change impact, risk, knowledge, retrieval, and index lifecycle."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple

from .analysis import AnalysisService
from .gitintel import GitIntelligenceService
from .graph import GraphBuilder
from .identity import IdentityService
from .indexer import IndexingEngine
from .inventory import InventoryService
from .knowledge import KnowledgeService
from .models import (
    ArchitectureGraph,
    ChangeImpact,
    ContextPack,
    DecisionRecord,
    ConventionRecord,
    DependencyGraph,
    GitIntelligence,
    IndexHealth,
    MemoryEntry,
    MemoryStatus,
    ProjectIdentity,
    ProjectOverview,
    RepositoryFingerprint,
    RetrievalEnvelope,
    RiskFinding,
)
from .retrieval import RetrievalService
from .storage import Storage


class IntelligenceService:
    def __init__(
        self,
        project_service,
        data_dir: str,
        *,
        git_binary: str = "git",
    ) -> None:
        self._projects = project_service
        self._storage = Storage(data_dir)
        self._storage.prepare()

        def connection_factory() -> sqlite3.Connection:
            return self._storage.connect()

        self.identity = IdentityService(project_service)
        self.inventory = InventoryService(self._projects.root_path, connection_factory)
        self.knowledge = KnowledgeService(connection_factory)
        self._git_intel = GitIntelligenceService(git_binary)
        self.analysis = AnalysisService()
        self.retrieval = RetrievalService(connection_factory)
        self._engine = IndexingEngine(
            self._storage,
            self.inventory,
            self.knowledge,
            self._git_intel,
            self.analysis,
            self._projects.root_path,
            connection_factory,
        )
        self.inventory.prepare()
        self.knowledge.prepare()

    # ---- identity & fingerprint ----

    def identity_for(self, project_id: str) -> ProjectIdentity:
        self._require_project(project_id)
        return self.identity.derive(project_id)

    def fingerprint_for(self, project_id: str) -> RepositoryFingerprint:
        self._require_project(project_id)
        record = self._projects.get(project_id)
        return self.identity.fingerprint(
            project_id,
            record.name,
            self.identity._git_facts(record.path).remote_url,
            record.path,
        )

    def project_overview(self, project_id: str) -> ProjectOverview:
        self._require_project(project_id)
        identity = self.identity_for(project_id)
        fingerprint = self.fingerprint_for(project_id)
        health = self.index_health(project_id)
        symbols = self._engine.diagnostics(project_id).symbols
        records = self.inventory.load(project_id)
        return ProjectOverview(
            project_id=project_id,
            identity=identity,
            fingerprint=fingerprint,
            files=len(records),
            symbols=symbols,
            parse_failures=self._engine.diagnostics(project_id).parse_failures,
            cycles=len(self.dependency_graph(project_id).cycles),
            hotspots=self.git_intelligence(project_id).hotspots[:16],
            decisions=self.knowledge.decisions(project_id)[:16],
            conventions=self.knowledge.conventions(project_id)[:16],
            health=health,
            generated_at=_now(),
        )

    # ---- inventory & classification ----

    def file_inventory(self, project_id: str) -> Tuple:
        self._require_project(project_id)
        return self.inventory.scan(project_id, self._projects.root_path(project_id))

    # ---- graphs ----

    def dependency_graph(self, project_id: str) -> DependencyGraph:
        self._require_project(project_id)
        builder = GraphBuilder(project_id)
        for record in self.inventory.load(project_id):
            if not record.secret_sensitive:
                builder.register_file(record.file_id, record.rel_path)
        with self._storage.connect() as connection:
            rows = connection.execute(
                "SELECT source_file_id, target_text, kind, line, resolution "
                "FROM intelligence_references WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        for row in rows:
            builder.register_import(row["source_file_id"], row["target_text"], row["kind"], row["line"])
        return builder.build(_now())

    def architecture_graph(self, project_id: str) -> ArchitectureGraph:
        self._require_project(project_id)
        graph = self.dependency_graph(project_id)
        return self._architecture_from_deps(project_id, graph)

    def _architecture_from_deps(self, project_id: str, graph: DependencyGraph) -> ArchitectureGraph:
        builder = GraphBuilder(project_id)
        for record in self.inventory.load(project_id):
            if not record.secret_sensitive:
                builder.register_file(record.file_id, record.rel_path)
        with self._storage.connect() as connection:
            rows = connection.execute(
                "SELECT source_file_id, target_text, kind, line "
                "FROM intelligence_references WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        for row in rows:
            builder.register_import(row["source_file_id"], row["target_text"], row["kind"], row["line"])
        return builder.architecture(_now())

    # ---- git intelligence ----

    def git_intelligence(self, project_id: str) -> GitIntelligence:
        self._require_project(project_id)
        root = self._projects.root_path(project_id)
        return self._git_intel.collect(project_id, str(root))

    # ---- change impact & risk ----

    def change_impact(self, project_id: str, target: str) -> Tuple[ChangeImpact, ...]:
        self._require_project(project_id)
        forward, reverse, classification, file_id = self._relationship_maps(project_id)
        return self.analysis.change_impact(project_id, target, forward, reverse, classification, file_id)

    def risk_findings(self, project_id: str) -> Tuple[RiskFinding, ...]:
        self._require_project(project_id)
        with self._storage.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM intelligence_risk_findings WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        return tuple(_risk_from_row(row) for row in rows)

    def _relationship_maps(self, project_id: str):
        forward: dict = {}
        reverse: dict = {}
        classification: dict = {}
        file_id: dict = {}
        graph = self.dependency_graph(project_id)
        for edge in graph.edges:
            forward.setdefault(edge.source_rel_path, set()).add(edge.target_rel_path)
            reverse.setdefault(edge.target_rel_path, set()).add(edge.source_rel_path)
        for record in self.inventory.load(project_id):
            classification[record.rel_path] = record.classification
            file_id[record.rel_path] = record.file_id
        return forward, reverse, classification, file_id

    # ---- knowledge ----

    def decisions(self, project_id: str) -> Tuple[DecisionRecord, ...]:
        self._require_project(project_id)
        return self.knowledge.decisions(project_id)

    def conventions(self, project_id: str) -> Tuple[ConventionRecord, ...]:
        self._require_project(project_id)
        return self.knowledge.conventions(project_id)

    def add_memory(self, project_id: str, entry: MemoryEntry) -> MemoryEntry:
        self._require_project(project_id)
        now = _now()
        updated = entry.model_copy(
            update={
                "memory_id": entry.memory_id,
                "updated_at": now,
            }
        )
        return self.knowledge.add_memory(updated, now)

    def update_memory(self, project_id: str, memory_id: str, status: MemoryStatus) -> bool:
        self._require_project(project_id)
        return self.knowledge.update_memory_status(project_id, memory_id, status, _now())

    def memories(self, project_id: str) -> Tuple[MemoryEntry, ...]:
        self._require_project(project_id)
        return self.knowledge.memories(project_id)

    # ---- retrieval ----

    def search(self, project_id: str, query: str, *, limit: int = 25) -> RetrievalEnvelope:
        self._require_project(project_id)
        return self.retrieval.search(project_id, query, limit=limit)

    def context_pack(self, project_id: str, objective: str, targets: Tuple[str, ...], *, limit: int = 64) -> ContextPack:
        self._require_project(project_id)
        return self.retrieval.context_pack(project_id, objective, targets, limit=limit)

    # ---- index lifecycle ----

    def trigger_full_index(self, project_id: str) -> None:
        self._require_project(project_id)
        self._engine.trigger_full_index(project_id)

    def trigger_incremental_index(self, project_id: str) -> None:
        self._require_project(project_id)
        self._engine.trigger_incremental_index(project_id)

    def cancel_index(self, project_id: str) -> None:
        self._require_project(project_id)
        self._engine.cancel(project_id)

    def index_health(self, project_id: str) -> IndexHealth:
        self._require_project(project_id)
        return self._engine.health(project_id)

    def is_indexing(self, project_id: str) -> bool:
        return self._engine.is_running(project_id)

    def backup_index(self, project_id: str) -> Optional[str]:
        self._require_project(project_id)
        return self._storage.backup_to(str(self._storage._data_dir / "backups"))

    def storage_stats(self, project_id: str) -> dict:
        self._require_project(project_id)
        return {
            "path": self._storage.path(),
            "size_bytes": self._storage.size_bytes(),
            "version": 1,
        }

    def _require_project(self, project_id: str) -> None:
        self._projects.get(project_id)


def _risk_from_row(row) -> RiskFinding:
    return RiskFinding(
        risk_id=row["risk_id"],
        project_id=row["project_id"],
        category=row["category"],
        severity=row["severity"],
        confidence=row["confidence"],
        evidence=tuple(x for x in row["evidence"].split("|") if x),
        affected_items=tuple(x for x in row["affected_items"].split("|") if x),
        mitigation=row["mitigation"],
        review_required=bool(row["review_required"]),
        recommended_tests=tuple(x for x in row["recommended_tests"].split("|") if x),
        generated_at=row["generated_at"],
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
