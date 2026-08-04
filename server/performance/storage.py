"""Versioned SQLite storage for the JoeOS Performance Platform.

Persists bounded metric history, benchmark records, performance budgets,
regressions, traces, leak indicators, cache registrations, and queue states.
Retention is applied so history never grows without bound. No private content
is ever stored: traces carry only safe metadata and the storage layer rejects
secrets/raw payloads by construction.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable, Optional

STORAGE_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS performance_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,
    sampling_method TEXT NOT NULL,
    unit TEXT NOT NULL,
    available INTEGER NOT NULL DEFAULT 1,
    uncertainty TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_perf_metrics_metric ON performance_metrics(metric);

CREATE TABLE IF NOT EXISTS performance_benchmarks (
    benchmark_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    subsystem TEXT NOT NULL,
    scenario TEXT NOT NULL,
    dataset TEXT NOT NULL DEFAULT '',
    fixture TEXT NOT NULL DEFAULT '',
    hardware_profile TEXT NOT NULL DEFAULT '',
    software_version TEXT NOT NULL DEFAULT '',
    warm INTEGER NOT NULL DEFAULT 0,
    iterations INTEGER NOT NULL DEFAULT 1,
    warmup INTEGER NOT NULL DEFAULT 0,
    measurement_method TEXT NOT NULL DEFAULT 'real',
    metric TEXT NOT NULL DEFAULT 'duration_ms',
    result REAL NOT NULL DEFAULT 0,
    median REAL NOT NULL DEFAULT 0,
    variance REAL NOT NULL DEFAULT 0,
    timestamp TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL DEFAULT '',
    artifact TEXT NOT NULL DEFAULT '',
    limitations TEXT NOT NULL DEFAULT '',
    budget_pass INTEGER
);

CREATE TABLE IF NOT EXISTS performance_budgets (
    budget_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    hardware_profile TEXT NOT NULL,
    metric TEXT NOT NULL,
    target REAL NOT NULL,
    warning_threshold REAL NOT NULL,
    failure_threshold REAL NOT NULL,
    measurement_method TEXT NOT NULL DEFAULT 'direct',
    owner TEXT NOT NULL DEFAULT 'performance',
    version INTEGER NOT NULL DEFAULT 1,
    exceptions TEXT NOT NULL DEFAULT '',
    review_date TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL DEFAULT 'lower_is_better'
);

CREATE TABLE IF NOT EXISTS performance_regressions (
    regression_id TEXT PRIMARY KEY,
    benchmark_id TEXT NOT NULL,
    baseline_commit TEXT NOT NULL,
    current_commit TEXT NOT NULL,
    baseline_median REAL NOT NULL,
    current_median REAL NOT NULL,
    variance REAL NOT NULL DEFAULT 0,
    confidence TEXT NOT NULL DEFAULT 'low',
    classification TEXT NOT NULL DEFAULT 'insufficient_samples',
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS performance_traces (
    trace_id TEXT NOT NULL,
    parent_trace_id TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL,
    operation TEXT NOT NULL,
    start_iso TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    queue_ms REAL NOT NULL DEFAULT 0,
    execution_ms REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok',
    cancelled INTEGER NOT NULL DEFAULT 0,
    safe_metadata TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (trace_id, parent_trace_id)
);

CREATE TABLE IF NOT EXISTS performance_leak_indicators (
    indicator_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    kind TEXT NOT NULL,
    baseline REAL NOT NULL DEFAULT 0,
    current REAL NOT NULL DEFAULT 0,
    growth_rate REAL NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'unknown',
    message TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS performance_caches (
    cache_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    purpose TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT 'project',
    max_entries INTEGER NOT NULL DEFAULT 256,
    max_bytes INTEGER NOT NULL DEFAULT 8388608,
    ttl_seconds REAL NOT NULL DEFAULT 300,
    privacy TEXT NOT NULL DEFAULT 'private',
    invalidation TEXT NOT NULL DEFAULT '',
    persistence TEXT NOT NULL DEFAULT 'memory',
    encryption TEXT NOT NULL DEFAULT 'none',
    sharing_policy TEXT NOT NULL DEFAULT 'never',
    failure_behavior TEXT NOT NULL DEFAULT 'recompute',
    security_sensitive INTEGER NOT NULL DEFAULT 0,
    entries INTEGER NOT NULL DEFAULT 0,
    bytes_used INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0,
    misses INTEGER NOT NULL DEFAULT 0,
    evictions INTEGER NOT NULL DEFAULT 0,
    last_cleanup TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS performance_queues (
    queue_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    workload_class TEXT NOT NULL DEFAULT 'maintenance',
    depth INTEGER NOT NULL DEFAULT 0,
    queue_limit INTEGER NOT NULL DEFAULT 0,
    oldest_wait_ms REAL NOT NULL DEFAULT 0,
    average_wait_ms REAL NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    cancelled INTEGER NOT NULL DEFAULT 0,
    throughput_per_minute REAL NOT NULL DEFAULT 0,
    backpressure TEXT NOT NULL DEFAULT 'none',
    preserved INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT ''
);
"""

