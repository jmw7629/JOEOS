from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import (
    ConfigurationChange,
    ConfigurationGuideRequest,
    ConfigurationProposal,
    ConfigurationProposalEnvelope,
    WidgetDefinition,
    WidgetInstance,
    WidgetInstanceUpdate,
    WidgetSize,
    Workspace,
    WorkspaceEnvelope,
    WorkspaceTheme,
    WorkspaceUpdate,
)


CATALOG_VERSION = 1
DEFAULT_WORKSPACE_ID = "default"
MAX_SETTINGS_BYTES = 16_384
SECRET_NOTICE = (
    "JoeOS configuration guidance never asks for or stores secrets. "
    "Connect credentials only through an approved secrets workflow."
)


def _widget(
    widget_id: str,
    name: str,
    description: str,
    category: str,
    icon: str,
    state: str,
    default: Tuple[int, int],
    minimum: Tuple[int, int],
    maximum: Tuple[int, int],
    integration: Optional[str] = None,
) -> WidgetDefinition:
    return WidgetDefinition(
        id=widget_id,
        version=1,
        name=name,
        description=description,
        category=category,
        icon=icon,
        state=state,
        default_size=WidgetSize(columns=default[0], rows=default[1]),
        min_size=WidgetSize(columns=minimum[0], rows=minimum[1]),
        max_size=WidgetSize(columns=maximum[0], rows=maximum[1]),
        integration=integration,
    )


CATALOG: Tuple[WidgetDefinition, ...] = (
    _widget("mission.attention", "Attention Now", "Items requiring executive attention.", "mission_control", "fa-bell", "ready", (6, 2), (3, 1), (12, 6)),
    _widget("mission.ai_delegation", "AI Can Handle", "Work eligible for approval-gated delegation.", "mission_control", "fa-wand-magic-sparkles", "ready", (6, 2), (3, 1), (12, 6)),
    _widget("mission.blockers", "Blocked & At Risk", "Blocked, overdue, and at-risk work.", "mission_control", "fa-triangle-exclamation", "ready", (4, 2), (3, 1), (12, 6)),
    _widget("mission.next_action", "Work on Next", "The highest-value recommended next action.", "mission_control", "fa-compass", "ready", (4, 2), (3, 1), (12, 6)),
    _widget("mission.changes", "Since Last Visit", "Material changes since the workspace was last reviewed.", "mission_control", "fa-clock-rotate-left", "ready", (4, 2), (3, 1), (12, 6)),
    _widget("system.health", "VPS Health", "Live CPU, GPU, memory, disk, and model runtime health.", "operations", "fa-heart-pulse", "ready", (12, 2), (4, 1), (12, 6)),
    _widget("calendar.today", "Today's Calendar", "Agenda, meeting preparation, and schedule risk.", "productivity", "fa-calendar-days", "integration_required", (6, 3), (3, 2), (12, 8), "calendar"),
    _widget("email.unread", "Important Email", "Unread and priority email requiring attention.", "communications", "fa-envelope", "integration_required", (6, 3), (3, 2), (12, 8), "email"),
    _widget("weather.local", "Weather & Traffic", "Local weather and travel conditions.", "briefing", "fa-cloud-sun", "integration_required", (4, 2), (3, 1), (8, 4), "weather_traffic"),
    _widget("markets.summary", "Market Summary", "Executive market watch and material movements.", "briefing", "fa-chart-line", "integration_required", (4, 2), (3, 1), (12, 6), "market_data"),
    _widget("manufacturing.kpis", "Manufacturing KPIs", "Production, quality, inventory, and delivery metrics.", "operations", "fa-industry", "integration_required", (8, 3), (4, 2), (12, 8), "manufacturing_erp"),
    _widget("social.feeds", "Social Feeds", "Curated social channels and monitored conversations.", "communications", "fa-hashtag", "integration_required", (6, 4), (3, 2), (12, 10), "social_media"),
    _widget("documents.recent", "Recent Documents", "Recently changed and recommended documents.", "knowledge", "fa-file-lines", "integration_required", (6, 3), (3, 2), (12, 8), "document_index"),
    _widget("automations.running", "Running Automations", "Active workflows, approvals, and exceptions.", "automation", "fa-diagram-project", "integration_required", (6, 3), (3, 2), (12, 8), "automation_engine"),
    _widget("git.status", "Git Status", "Repository health, changes, checks, and deployments.", "development", "fa-code-branch", "integration_required", (6, 3), (3, 2), (12, 8), "git_repository"),
    _widget("terminal.session", "Secure Terminal", "Approval-gated command execution and output.", "development", "fa-terminal", "integration_required", (8, 4), (4, 2), (12, 10), "approval_runner"),
    _widget("voice.control", "Voice Control", "Voice commands, conversations, and meeting capture.", "interaction", "fa-microphone", "integration_required", (4, 2), (3, 1), (8, 5), "speech_stack"),
    _widget("vision.camera", "Vision", "Camera, screenshot, OCR, and visual understanding.", "interaction", "fa-eye", "integration_required", (6, 3), (3, 2), (12, 8), "vision_stack"),
    _widget("even_reality.g2", "Even Reality G2", "Private notifications, captions, and quick actions.", "devices", "fa-glasses", "integration_required", (4, 2), (3, 1), (8, 5), "even_reality_g2"),
)


