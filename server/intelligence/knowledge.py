"""Project knowledge: decisions, conventions, and memory registry.

These registries store facts entered by the project (ADR files, docs, config)
or by authorized users. Nothing is invented: conventions are only recorded
when backed by an explicit rule, lint config, or strong observed pattern with
an inspectable provenance.
"""

from __future__ import annotations

import hashlib
import re
from typing import List, Optional, Tuple

from .models import (
    ConventionRecord,
    ConventionSource,
    DecisionRecord,
    DecisionStatus,
    MemoryEntry,
    MemoryStatus,
    Provenance,
)

_ADR_TITLE = re.compile(r"^#\s+([0-9]+[\.\s].*)$", re.MULTILINE)
_ADR_STATUS = re.compile(r"^\*\s*Status\*?:\s*(.*)$", re.IGNORECASE)
_ADR_DATE = re.compile(r"^\*\s*Date\*?:\s*(.*)$", re.IGNORECASE)
_CONVENTION_KEY = re.compile(
    r"^\s*[\"\']?(indent|quote|quotes|tabs|semicolons|semi|tab-width|tabWidth|tabwidth|trailing-comma|line-length|max-line-length|print-width|printWidth|import-order|naming|case|single-quote|singleQuote|double-quote|doubleQuote)[\"\']?\s*:",
    re.IGNORECASE,
)


