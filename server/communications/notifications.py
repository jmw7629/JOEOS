"""Notification Center for the JoeOS Communications Platform.

One authoritative notification store with routing rules, quiet hours and DND,
snooze, digests, and bounded unread counts. Notifications never contain
secrets or full private content.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .models import DigestRecord, NotificationRecord, NotificationRule, QuietHours

NON_SUPPRESSIBLE = {"security_alert", "approval_request", "secret_exposure"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotificationError(RuntimeError):
    pass


class NotificationCenter:
    def __init__(self, connection_factory: Callable[[], sqlite3.Connection]) -> None:
        self._connection_factory = connection_factory
        self._lock = threading.RLock()
        self._quiet_hours: QuietHours = QuietHours()
        self._dnd = False

    # ---- creation ----

    def create(
        self,
        *,
        source: str,
        category: str,
        title: str,
        message: str = "",
        severity: str = "informational",
        priority: str = "normal",
        urgency: str = "routine",
        project: str = "",
        mission: str = "",
        task: str = "",
        workflow: str = "",
        plugin: str = "",
        deduplication_key: str = "",
        grouping_key: str = "",
        source_type: str = "",
    ) -> NotificationRecord:
        notification_id = "notif_" + uuid.uuid4().hex[:16]
        now = _now()
        if deduplication_key and self._has_dedup(deduplication_key):
            raise NotificationError("duplicate notification suppressed by deduplication key.")
        # A notification is never suppressed into nothing; DND only affects
        # interruption channels, never inbox persistence.
        channels = self._route_channels(category, severity, priority, urgency)
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_notifications (
                    notification_id, source, source_type, category, title, message,
                    severity, priority, urgency, privacy, project, mission, task, workflow,
                    plugin, action_links, created_at, updated_at, delivery_channels,
                    delivery_state, read_state, archive_state, mute_state, snooze_until,
                    deduplication_key, grouping_key, escalation_policy, trace_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'private', ?, ?, ?, ?, ?, '', ?, ?, ?, 'created', 'delivered', 0, 0, '', ?, ?, '', '')
                """,
                (
                    notification_id,
                    source,
                    source_type,
                    category,
                    title,
                    message[:500],
                    severity,
                    priority,
                    urgency,
                    project,
                    mission,
                    task,
                    workflow,
                    plugin,
                    now,
                    now,
                    json.dumps(channels),
                    deduplication_key,
                    grouping_key,
                ),
            )
        return self.get(notification_id)

    def _has_dedup(self, dedup_key: str) -> bool:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT notification_id FROM comms_notifications WHERE deduplication_key = ? LIMIT 1",
                (dedup_key,),
            ).fetchone()
        return row is not None

    def get(self, notification_id: str) -> Optional[NotificationRecord]:
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT * FROM comms_notifications WHERE notification_id = ?", (notification_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(
        self,
        *,
        category: Optional[str] = None,
        source: Optional[str] = None,
        severity: Optional[str] = None,
        read_state: Optional[str] = None,
        limit: int = 50,
        include_archived: bool = False,
    ) -> Tuple[NotificationRecord, ...]:
        count = max(1, min(200, int(limit)))
        clauses: List[str] = []
        params: List[object] = []
        if not include_archived:
            clauses.append("archive_state = 0")
        if category:
            clauses.append("category = ?")
            params.append(category)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if read_state:
            clauses.append("read_state = ?")
            params.append(read_state)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(count)
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM comms_notifications" + where + " ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def mark_read(self, notification_id: str, state: str = "read") -> NotificationRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_notifications SET read_state = ?, updated_at = ? WHERE notification_id = ?",
                (state, _now(), notification_id),
            )
        return self.get(notification_id)

    def acknowledge(self, notification_id: str) -> NotificationRecord:
        return self.mark_read(notification_id, "acknowledged")

    def set_archive(self, notification_id: str, archived: bool) -> NotificationRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_notifications SET archive_state = ?, updated_at = ? WHERE notification_id = ?",
                (1 if archived else 0, _now(), notification_id),
            )
        return self.get(notification_id)

    def set_snooze(self, notification_id: str, until: str) -> NotificationRecord:
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "UPDATE comms_notifications SET snooze_until = ?, updated_at = ? WHERE notification_id = ?",
                (until, _now(), notification_id),
            )
        return self.get(notification_id)

    def unread_count(self, *, category: Optional[str] = None) -> int:
        clauses = "archive_state = 0 AND read_state = 'delivered'"
        params: List[object] = []
        if category:
            clauses += " AND category = ?"
            params.append(category)
        with self._connection_factory() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM comms_notifications WHERE " + clauses, params
            ).fetchone()
        return min(int(row[0]), 999)

    # ---- routing / quiet hours / DND ----

    def set_quiet_hours(self, quiet_hours: QuietHours) -> None:
        self._quiet_hours = quiet_hours
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                "INSERT INTO comms_quiet_hours (id, payload) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (quiet_hours.model_dump_json(),),
            )

    def quiet_hours_config(self) -> QuietHours:
        with self._connection_factory() as connection:
            row = connection.execute("SELECT payload FROM comms_quiet_hours WHERE id = 1").fetchone()
        if row:
            return QuietHours.model_validate(json.loads(str(row["payload"])))
        return self._quiet_hours

    def set_dnd(self, active: bool) -> None:
        self._dnd = bool(active)

    def dnd_active(self) -> bool:
        return self._dnd

    def quiet_hours_active(self, *, now=None) -> bool:
        config = self.quiet_hours_config()
        if not config.enabled:
            return False
        now = now or datetime.now(timezone.utc)
        try:
            from zoneinfo import ZoneInfo
            local = now.astimezone(ZoneInfo(config.timezone))
        except Exception:
            local = now
        weekday = local.weekday()
        is_weekend = weekday >= 5
        start = config.weekend_start if is_weekend else config.weekday_start
        end = config.weekend_end if is_weekend else config.weekday_end
        current = local.strftime("%H:%M")
        return _in_window(current, start, end)

    def _route_channels(self, category: str, severity: str, priority: str, urgency: str) -> Tuple[str, ...]:
        channels = ["inbox"]
        if self._dnd or self.quiet_hours_active():
            critical = severity in {"critical", "security_critical"} and self._quiet_hours.critical_exceptions
            security = category in NON_SUPPRESSIBLE and self._quiet_hours.security_exceptions
            if not (critical or security):
                return ("inbox",)  # persist but suppress interruption
            channels.append("banner")
        else:
            if urgency == "immediate" or severity in {"critical", "security_critical"}:
                channels.append("banner")
            if severity in {"warning", "error", "critical", "security_critical"}:
                channels.append("toast")
        if urgency == "digest_only" and len(channels) == 1:
            return ("digest",)
        return tuple(channels)

    # ---- digests ----

    def build_digest(self, *, window_hours: int = 24, method: str = "structured") -> DigestRecord:
        now = _now()
        import datetime as _dt
        window_start = (_dt.datetime.now(timezone.utc) - _dt.timedelta(hours=window_hours)).isoformat()
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                SELECT notification_id, category, severity, read_state, title
                FROM comms_notifications WHERE created_at >= ? ORDER BY created_at
                """,
                (window_start,),
            ).fetchall()
        important = [str(r["notification_id"]) for r in rows if r["severity"] in {"warning", "error", "critical", "security_critical"}]
        unresolved = [str(r["notification_id"]) for r in rows if r["read_state"] == "delivered"]
        failures = [str(r["notification_id"]) for r in rows if r["category"] in {"failed_delivery", "build_failed", "workflow_failed"}]
        approvals = [str(r["notification_id"]) for r in rows if r["category"] in {"approval_request", "approval_required"}]
        categories = tuple(sorted({str(r["category"]) for r in rows if r["category"]}))
        digest = DigestRecord(
            digest_id="digest_" + uuid.uuid4().hex[:16],
            time_window_start=window_start,
            time_window_end=now,
            source_categories=categories,
            important_items=tuple(important[-50:]),
            unresolved_items=tuple(unresolved[-50:]),
            failures=tuple(failures[-50:]),
            approvals=tuple(approvals[-50:]),
            generation_method=method,
            privacy="private",
            created_at=now,
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_digests (
                    digest_id, time_window_start, time_window_end, source_categories,
                    important_items, unresolved_items, failures, approvals,
                    generation_method, privacy, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest.digest_id,
                    digest.time_window_start,
                    digest.time_window_end,
                    json.dumps(list(digest.source_categories)),
                    json.dumps(list(digest.important_items)),
                    json.dumps(list(digest.unresolved_items)),
                    json.dumps(list(digest.failures)),
                    json.dumps(list(digest.approvals)),
                    digest.generation_method,
                    digest.privacy,
                    digest.created_at,
                ),
            )
        return digest

    # ---- rules ----

    def add_rule(self, *, source: str = "", category: str = "", severity: str = "", action: str = "deliver", channel: str = "", priority: int = 50) -> NotificationRule:
        rule = NotificationRule(
            rule_id="rule_" + uuid.uuid4().hex[:16],
            source=source,
            category=category,
            severity=severity or None,
            action=action,
            channel=channel,
            priority=priority,
            created_at=_now(),
        )
        with self._lock, self._connection_factory() as connection:
            connection.execute(
                """
                INSERT INTO comms_notification_rules (
                    rule_id, source, category, severity, action, channel, priority, enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (rule.rule_id, source, category, severity, action, channel, priority, _now()),
            )
        return rule

    def evaluate_rules(self, *, source: str, category: str, severity: str) -> str:
        """Return the effective action for a notification: deliver/digest/suppress."""
        if category in NON_SUPPRESSIBLE:
            return "deliver"
        with self._connection_factory() as connection:
            rows = connection.execute(
                "SELECT * FROM comms_notification_rules WHERE enabled = 1 ORDER BY priority DESC"
            ).fetchall()
        for row in rows:
            if str(row["source"]) and str(row["source"]) != source:
                continue
            if str(row["category"]) and str(row["category"]) != category:
                continue
            if str(row["severity"]) and str(row["severity"]) != severity:
                continue
            return str(row["action"])
        return "deliver"

    @staticmethod
    def _row(row: sqlite3.Row) -> NotificationRecord:
        try:
            channels = tuple(json.loads(str(row["delivery_channels"]))) if str(row["delivery_channels"]) else ()
        except ValueError:
            channels = ()
        return NotificationRecord(
            notification_id=str(row["notification_id"]),
            source=str(row["source"]),
            source_type=str(row["source_type"]),
            category=str(row["category"]),
            title=str(row["title"]),
            message=str(row["message"]),
            severity=str(row["severity"]),
            priority=str(row["priority"]),
            urgency=str(row["urgency"]),
            privacy=str(row["privacy"]),
            project=str(row["project"]),
            mission=str(row["mission"]),
            task=str(row["task"]),
            workflow=str(row["workflow"]),
            plugin=str(row["plugin"]),
            service=str(row["service"]),
            related_entity=str(row["related_entity"]),
            action_links=tuple(p for p in str(row["action_links"]).split("\n") if p),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            expiration=str(row["expiration"]),
            delivery_channels=channels,
            delivery_state=str(row["delivery_state"]),
            read_state=str(row["read_state"]),
            archive_state=bool(row["archive_state"]),
            mute_state=bool(row["mute_state"]),
            snooze_until=str(row["snooze_until"]),
            deduplication_key=str(row["deduplication_key"]),
            grouping_key=str(row["grouping_key"]),
            escalation_policy=str(row["escalation_policy"]),
            trace_id=str(row["trace_id"]),
        )


def _in_window(current: str, start: str, end: str) -> bool:
    def _minutes(value: str) -> int:
        hour, minute = (int(part) for part in value.split(":"))
        return hour * 60 + minute

    current_min = _minutes(current)
    start_min = _minutes(start)
    end_min = _minutes(end)
    if start_min <= end_min:
        return start_min <= current_min < end_min
    # Overnight window.
    return current_min >= start_min or current_min < end_min