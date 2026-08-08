"""Roadmap ingestion: parse a strict roadmap YAML file into RoadmapEntry records.

Only YAML (no code execution). Unknown fields are rejected so a typo surfaces
as a validation error instead of silently changing campaign scope.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from pydantic import ValidationError

from .models import RoadmapEntry, RoadmapEnvelope

try:  # pragma: no cover - runtime dependency
    import yaml as _yaml

    _HAS_YAML = True
except Exception:  # pragma: no cover - fallback when pyyaml is absent
    _yaml = None  # type: ignore[assignment]
    _HAS_YAML = False


ROADMAP_SCHEMA_HINT = "ROADMAP_SCHEMA_V1"


def parse_roadmap_document(document: str) -> RoadmapEnvelope:
    """Parse a roadmap YAML document. Returns entries with normalized stage
    orders and any per-entry warnings."""
    warnings: List[str] = []
    if not _HAS_YAML:  # pragma: no cover - exercised only without pyyaml
        raise ValueError("PyYAML is required to parse roadmap documents.")
    raw = _yaml.safe_load(document)
    if not isinstance(raw, dict):
        raise ValueError("Roadmap document must be a mapping.")
    if raw.get("schema") not in (ROADMAP_SCHEMA_HINT, None):
        raise ValueError(
            "Roadmap document must declare schema: %s" % ROADMAP_SCHEMA_HINT)
    campaign_key = str(raw.get("campaign") or "").strip() or None
    entries_raw = raw.get("work_packages")
    if entries_raw is None:
        entries_raw = raw.get("packages")
    if not isinstance(entries_raw, list):
        raise ValueError("Roadmap document must contain a work_packages list.")
    entries: List[RoadmapEntry] = []
    for index, item in enumerate(entries_raw):
        if not isinstance(item, dict):
            warnings.append("entry %d: skipped (not a mapping)" % index)
            continue
        item = dict(item)
        try:
            entry = RoadmapEntry(
                key=str(item.get("key") or "").strip(),
                title=str(item.get("title") or "").strip(),
                description=str(item.get("description") or ""),
                owner_agent_key=str(item.get("owner_agent_key") or "").strip(),
                verifier_agent_key=str(item.get("verifier_agent_key") or "").strip() or None,
                review_agent_key=str(item.get("review_agent_key") or "").strip() or None,
                dependencies=tuple(
                    str(dep).strip() for dep in (item.get("dependencies") or []) if str(dep).strip()
                ),
                acceptance_criteria=tuple(
                    str(c).strip() for c in (item.get("acceptance_criteria") or []) if str(c).strip()
                ),
                roadmap_order=int(item.get("order", index)),
                priority=int(item.get("priority", 100)),
                risk=str(item.get("risk") or "low"),
                stage_order=tuple(str(s).strip() for s in (item.get("stage_order") or [])),
                enabled=bool(item.get("enabled", True)),
                source="roadmap_yaml",
            )
            from .state_machine import normalize_stage_order

            entry = entry.model_copy(update={"stage_order": normalize_stage_order(entry.stage_order)})
            entries.append(entry)
        except (ValidationError, ValueError, TypeError) as exc:
            warnings.append("entry %d (%s): skipped (%s)" % (
                index, item.get("key", "?"), exc))
    return RoadmapEnvelope(entries=tuple(entries), campaign_key=campaign_key,
                           warnings=tuple(warnings))