DEFAULT_WIDGETS: Tuple[Tuple[str, str, int, int], ...] = (
    ("attention", "mission.attention", 6, 2),
    ("ai-delegation", "mission.ai_delegation", 6, 2),
    ("blockers", "mission.blockers", 4, 2),
    ("next-action", "mission.next_action", 4, 2),
    ("changes", "mission.changes", 4, 2),
    ("vps-health", "system.health", 12, 2),
)


ALIASES: Dict[str, Tuple[str, ...]] = {
    "mission.attention": ("attention", "requires my attention"),
    "mission.ai_delegation": ("ai can handle", "delegation", "delegate"),
    "mission.blockers": ("blockers", "blocked", "at risk", "overdue"),
    "mission.next_action": ("next action", "work on next", "recommendation"),
    "mission.changes": ("changes", "since last visit", "what changed"),
    "system.health": ("system health", "vps health", "server health", "metrics"),
    "calendar.today": ("calendar", "agenda", "meetings"),
    "email.unread": ("email", "inbox", "unread mail"),
    "weather.local": ("weather", "traffic"),
    "markets.summary": ("market", "stocks"),
    "manufacturing.kpis": ("manufacturing", "production kpi", "inventory", "quality"),
    "social.feeds": ("social", "social media", "feeds"),
    "documents.recent": ("documents", "recent files"),
    "automations.running": ("automations", "workflows"),
    "git.status": ("git", "repository", "repo status"),
    "terminal.session": ("terminal", "shell"),
    "voice.control": ("voice", "microphone"),
    "vision.camera": ("vision", "camera", "ocr"),
    "even_reality.g2": ("even reality", "g2 glasses", "glasses"),
}


class RevisionConflictError(RuntimeError):
    def __init__(self, current_revision: int):
        super().__init__("Workspace revision is stale.")
        self.current_revision = current_revision


class WorkspaceValidationError(ValueError):
    pass