DEFAULT_RETENTION = {
    "performance_metrics": 20000,
    "performance_traces": 20000,
    "performance_regressions": 1000,
    "performance_benchmarks": 2000,
}


class PerformanceStorage:
    """Owns the SQLite schema and provides bounded access helpers."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        *,
        retention: Optional[dict] = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._retention = dict(DEFAULT_RETENTION)
        if retention:
            self._retention.update(retention)
        self._lock = threading.RLock()
        self.prepare()

    def prepare(self) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO performance_meta (key, value) VALUES (?, ?)",
                ("performance_schema_version", str(STORAGE_VERSION)),
            )

    # ---- metric history ----

    def insert_metric(
        self,
        recorded_at: str,
        metric: str,
        value: float,
        source: str,
        sampling_method: str,
        unit: str,
        available: bool,
        uncertainty: str,
    ) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "INSERT INTO performance_metrics (recorded_at, metric, value, source, sampling_method, unit, available, uncertainty)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (recorded_at, metric[:120], value, source[:80], sampling_method[:80], unit[:20], 1 if available else 0, uncertainty[:200]),
            )
            self._prune(connection, "performance_metrics", self._retention["performance_metrics"])

    def latest_metric(self, metric: str) -> Optional[tuple]:
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT recorded_at, metric, value, source, sampling_method, unit, available, uncertainty"
                " FROM performance_metrics WHERE metric = ? ORDER BY id DESC LIMIT 1",
                (metric,),
            ).fetchone()
        return tuple(row) if row else None

    def metric_history(self, metric: str, limit: int = 100) -> list:
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT recorded_at, metric, value, source, sampling_method, unit, available, uncertainty"
                " FROM performance_metrics WHERE metric = ? ORDER BY id DESC LIMIT ?",
                (metric, max(1, min(2000, int(limit)))),
            ).fetchall()
        return [tuple(row) for row in rows]

    def metric_metrics(self) -> dict:
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT metric, COUNT(*), MIN(value), MAX(value), AVG(value) FROM performance_metrics GROUP BY metric"
            ).fetchall()
        return {str(row[0]): {"count": row[1], "min": row[2], "max": row[3], "mean": row[4]} for row in rows}

    # ---- benchmarks ----

    def upsert_benchmark(self, values: dict) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO performance_benchmarks (
                    benchmark_id, title, subsystem, scenario, dataset, fixture, hardware_profile,
                    software_version, warm, iterations, warmup, measurement_method, metric,
                    result, median, variance, timestamp, commit_sha, artifact, limitations, budget_pass
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(benchmark_id) DO UPDATE SET
                    title=excluded.title, subsystem=excluded.subsystem, scenario=excluded.scenario,
                    dataset=excluded.dataset, fixture=excluded.fixture, hardware_profile=excluded.hardware_profile,
                    software_version=excluded.software_version, warm=excluded.warm, iterations=excluded.iterations,
                    warmup=excluded.warmup, measurement_method=excluded.measurement_method, metric=excluded.metric,
                    result=excluded.result, median=excluded.median, variance=excluded.variance,
                    timestamp=excluded.timestamp, commit_sha=excluded.commit_sha, artifact=excluded.artifact,
                    limitations=excluded.limitations, budget_pass=excluded.budget_pass
                """,
                (
                    values["benchmark_id"], values["title"], values["subsystem"], values["scenario"],
                    values.get("dataset", ""), values.get("fixture", ""), values.get("hardware_profile", ""),
                    values.get("software_version", ""), 1 if values.get("warm") else 0,
                    values.get("iterations", 1), values.get("warmup", 0),
                    values.get("measurement_method", "real"), values.get("metric", "duration_ms"),
                    values.get("result", 0.0), values.get("median", 0.0), values.get("variance", 0.0),
                    values.get("timestamp", ""), values.get("commit_sha", ""), values.get("artifact", ""),
                    values.get("limitations", ""), values.get("budget_pass"),
                ),
            )

    def list_benchmarks(self, subsystem: str = "", limit: int = 200) -> list:
        with self._lock, self._connection_factory() as connection:
            if subsystem:
                rows = connection.execute(
                    "SELECT * FROM performance_benchmarks WHERE subsystem = ? ORDER BY timestamp DESC LIMIT ?",
                    (subsystem, max(1, min(2000, int(limit)))),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM performance_benchmarks ORDER BY timestamp DESC LIMIT ?",
                    (max(1, min(2000, int(limit))),),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_benchmark(self, benchmark_id: str) -> Optional[dict]:
        with self._lock, self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM performance_benchmarks WHERE benchmark_id = ?", (benchmark_id,)
            ).fetchone()
        return dict(row) if row else None

    # ---- budgets ----

    def upsert_budget(self, values: dict) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO performance_budgets (
                    budget_id, platform, hardware_profile, metric, target, warning_threshold,
                    failure_threshold, measurement_method, owner, version, exceptions, review_date, direction
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(budget_id) DO UPDATE SET
                    platform=excluded.platform, hardware_profile=excluded.hardware_profile,
                    metric=excluded.metric, target=excluded.target, warning_threshold=excluded.warning_threshold,
                    failure_threshold=excluded.failure_threshold, measurement_method=excluded.measurement_method,
                    owner=excluded.owner, version=excluded.version, exceptions=excluded.exceptions,
                    review_date=excluded.review_date, direction=excluded.direction
                """,
                (
                    values["budget_id"], values["platform"], values["hardware_profile"], values["metric"],
                    values["target"], values["warning_threshold"], values["failure_threshold"],
                    values.get("measurement_method", "direct"), values.get("owner", "performance"),
                    values.get("version", 1), values.get("exceptions", ""), values.get("review_date", ""),
                    values.get("direction", "lower_is_better"),
                ),
            )

    def list_budgets(self, platform: str = "", hardware_profile: str = "") -> list:
        sql = "SELECT * FROM performance_budgets WHERE 1=1"
        params = []
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        if hardware_profile:
            sql += " AND hardware_profile = ?"
            params.append(hardware_profile)
        sql += " ORDER BY budget_id"
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # ---- regressions ----

    def insert_regression(self, values: dict) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO performance_regressions (
                    regression_id, benchmark_id, baseline_commit, current_commit, baseline_median,
                    current_median, variance, confidence, classification, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["regression_id"], values["benchmark_id"], values["baseline_commit"],
                    values["current_commit"], values["baseline_median"], values["current_median"],
                    values.get("variance", 0.0), values.get("confidence", "low"),
                    values["classification"], values.get("created_at", ""),
                ),
            )
            self._prune(connection, "performance_regressions", self._retention["performance_regressions"], order_col="created_at", key_col="regression_id")

    def list_regressions(self, classification: str = "", limit: int = 200) -> list:
        sql = "SELECT * FROM performance_regressions"
        params = []
        if classification:
            sql += " WHERE classification = ?"
            params.append(classification)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(2000, int(limit))))
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # ---- traces ----

    def insert_trace(self, values: dict) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO performance_traces (
                    trace_id, parent_trace_id, service, operation, start_iso, duration_ms,
                    queue_ms, execution_ms, status, cancelled, safe_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    values["trace_id"], values.get("parent_trace_id", ""), values["service"],
                    values["operation"], values["start_iso"], values["duration_ms"],
                    values.get("queue_ms", 0.0), values.get("execution_ms", 0.0),
                    values.get("status", "ok"), 1 if values.get("cancelled") else 0,
                    values.get("safe_metadata", "{}"),
                ),
            )
            self._prune(connection, "performance_traces", self._retention["performance_traces"])

    def list_traces(self, service: str = "", operation: str = "", limit: int = 200) -> list:
        sql = "SELECT * FROM performance_traces WHERE 1=1"
        params = []
        if service:
            sql += " AND service = ?"
            params.append(service)
        if operation:
            sql += " AND operation = ?"
            params.append(operation)
        sql += " ORDER BY start_iso DESC LIMIT ?"
        params.append(max(1, min(2000, int(limit))))
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    # ---- leak indicators ----

    def upsert_leak_indicator(self, values: dict) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO performance_leak_indicators (
                    indicator_id, owner, kind, baseline, current, growth_rate, state, message, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(indicator_id) DO UPDATE SET
                    owner=excluded.owner, kind=excluded.kind, baseline=excluded.baseline,
                    current=excluded.current, growth_rate=excluded.growth_rate, state=excluded.state,
                    message=excluded.message, updated_at=excluded.updated_at
                """,
                (
                    values["indicator_id"], values["owner"], values["kind"], values["baseline"],
                    values["current"], values["growth_rate"], values["state"], values["message"],
                    values.get("updated_at", ""),
                ),
            )

    def list_leak_indicators(self, state: str = "") -> list:
        sql = "SELECT * FROM performance_leak_indicators"
        params = []
        if state:
            sql += " WHERE state = ?"
            params.append(state)
        sql += " ORDER BY updated_at DESC"
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def clear_leak_indicators(self) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute("DELETE FROM performance_leak_indicators")

    # ---- caches ----

    def upsert_cache(self, values: dict) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO performance_caches (
                    cache_id, owner, purpose, scope, max_entries, max_bytes, ttl_seconds, privacy,
                    invalidation, persistence, encryption, sharing_policy, failure_behavior,
                    security_sensitive, entries, bytes_used, hits, misses, evictions, last_cleanup
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_id) DO UPDATE SET
                    owner=excluded.owner, purpose=excluded.purpose, scope=excluded.scope,
                    max_entries=excluded.max_entries, max_bytes=excluded.max_bytes,
                    ttl_seconds=excluded.ttl_seconds, privacy=excluded.privacy, invalidation=excluded.invalidation,
                    persistence=excluded.persistence, encryption=excluded.encryption,
                    sharing_policy=excluded.sharing_policy, failure_behavior=excluded.failure_behavior,
                    security_sensitive=excluded.security_sensitive, entries=excluded.entries,
                    bytes_used=excluded.bytes_used, hits=excluded.hits, misses=excluded.misses,
                    evictions=excluded.evictions, last_cleanup=excluded.last_cleanup
                """,
                (
                    values["cache_id"], values["owner"], values["purpose"], values.get("scope", "project"),
                    values.get("max_entries", 256), values.get("max_bytes", 8 * 1024 * 1024),
                    values.get("ttl_seconds", 300.0), values.get("privacy", "private"),
                    ",".join(values.get("invalidation", ())), values.get("persistence", "memory"),
                    values.get("encryption", "none"), values.get("sharing_policy", "never"),
                    values.get("failure_behavior", "recompute"),
                    1 if values.get("security_sensitive") else 0,
                    values.get("entries", 0), values.get("bytes_used", 0),
                    values.get("hits", 0), values.get("misses", 0), values.get("evictions", 0),
                    values.get("last_cleanup", ""),
                ),
            )

    def list_caches(self) -> list:
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM performance_caches ORDER BY cache_id").fetchall()
        return [dict(row) for row in rows]

    def delete_cache(self, cache_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute("DELETE FROM performance_caches WHERE cache_id = ?", (cache_id,))

    # ---- queues ----

    def upsert_queue(self, values: dict) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO performance_queues (
                    queue_id, owner, workload_class, depth, queue_limit, oldest_wait_ms, average_wait_ms,
                    rejected, cancelled, throughput_per_minute, backpressure, preserved, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(queue_id) DO UPDATE SET
                    owner=excluded.owner, workload_class=excluded.workload_class, depth=excluded.depth,
                    queue_limit=excluded.queue_limit, oldest_wait_ms=excluded.oldest_wait_ms,
                    average_wait_ms=excluded.average_wait_ms, rejected=excluded.rejected,
                    cancelled=excluded.cancelled, throughput_per_minute=excluded.throughput_per_minute,
                    backpressure=excluded.backpressure, preserved=excluded.preserved, updated_at=excluded.updated_at
                """,
                (
                    values["queue_id"], values["owner"], values.get("workload_class", "maintenance"),
                    values.get("depth", 0), values.get("queue_limit", 0), values.get("oldest_wait_ms", 0.0),
                    values.get("average_wait_ms", 0.0), values.get("rejected", 0),
                    values.get("cancelled", 0), values.get("throughput_per_minute", 0.0),
                    values.get("backpressure", "none"), 1 if values.get("preserved") else 0,
                    values.get("updated_at", ""),
                ),
            )

    def list_queues(self) -> list:
        with self._lock, self._connection_factory() as connection:
            rows = connection.execute("SELECT * FROM performance_queues ORDER BY queue_id").fetchall()
        return [dict(row) for row in rows]

    def delete_queue(self, queue_id: str) -> None:
        with self._lock, self._connection_factory() as connection:
            connection.execute("DELETE FROM performance_queues WHERE queue_id = ?", (queue_id,))

    # ---- retention ----

    def _prune(self, connection: sqlite3.Connection, table: str, keep: int, *, order_col: str = "id", key_col: str = "id") -> None:
        connection.execute(
            f"DELETE FROM {table} WHERE {key_col} NOT IN (SELECT {key_col} FROM {table} ORDER BY {order_col} DESC LIMIT ?)",
            (keep,),
        )