class KnowledgeService:
    def __init__(self, connection_factory) -> None:
        self._connection_factory = connection_factory

    def prepare(self) -> None:
        with self._connection_factory() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS intelligence_decisions (
                    decision_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    date TEXT,
                    owner TEXT,
                    context TEXT NOT NULL DEFAULT '',
                    decision TEXT NOT NULL DEFAULT '',
                    alternatives TEXT NOT NULL DEFAULT '',
                    consequences TEXT NOT NULL DEFAULT '',
                    affected_systems TEXT NOT NULL DEFAULT '',
                    affected_files TEXT NOT NULL DEFAULT '',
                    supersedes TEXT,
                    source_kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    detected_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_intel_decisions_project ON intelligence_decisions(project_id);

                CREATE TABLE IF NOT EXISTS intelligence_conventions (
                    convention_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    convention TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT 'project',
                    confidence TEXT NOT NULL,
                    authoritative INTEGER NOT NULL DEFAULT 0,
                    date TEXT,
                    exceptions TEXT NOT NULL DEFAULT '',
                    affected_languages TEXT NOT NULL DEFAULT '',
                    affected_paths TEXT NOT NULL DEFAULT '',
                    source_label TEXT NOT NULL,
                    detected_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_intel_conventions_project ON intelligence_conventions(project_id);

                CREATE TABLE IF NOT EXISTS intelligence_memory (
                    memory_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    type TEXT NOT NULL DEFAULT 'note',
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'proposed',
                    confidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expiration TEXT,
                    related_files TEXT NOT NULL DEFAULT '',
                    related_symbols TEXT NOT NULL DEFAULT '',
                    related_decisions TEXT NOT NULL DEFAULT '',
                    related_tasks TEXT NOT NULL DEFAULT '',
                    superseded_by TEXT,
                    source_kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    detected_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_intel_memory_project ON intelligence_memory(project_id);
                """
            )

    # ---- ADR / decisions ----

    def ingest_adr(self, project_id: str, rel_path: str, content: str, now: str) -> Optional[DecisionRecord]:
        if "adr" not in rel_path.lower() and "decisions" not in rel_path.lower():
            return None
        title_match = _ADR_TITLE.search(content[:2000])
        if not title_match:
            return None
        title = title_match.group(1).strip()
        status: DecisionStatus = "accepted"
        date = None
        for raw in content.splitlines()[:80]:
            status_match = _ADR_STATUS.match(raw.strip())
            if status_match:
                value = status_match.group(1).strip().lower()
                for candidate in ("accepted", "proposed", "rejected", "deprecated", "superseded"):
                    if value.startswith(candidate):
                        status = candidate
                        break
                continue
            date_match = _ADR_DATE.match(raw.strip())
            if date_match:
                date = date_match.group(1).strip()
                break
        decision_id = _id("decision", project_id, rel_path)
        record = DecisionRecord(
            decision_id=decision_id,
            project_id=project_id,
            title=title,
            status=status,
            date=date or now[:10],
            affected_files=(rel_path,),
            source=Provenance(
                kind="documentation",
                source="architectural decision record",
                detail=rel_path,
                detected_at=now,
            ),
        )
        self.upsert_decision(record, now)
        return record

    def upsert_decision(self, record: DecisionRecord, now: str) -> None:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO intelligence_decisions (
                    decision_id, project_id, title, status, date, owner, context,
                    decision, alternatives, consequences, affected_systems,
                    affected_files, supersedes, source_kind, source, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    status = excluded.status, owner = excluded.owner,
                    context = excluded.context, decision = excluded.decision,
                    alternatives = excluded.alternatives, consequences = excluded.consequences,
                    affected_files = excluded.affected_files
                """,
                (
                    record.decision_id, record.project_id, record.title, record.status,
                    record.date, record.owner, record.context, record.decision,
                    ",".join(record.alternatives), ",".join(record.consequences),
                    ",".join(record.affected_systems), ",".join(record.affected_files),
                    record.supersedes, record.source.kind, record.source.detail or record.source.source,
                    now,
                ),
            )

    def decisions(self, project_id: str) -> Tuple[DecisionRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM intelligence_decisions WHERE project_id = ? ORDER BY date DESC",
                (project_id,),
            ).fetchall()
        return tuple(_decision_from_row(row) for row in rows)

    # ---- Conventions ----

    def detect_conventions(self, project_id: str, file_meta: Tuple[Tuple[str, str], ...], now: str) -> Tuple[ConventionRecord, ...]:
        """Extract conventions from configuration files (lint/prettier/editorconfig)."""
        found: List[ConventionRecord] = []
        for rel_path, content in file_meta:
            lowered = rel_path.lower()
            if lowered.endswith(".prettierrc") or "prettier" in lowered and lowered.endswith((".json", ".yaml", ".yml", ".js", ".cjs", ".toml")):
                found.extend(self._from_prettier(project_id, rel_path, content, now))
            elif lowered.endswith(".editorconfig"):
                found.extend(self._from_editorconfig(project_id, rel_path, content, now))
            elif lowered.endswith((".eslintrc", ".eslintrc.json", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.yaml", ".eslintrc.yml")):
                found.extend(self._from_eslint(project_id, rel_path, content, now))
        return tuple(found)

    def _from_prettier(self, project_id: str, rel_path: str, content: str, now: str) -> List[ConventionRecord]:
        records: List[ConventionRecord] = []
        for line in content.splitlines():
            key_match = _CONVENTION_KEY.match(line)
            if not key_match:
                continue
            key = key_match.group(1).lower()
            value = re.sub(r"\s*[,}]\s*$", "", line.split(":", 1)[1]).strip().strip("\"'")
            if not value:
                continue
            records.append(
                ConventionRecord(
                    convention_id=_id("convention", project_id, rel_path, key),
                    project_id=project_id,
                    convention="%s: %s" % (key, value),
                    source_kind="lint_config",
                    confidence="reported",
                    authoritative=True,
                    date=now,
                    affected_languages=("javascript", "typescript"),
                    affected_paths=(rel_path,),
                    provenance=Provenance(kind="classification", source="prettier config", detail=rel_path, detected_at=now),
                )
            )
        return records

    def _from_editorconfig(self, project_id: str, rel_path: str, content: str, now: str) -> List[ConventionRecord]:
        records: List[ConventionRecord] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(";") or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip().lower()
            if key in {"indent_style", "indent_size", "tab_width", "max_line_length", "charset", "end_of_line", "trim_trailing_whitespace", "insert_final_newline"}:
                records.append(
                    ConventionRecord(
                        convention_id=_id("convention", project_id, rel_path, key),
                        project_id=project_id,
                        convention="%s: %s" % (key, value.strip()),
                        source_kind="explicit_rule",
                        confidence="reported",
                        authoritative=True,
                        date=now,
                        affected_paths=(rel_path,),
                        provenance=Provenance(kind="classification", source="editorconfig", detail=rel_path, detected_at=now),
                    )
                )
        return records

    def _from_eslint(self, project_id: str, rel_path: str, content: str, now: str) -> List[ConventionRecord]:
        records: List[ConventionRecord] = []
        rules_match = re.search(r'"rules"\s*:\s*\{', content)
        if not rules_match:
            return records
        for match in re.finditer(r'"([a-z@][\w/@-]*)"\s*:\s*\[?\s*"?\d*"?', content[rules_match.end():rules_match.end() + 3000]):
            records.append(
                ConventionRecord(
                    convention_id=_id("convention", project_id, rel_path, match.group(1)),
                    project_id=project_id,
                    convention="eslint rule %s enabled" % match.group(1),
                    source_kind="lint_config",
                    confidence="reported",
                    authoritative=True,
                    date=now,
                    affected_languages=("javascript", "typescript"),
                    affected_paths=(rel_path,),
                    provenance=Provenance(kind="classification", source="eslint config", detail=rel_path, detected_at=now),
                )
            )
        return records[:16]

    def conventions(self, project_id: str) -> Tuple[ConventionRecord, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM intelligence_conventions WHERE project_id = ? ORDER BY date DESC",
                (project_id,),
            ).fetchall()
        return tuple(_convention_from_row(row) for row in rows)

    # ---- Memory ----

    def add_memory(self, entry: MemoryEntry, now: str) -> MemoryEntry:
        with self._connection_factory() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO intelligence_memory (
                    memory_id, project_id, type, summary, status, confidence,
                    created_at, updated_at, expiration, related_files,
                    related_symbols, related_decisions, related_tasks,
                    superseded_by, source_kind, source, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.memory_id, entry.project_id, entry.type, entry.summary,
                    entry.status, entry.confidence, entry.created_at, entry.updated_at,
                    entry.expiration, ",".join(entry.related_files),
                    ",".join(entry.related_symbols), ",".join(entry.related_decisions),
                    ",".join(entry.related_tasks), entry.superseded_by,
                    entry.source.kind, entry.source.detail or entry.source.source, now,
                ),
            )
        return entry

    def update_memory_status(self, project_id: str, memory_id: str, status: MemoryStatus, now: str) -> bool:
        with self._connection_factory() as connection:
            cursor = connection.execute(
                "UPDATE intelligence_memory SET status = ?, updated_at = ? WHERE project_id = ? AND memory_id = ?",
                (status, now, project_id, memory_id),
            )
        return cursor.rowcount > 0

    def memories(self, project_id: str) -> Tuple[MemoryEntry, ...]:
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM intelligence_memory WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return tuple(_memory_from_row(row) for row in rows)


def _id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def _decision_from_row(row) -> DecisionRecord:
    return DecisionRecord(
        decision_id=row["decision_id"],
        project_id=row["project_id"],
        title=row["title"],
        status=row["status"],
        date=row["date"] or "",
        owner=row["owner"],
        context=row["context"],
        decision=row["decision"],
        alternatives=tuple(x for x in row["alternatives"].split(",") if x),
        consequences=tuple(x for x in row["consequences"].split(",") if x),
        affected_systems=tuple(x for x in row["affected_systems"].split(",") if x),
        affected_files=tuple(x for x in row["affected_files"].split(",") if x),
        supersedes=row["supersedes"],
        source=Provenance(kind=row["source_kind"], source=row["source"], detected_at=row["detected_at"]),
    )


def _convention_from_row(row) -> ConventionRecord:
    return ConventionRecord(
        convention_id=row["convention_id"],
        project_id=row["project_id"],
        convention=row["convention"],
        source_kind=row["source_kind"],
        scope=row["scope"],
        confidence=row["confidence"],
        authoritative=bool(row["authoritative"]),
        date=row["date"],
        exceptions=tuple(x for x in row["exceptions"].split(",") if x),
        affected_languages=tuple(x for x in row["affected_languages"].split(",") if x),
        affected_paths=tuple(x for x in row["affected_paths"].split(",") if x),
        provenance=Provenance(
            kind="classification",
            source=row["source_label"],
            detail=row["source_kind"],
            detected_at=row["detected_at"],
        ),
    )


def _memory_from_row(row) -> MemoryEntry:
    return MemoryEntry(
        memory_id=row["memory_id"],
        project_id=row["project_id"],
        type=row["type"],
        summary=row["summary"],
        status=row["status"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expiration=row["expiration"],
        related_files=tuple(x for x in row["related_files"].split(",") if x),
        related_symbols=tuple(x for x in row["related_symbols"].split(",") if x),
        related_decisions=tuple(x for x in row["related_decisions"].split(",") if x),
        related_tasks=tuple(x for x in row["related_tasks"].split(",") if x),
        superseded_by=row["superseded_by"],
        source=Provenance(kind=row["source_kind"], source=row["source"], detected_at=row["detected_at"]),
    )