class WorkspaceService:
    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        event_sink: Optional[Callable[[str, str, str], None]] = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._event_sink = event_sink
        self._catalog_by_key = {(item.id, item.version): item for item in CATALOG}

    def prepare(self) -> None:
        now = self._now()
        with self._connection_factory() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS widget_catalog (
                    widget_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    definition_json TEXT NOT NULL,
                    catalog_version INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(widget_id, version)
                );

                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    theme_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS workspace_widgets (
                    workspace_id TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    widget_id TEXT NOT NULL,
                    widget_version INTEGER NOT NULL,
                    position INTEGER NOT NULL CHECK(position >= 0),
                    columns INTEGER NOT NULL CHECK(columns BETWEEN 1 AND 12),
                    rows INTEGER NOT NULL CHECK(rows BETWEEN 1 AND 12),
                    visible INTEGER NOT NULL CHECK(visible IN (0, 1)),
                    settings_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(workspace_id, instance_id),
                    FOREIGN KEY(workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE,
                    FOREIGN KEY(widget_id, widget_version) REFERENCES widget_catalog(widget_id, version)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_workspace_widget_position
                ON workspace_widgets(workspace_id, position);
                """
            )
            connection.execute(
                "UPDATE widget_catalog SET catalog_version = 0 WHERE catalog_version = ?",
                (CATALOG_VERSION,),
            )
            for definition in CATALOG:
                connection.execute(
                    """
                    INSERT INTO widget_catalog(widget_id, version, definition_json, catalog_version, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(widget_id, version) DO UPDATE SET
                        definition_json = excluded.definition_json,
                        catalog_version = excluded.catalog_version,
                        updated_at = excluded.updated_at
                    """,
                    (
                        definition.id,
                        definition.version,
                        self._json(definition.model_dump()),
                        CATALOG_VERSION,
                        now,
                    ),
                )
            default_theme = WorkspaceTheme()
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO workspaces(id, name, revision, theme_json, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (DEFAULT_WORKSPACE_ID, "Mission Control", self._json(default_theme.model_dump()), now, now),
            )
            if cursor.rowcount:
                for position, (instance_id, widget_id, columns, rows) in enumerate(DEFAULT_WIDGETS):
                    connection.execute(
                        """
                        INSERT INTO workspace_widgets(
                            workspace_id, instance_id, widget_id, widget_version, position,
                            columns, rows, visible, settings_json, updated_at
                        ) VALUES (?, ?, ?, 1, ?, ?, ?, 1, '{}', ?)
                        """,
                        (DEFAULT_WORKSPACE_ID, instance_id, widget_id, position, columns, rows, now),
                    )

    def get_workspace(self) -> WorkspaceEnvelope:
        with self._connection_factory() as connection:
            return self._envelope(connection)

    def update_workspace(self, payload: WorkspaceUpdate) -> WorkspaceEnvelope:
        normalized = self._normalize_updates(payload.widgets)
        now = self._now()
        connection = self._connection_factory()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT name, revision FROM workspaces WHERE id = ?", (DEFAULT_WORKSPACE_ID,)
            ).fetchone()
            if current is None:
                raise WorkspaceValidationError("The default workspace has not been initialized.")
            current_revision = int(current["revision"])
            if payload.revision != current_revision:
                raise RevisionConflictError(current_revision)
            next_revision = current_revision + 1
            connection.execute(
                """
                UPDATE workspaces
                SET name = ?, revision = ?, theme_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    (payload.name or current["name"]).strip(),
                    next_revision,
                    self._json(payload.theme.model_dump()),
                    now,
                    DEFAULT_WORKSPACE_ID,
                ),
            )
            connection.execute("DELETE FROM workspace_widgets WHERE workspace_id = ?", (DEFAULT_WORKSPACE_ID,))
            for position, item in enumerate(normalized):
                settings_json = self._validated_settings(item.settings)
                connection.execute(
                    """
                    INSERT INTO workspace_widgets(
                        workspace_id, instance_id, widget_id, widget_version, position,
                        columns, rows, visible, settings_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        DEFAULT_WORKSPACE_ID,
                        item.instance_id,
                        item.widget_id,
                        item.widget_version,
                        position,
                        item.size.columns,
                        item.size.rows,
                        1 if item.visible else 0,
                        settings_json,
                        now,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        if self._event_sink:
            self._event_sink("success", "workspace", "Mission Control workspace revision %s saved." % next_revision)
        return self.get_workspace()

    def guide(self, request: ConfigurationGuideRequest) -> ConfigurationProposalEnvelope:
        current = self.get_workspace().workspace
        normalized_message = " ".join(request.message.lower().split())
        theme = current.theme.model_copy(deep=True)
        widgets = [
            WidgetInstanceUpdate(
                instance_id=item.instance_id,
                widget_id=item.widget_id,
                widget_version=item.widget_version,
                order=item.order,
                size=item.size.model_copy(deep=True),
                visible=item.visible,
                settings=dict(item.settings),
            )
            for item in current.widgets
        ]
        changes: List[ConfigurationChange] = []
        warnings: List[str] = []
        if self._looks_like_secret(normalized_message):
            warnings.append("Potential credential text was ignored. Rotate it if it was a real secret.")
        else:
            self._guide_theme(normalized_message, theme, changes)
            self._guide_widgets(normalized_message, widgets, changes)
        widgets = self._normalize_updates(
            [item.model_copy(update={"order": position}) for position, item in enumerate(widgets)]
        )
        requirements = sorted(
            {
                definition.integration
                for item in widgets
                if item.visible
                for definition in [self._catalog_by_key[(item.widget_id, item.widget_version)]]
                if definition.integration
            }
        )
        summary = (
            "%s proposed change%s ready for review."
            % (len(changes), "" if len(changes) == 1 else "s")
            if changes
            else "No supported customization change was detected; the current workspace was preserved."
        )
        proposal_fingerprint = self._json(
            {
                "revision": current.revision,
                "message": normalized_message if not warnings else "credential-text-ignored",
                "theme": theme.model_dump(),
                "widgets": [item.model_dump() for item in widgets],
            }
        )
        proposal_id = "cfg-" + hashlib.sha256(proposal_fingerprint.encode("utf-8")).hexdigest()[:16]
        return ConfigurationProposalEnvelope(
            proposal=ConfigurationProposal(
                proposal_id=proposal_id,
                based_on_revision=current.revision,
                summary=summary,
                theme=theme,
                widgets=widgets,
                changes=changes,
                integration_requirements=requirements,
                warnings=warnings,
                requires_confirmation=True,
                secret_notice=SECRET_NOTICE,
            )
        )

    def _envelope(self, connection: sqlite3.Connection) -> WorkspaceEnvelope:
        row = connection.execute(
            "SELECT * FROM workspaces WHERE id = ?", (DEFAULT_WORKSPACE_ID,)
        ).fetchone()
        if row is None:
            raise WorkspaceValidationError("The default workspace has not been initialized.")
        widget_rows = connection.execute(
            """
            SELECT * FROM workspace_widgets
            WHERE workspace_id = ? ORDER BY position ASC
            """,
            (DEFAULT_WORKSPACE_ID,),
        ).fetchall()
        catalog_rows = connection.execute(
            """
            SELECT definition_json FROM widget_catalog
            WHERE catalog_version = ? ORDER BY widget_id ASC
            """,
            (CATALOG_VERSION,),
        ).fetchall()
        widgets = [
            WidgetInstance(
                instance_id=item["instance_id"],
                widget_id=item["widget_id"],
                widget_version=item["widget_version"],
                order=item["position"],
                size=WidgetSize(columns=item["columns"], rows=item["rows"]),
                visible=bool(item["visible"]),
                settings=self._load_object(item["settings_json"]),
            )
            for item in widget_rows
        ]
        catalog = [WidgetDefinition.model_validate_json(item["definition_json"]) for item in catalog_rows]
        return WorkspaceEnvelope(
            workspace=Workspace(
                id=row["id"],
                name=row["name"],
                revision=row["revision"],
                theme=WorkspaceTheme.model_validate(self._load_object(row["theme_json"])),
                widgets=widgets,
            ),
            catalog=catalog,
            catalog_version=CATALOG_VERSION,
        )

    def _normalize_updates(self, items: Sequence[WidgetInstanceUpdate]) -> List[WidgetInstanceUpdate]:
        instance_ids = [item.instance_id for item in items]
        if len(instance_ids) != len(set(instance_ids)):
            raise WorkspaceValidationError("Widget instance IDs must be unique.")
        indexed = list(enumerate(items))
        if items and all(item.order is not None for item in items):
            indexed.sort(key=lambda pair: (int(pair[1].order or 0), pair[0]))
        normalized: List[WidgetInstanceUpdate] = []
        for position, (_, item) in enumerate(indexed):
            definition = self._catalog_by_key.get((item.widget_id, item.widget_version))
            if definition is None:
                raise WorkspaceValidationError(
                    "Unknown widget catalog entry %s version %s." % (item.widget_id, item.widget_version)
                )
            if not (
                definition.min_size.columns <= item.size.columns <= definition.max_size.columns
                and definition.min_size.rows <= item.size.rows <= definition.max_size.rows
            ):
                raise WorkspaceValidationError("Widget %s size is outside its catalog limits." % item.widget_id)
            self._validated_settings(item.settings)
            normalized.append(item.model_copy(update={"order": position}, deep=True))
        return normalized

    def _guide_theme(
        self,
        message: str,
        theme: WorkspaceTheme,
        changes: List[ConfigurationChange],
    ) -> None:
        def set_theme(field: str, value: object, description: str) -> None:
            if getattr(theme, field) != value:
                setattr(theme, field, value)
                changes.append(ConfigurationChange(kind="theme", action="set", field=field, description=description))

        larger_font = any(
            term in message
            for term in ("larger font", "font larger", "bigger font", "font bigger", "increase font", "text larger")
        ) or bool(re.search(r"\b(?:larger|bigger)\s+(?:[a-z]+\s+){0,2}font\b", message))
        smaller_font = any(
            term in message
            for term in ("smaller font", "font smaller", "decrease font", "text smaller")
        ) or bool(re.search(r"\bsmaller\s+(?:[a-z]+\s+){0,2}font\b", message))
        if larger_font:
            set_theme("font_scale", min(1.5, round(theme.font_scale + 0.1, 2)), "Increase the interface font scale.")
        if smaller_font:
            set_theme("font_scale", max(0.75, round(theme.font_scale - 0.1, 2)), "Decrease the interface font scale.")
        font_families = {
            "monospace font": "monospace",
            "mono font": "monospace",
            "rounded font": "rounded",
            "serif font": "serif",
            "system font": "system",
        }
        for term, family in font_families.items():
            if term in message:
                set_theme("font_family", family, "Use the %s interface font." % family)
                break
        density_terms = {
            "compact": "compact",
            "comfortable": "comfortable",
            "spacious": "spacious",
        }
        for term, value in density_terms.items():
            if term in message and ("density" in message or term != "comfortable"):
                set_theme("density", value, "Use %s dashboard density." % value)
                break
        color_match = re.search(
            r"(?:accent(?:\s+color)?|color\s+accent)\s+(#[0-9a-f]{6})\b",
            message,
            flags=re.IGNORECASE,
        )
        colors = {
            "cyan": "#31D7FF",
            "blue": "#5577FF",
            "purple": "#A879FF",
            "green": "#3DE3A4",
            "orange": "#FFB45C",
            "red": "#FF647C",
        }
        if color_match:
            set_theme("accent_hex", color_match.group(1).upper(), "Set the requested accent color.")
        else:
            for name, value in colors.items():
                if "%s accent" % name in message or "accent %s" % name in message:
                    set_theme("accent_hex", value, "Use %s as the accent color." % name)
                    break
        text_color_match = re.search(
            r"(?:text|font)\s+(?:color\s+)?(#[0-9a-f]{6})\b",
            message,
            flags=re.IGNORECASE,
        )
        if text_color_match:
            set_theme("text_hex", text_color_match.group(1).upper(), "Set the requested primary text color.")
        canvas_color_match = re.search(
            r"(?:canvas|background)\s+(?:color\s+)?(#[0-9a-f]{6})\b",
            message,
            flags=re.IGNORECASE,
        )
        if canvas_color_match:
            set_theme("canvas_hex", canvas_color_match.group(1).upper(), "Set the requested canvas color.")
        if any(term in message for term in ("square corners", "sharp corners", "less rounded")):
            set_theme("radius", 6, "Use sharper module corners.")
        elif any(term in message for term in ("very rounded", "more rounded")):
            set_theme("radius", 26, "Use more rounded module corners.")
        elif "rounded corners" in message:
            set_theme("radius", 20, "Use rounded module corners.")
        if any(term in message for term in ("more transparent", "lighter glass")):
            set_theme("glass_opacity", max(0.2, round(theme.glass_opacity - 0.1, 2)), "Make glass modules more transparent.")
        elif any(term in message for term in ("more opaque", "solid glass", "less transparent")):
            set_theme("glass_opacity", min(1.0, round(theme.glass_opacity + 0.1, 2)), "Make glass modules more opaque.")

    def _guide_widgets(
        self,
        message: str,
        widgets: List[WidgetInstanceUpdate],
        changes: List[ConfigurationChange],
    ) -> None:
        clauses = [part.strip() for part in re.split(r"[,;]|\b(?:and then|then|and)\b", message) if part.strip()]
        for clause in clauses:
            for widget_id, aliases in ALIASES.items():
                if not any(alias in clause for alias in aliases):
                    continue
                definition = self._catalog_by_key[(widget_id, 1)]
                item = next((candidate for candidate in widgets if candidate.widget_id == widget_id), None)
                hide = any(term in clause for term in ("hide", "remove", "disable"))
                show = any(term in clause for term in ("show", "add", "enable", "include", "pin"))
                if hide and item and item.visible:
                    item.visible = False
                    changes.append(ConfigurationChange(kind="widget", action="hide", widget_id=widget_id, description="Hide %s." % definition.name))
                elif show:
                    if item is None:
                        instance_id = self._unique_instance_id(widget_id, widgets)
                        item = WidgetInstanceUpdate(
                            instance_id=instance_id,
                            widget_id=widget_id,
                            widget_version=definition.version,
                            order=len(widgets),
                            size=definition.default_size.model_copy(deep=True),
                            visible=True,
                            settings={},
                        )
                        widgets.append(item)
                        changes.append(ConfigurationChange(kind="widget", action="add", widget_id=widget_id, description="Add %s." % definition.name))
                    elif not item.visible:
                        item.visible = True
                        changes.append(ConfigurationChange(kind="widget", action="show", widget_id=widget_id, description="Show %s." % definition.name))
                if item is None:
                    continue
                target_size: Optional[WidgetSize] = None
                if any(term in clause for term in ("full width", "wide", "larger", "large")):
                    target_size = WidgetSize(
                        columns=definition.max_size.columns if "full width" in clause else min(definition.max_size.columns, max(item.size.columns + 2, definition.default_size.columns)),
                        rows=min(definition.max_size.rows, max(item.size.rows + 1, definition.default_size.rows)),
                    )
                elif any(term in clause for term in ("smaller", "small")):
                    target_size = definition.min_size.model_copy(deep=True)
                if target_size and target_size != item.size:
                    item.size = target_size
                    changes.append(ConfigurationChange(kind="widget", action="resize", widget_id=widget_id, description="Resize %s." % definition.name))
                if any(term in clause for term in ("first", "to the top")) and widgets[0] is not item:
                    widgets.remove(item)
                    widgets.insert(0, item)
                    changes.append(ConfigurationChange(kind="widget", action="reorder", widget_id=widget_id, description="Move %s first." % definition.name))
                elif any(term in clause for term in ("last", "to the bottom")) and widgets[-1] is not item:
                    widgets.remove(item)
                    widgets.append(item)
                    changes.append(ConfigurationChange(kind="widget", action="reorder", widget_id=widget_id, description="Move %s last." % definition.name))

    @staticmethod
    def _unique_instance_id(widget_id: str, widgets: Iterable[WidgetInstanceUpdate]) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", widget_id.lower()).strip("-") or "widget"
        existing = {item.instance_id for item in widgets}
        if base not in existing:
            return base
        suffix = 2
        while "%s-%s" % (base, suffix) in existing:
            suffix += 1
        return "%s-%s" % (base, suffix)

    @staticmethod
    def _looks_like_secret(message: str) -> bool:
        secret_terms = ("api key", "service role", "private key", "password", "access token", "secret key")
        secret_shapes = (
            r"github_pat_[a-z0-9_]{20,}",
            r"(?:sk|rk)_(?:live|test)_[a-z0-9]{20,}",
            r"eyj[a-z0-9_-]+\.[a-z0-9_-]+\.[a-z0-9_-]+",
        )
        return any(term in message for term in secret_terms) or any(re.search(pattern, message) for pattern in secret_shapes)

    @staticmethod
    def _validated_settings(settings: Dict[str, object]) -> str:
        try:
            encoded = json.dumps(settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise WorkspaceValidationError("Widget settings must be JSON serializable.") from exc
        if len(encoded.encode("utf-8")) > MAX_SETTINGS_BYTES:
            raise WorkspaceValidationError("Widget settings exceed the 16 KiB limit.")
        return encoded

    @staticmethod
    def _load_object(value: str) -> Dict[str, object]:
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise WorkspaceValidationError("Stored workspace JSON is invalid.") from exc
        if not isinstance(parsed, dict):
            raise WorkspaceValidationError("Stored workspace JSON must be an object.")
        return parsed

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
