"""Incremental indexing engine with cancellation, health, and isolation.

Indexing runs on a background thread and is cancellable between file steps.
Each file is parsed independently; a single failure never aborts the run.
The engine records diagnostics so the health contract is always satisfied.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from .analysis import AnalysisService
from .graph import GraphBuilder
from .models import (
    IndexDiagnostics,
    IndexHealth,
    IndexHealthState,
    IndexPhase,
)
from .parser import parse, supported_languages

HEALTHY_THRESHOLD = 0.98


class IndexingEngine:
    def __init__(
        self,
        storage,
        inventory,
        knowledge,
        git_intel,
        analysis: AnalysisService,
        root_provider: Callable[[str], Path],
        connection_factory: Callable,
    ) -> None:
        self._storage = storage
        self._inventory = inventory
        self._knowledge = knowledge
        self._git_intel = git_intel
        self._analysis = analysis
        self._root_provider = root_provider
        self._connection_factory = connection_factory
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._lock = threading.RLock()
        self._health: Dict[str, IndexHealth] = {}
        self._diagnostics: Dict[str, IndexDiagnostics] = {}

    # ---- lifecycle ----

    def trigger_full_index(self, project_id: str) -> None:
        with self._lock:
            self._start(project_id, incremental=False)

    def trigger_incremental_index(self, project_id: str) -> None:
        with self._lock:
            self._start(project_id, incremental=True)

    def cancel(self, project_id: str) -> None:
        self._cancel.set()

    def is_running(self, project_id: str) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _start(self, project_id: str, incremental: bool) -> None:
        if self.is_running(project_id):
            return
        self._cancel.clear()
        try:
            root = self._root_provider(project_id)
        except Exception:
            self._set_health(project_id, "degraded", "failed", "project root is not accessible")
            return
        self._thread = threading.Thread(
            target=self._run,
            args=(project_id, incremental, root),
            name="intel-index-%s" % project_id,
            daemon=True,
        )
        self._thread.start()

    # ---- state ----

    def health(self, project_id: str) -> IndexHealth:
        with self._lock:
            return self._health.get(project_id) or _initial_health(project_id, "unknown", "not indexed yet")

    def diagnostics(self, project_id: str) -> IndexDiagnostics:
        with self._lock:
            return self._diagnostics.get(project_id) or _empty_diagnostics(self._storage)

    def _set_health(self, project_id: str, state: IndexHealthState, phase: Optional[IndexPhase], message: str = "", progress: Optional[int] = None) -> None:
        now = _now()
        with self._lock:
            diagnostics = self._diagnostics.setdefault(project_id, _empty_diagnostics(self._storage))
            existing = self._health.get(project_id)
            self._health[project_id] = IndexHealth(
                project_id=project_id,
                state=state,
                phase=phase,
                progress=progress if progress is not None else (existing.progress if existing else None),
                message=message,
                diagnostics=diagnostics,
                updated_at=now,
            )

    # ---- run ----

    def _run(self, project_id: str, incremental: bool, root: Path) -> None:
        try:
            self._set_health(project_id, "indexing", "scanning", progress=0)
            records = self._inventory.scan(project_id, root)
            if self._cancel.is_set():
                self._set_health(project_id, "cancelled", "cancelled", "cancelled by user", progress=0)
                return

            self._set_health(project_id, "indexing", "classifying", progress=15)
            self._ingest_configuration(project_id, records, root)
            if self._cancel.is_set():
                self._set_health(project_id, "cancelled", "cancelled", "cancelled by user", progress=15)
                return

            self._set_health(project_id, "indexing", "parsing", progress=25)
            parse_failures, parse_errors = self._parse_all(project_id, records, root)
            if self._cancel.is_set():
                self._set_health(project_id, "cancelled", "cancelled", "cancelled by user", progress=25)
                return

            self._set_health(project_id, "indexing", "linking", progress=80)
            unresolved, graph = self._link(project_id, records)
            if self._cancel.is_set():
                self._set_health(project_id, "cancelled", "cancelled", "cancelled by user", progress=80)
                return

            self._set_health(project_id, "indexing", "validating", progress=90)
            secrets, generated, stale = self._classify_for_risks(project_id, records)
            hotspots = tuple(h.rel_path for h in self._git_intel.collect(project_id, str(root)).hotspots)
            self._store_risks(
                project_id,
                secrets=secrets,
                parse_failures=parse_failures,
                unresolved=unresolved,
                graph_cycles=graph.cycles,
                stale=stale,
                hotspots=hotspots,
                generated=generated,
            )

            with self._lock:
                previous = self._diagnostics.get(project_id)
                self._diagnostics[project_id] = IndexDiagnostics(
                    indexed_files=len(records),
                    excluded_files=0,
                    parse_failures=len(parse_failures),
                    unresolved_references=len(unresolved),
                    stale_files=len(stale),
                    symbols=self._count_symbols(project_id),
                    relationships=self._count_relationships(project_id),
                    last_full_index=(_now() if not incremental else (previous.last_full_index if previous else None)),
                    last_incremental_update=_now() if incremental else (previous.last_incremental_update if previous else None),
                    parser_versions=tuple(sorted(supported_languages())),
                    storage_version=str(_STORAGE_VERSION_OF(self._storage)),
                    storage_size_bytes=self._storage.size_bytes(),
                    recent_errors=tuple(parse_errors[:16]),
                )
            self._set_health(
                project_id,
                "healthy" if self._diagnostics[project_id].indexed_files == 0 or len(parse_failures) / max(1, len(records)) <= (1 - HEALTHY_THRESHOLD) else "degraded",
                "completed",
                "index complete",
                progress=100,
            )
        except Exception as exc:  # pragma: no cover - defensive
            import traceback

            self._set_health(project_id, "degraded", "failed", "indexing failed: %s" % traceback.format_exc(), progress=None)

    # ---- steps ----

    def _ingest_configuration(self, project_id: str, records, root: Path) -> None:
        meta: List[Tuple[str, str]] = []
        now = _now()
        for record in records:
            if record.secret_sensitive or record.binary or record.size > 200_000:
                continue
            lowered = record.rel_path.lower()
            if lowered.endswith((".prettierrc", ".editorconfig", ".eslintrc", ".eslintrc.json", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.yaml", ".eslintrc.yml")) or ("prettier" in lowered and lowered.endswith((".json", ".yaml", ".yml", ".js", ".cjs", ".toml"))):
                meta.append((record.rel_path, self._read_text(root, record.rel_path)))
            if "adr" in lowered or "decisions" in lowered:
                content = self._read_text(root, record.rel_path)
                if content:
                    self._knowledge.ingest_adr(project_id, record.rel_path, content, now)
        if meta:
            for convention in self._knowledge.detect_conventions(project_id, tuple(meta), now):
                self._store_convention(convention)

    def _parse_all(self, project_id: str, records, root: Path) -> Tuple[List[str], List[str]]:
        failures: List[str] = []
        errors: List[str] = []
        total = len(records)
        for index, record in enumerate(records):
            if self._cancel.is_set():
                break
            if record.secret_sensitive or record.binary or record.parser not in supported_languages():
                continue
            content = self._read_text(root, record.rel_path)
            if not content:
                continue
            result = parse(content, record.parser, project_id, record.file_id, record.rel_path)
            if result.error and result.error.startswith("parser_failure"):
                failures.append(record.rel_path)
                errors.append("%s: %s" % (record.rel_path, result.error))
            self._store_parse(project_id, record.file_id, record.rel_path, result)
            if index % 20 == 0:
                progress = 25 + int(55 * (index / max(1, total)))
                self._set_health(project_id, "indexing", "parsing", progress=progress)
        return failures, errors

    def _link(self, project_id: str, records) -> Tuple[List[str], DependencyGraph]:
        builder = GraphBuilder(project_id)
        for record in records:
            if not record.secret_sensitive:
                builder.register_file(record.file_id, record.rel_path)
        references = self._load_references(project_id)
        now = _now()
        unresolved: List[str] = []
        for ref in references:
            builder.register_import(ref["source_file_id"], ref["target_text"], ref["kind"], ref["line"])
            if ref["resolution"] != "resolved":
                unresolved.append(ref["target_text"])
        graph = builder.build(now)
        self._store_graph(project_id, graph)
        return sorted(set(unresolved)), graph

    def _classify_for_risks(self, project_id: str, records) -> Tuple[List[str], List[str], List[str]]:
        secrets: List[str] = []
        generated: List[str] = []
        stale: List[str] = []
        for record in records:
            if record.secret_sensitive:
                secrets.append(record.rel_path)
            if record.generated:
                generated.append(record.rel_path)
            if record.stale:
                stale.append(record.rel_path)
        return secrets, generated, stale

    # ---- persistence helpers ----

    def _store_parse(self, project_id: str, file_id: str, rel_path: str, result) -> None:
        with self._connection_factory() as connection:
            connection.execute("DELETE FROM intelligence_symbols WHERE file_id = ?", (file_id,))
            connection.execute("DELETE FROM intelligence_references WHERE source_file_id = ?", (file_id,))
            for symbol in result.symbols:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO intelligence_symbols (
                        symbol_id, project_id, file_id, name, qualified_name, kind,
                        language, line, end_line, visibility, exported, signature,
                        parent_symbol, module, documentation, parser, confidence,
                        content_version, stale
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        symbol.symbol_id, project_id, file_id, symbol.name,
                        symbol.qualified_name, symbol.kind, symbol.language,
                        symbol.line, symbol.end_line, symbol.visibility,
                        int(symbol.exported), symbol.signature, symbol.parent_symbol,
                        symbol.module, symbol.documentation, symbol.parser,
                        symbol.confidence, symbol.content_version, int(symbol.stale),
                    ),
                )
            for ref in result.references:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO intelligence_references (
                        reference_id, project_id, source_symbol_id, target_symbol_id,
                        source_file_id, target_file_id, rel_path, target_text, kind,
                        line, resolution, parser, confidence, stale
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ref.reference_id, project_id, ref.source_symbol_id,
                        ref.target_symbol_id, ref.source_file_id, ref.target_file_id,
                        ref.rel_path, ref.target_text, ref.kind, ref.line,
                        ref.resolution, ref.parser, ref.confidence, int(ref.stale),
                    ),
                )
            self._inventory.update_symbol_counts(
                project_id, rel_path, len(result.symbols), len(result.references), None
            )

    def _store_graph(self, project_id: str, graph: DependencyGraph) -> None:
        with self._connection_factory() as connection:
            connection.execute("DELETE FROM intelligence_dependency_edges WHERE project_id = ?", (project_id,))
            for edge in graph.edges:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO intelligence_dependency_edges (
                        source_file_id, target_file_id, source_rel_path,
                        target_rel_path, kind, direct, resolution, project_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge.source_file_id, edge.target_file_id,
                        edge.source_rel_path, edge.target_rel_path,
                        edge.kind, int(edge.direct), edge.resolution, project_id,
                    ),
                )

    def _store_risks(
        self, project_id: str, *, secrets, parse_failures, unresolved, graph_cycles, stale, hotspots, generated
    ) -> None:
        now = _now()
        findings = self._analysis.risk_findings(
            project_id,
            secrets=tuple(secrets),
            parse_failures=tuple(parse_failures),
            unresolved=tuple(unresolved),
            graph_cycles=graph_cycles,
            stale=tuple(stale),
            hotspots=tuple(hotspots),
            generated_files=tuple(generated),
            now=now,
        )
        with self._connection_factory() as connection:
            connection.execute("DELETE FROM intelligence_risk_findings WHERE project_id = ?", (project_id,))
            for finding in findings:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO intelligence_risk_findings (
                        risk_id, project_id, category, severity, confidence,
                        evidence, affected_items, mitigation, review_required,
                        recommended_tests, generated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        finding.risk_id, project_id, finding.category, finding.severity,
                        finding.confidence, "|".join(finding.evidence),
                        "|".join(finding.affected_items), finding.mitigation,
                        int(finding.review_required), "|".join(finding.recommended_tests),
                        finding.generated_at,
                    ),
                )

    def _store_convention(self, convention) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO intelligence_conventions (
                    convention_id, project_id, convention, source_kind, scope,
                    confidence, authoritative, date, exceptions, affected_languages,
                    affected_paths, source_label, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    convention.convention_id, convention.project_id, convention.convention,
                    convention.source_kind, convention.scope, convention.confidence,
                    int(convention.authoritative), convention.date,
                    "|".join(convention.exceptions), "|".join(convention.affected_languages),
                    "|".join(convention.affected_paths),
                    convention.provenance.detail or convention.provenance.source,
                    convention.date,
                ),
            )

    # ---- reads ----

    def _load_references(self, project_id: str) -> List:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT source_file_id, target_text, kind, line, resolution FROM intelligence_references WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        return rows

    def _count_symbols(self, project_id: str) -> int:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM intelligence_symbols WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return row["total"]

    def _count_relationships(self, project_id: str) -> int:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS total FROM intelligence_references WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        return row["total"]

    def _read_text(self, root: Path, rel_path: str) -> str:
        try:
            raw = (root / rel_path).read_bytes()
        except (OSError, IsADirectoryError):
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return raw.decode("latin-1")
            except UnicodeDecodeError:
                return ""


def _initial_health(project_id: str, state: IndexHealthState, message: str) -> IndexHealth:
    return IndexHealth(project_id=project_id, state=state, message=message, updated_at=_now())


def _empty_diagnostics(storage) -> IndexDiagnostics:
    return IndexDiagnostics(
        indexed_files=0,
        excluded_files=0,
        parse_failures=0,
        unresolved_references=0,
        stale_files=0,
        symbols=0,
        relationships=0,
        storage_version=str(_STORAGE_VERSION_OF(storage)),
        storage_size_bytes=storage.size_bytes(),
        recent_errors=(),
    )


def _STORAGE_VERSION_OF(storage) -> int:
    return 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
