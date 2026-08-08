#!/usr/bin/env python3
"""Activate the JOEOS autonomous engineering campaign.

A CLI that (1) seeds the eight engineering role profiles through the
authoritative agent registry, (2) creates the `joeos-autonomous-build`
campaign when absent, (3) imports the roadmap document, and (4) optionally
starts the campaign. Runs against the live JoeOS database; idempotent.

Usage:
    python scripts/activate_campaign.py [--db PATH] [--start] [--list]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

DEFAULT_DB = BASE_DIR / "data" / "joeos.db"
ROADMAP = BASE_DIR / "docs" / "roadmap" / "joeos-autonomous-build.roadmap.yaml"
CAMPAIGN_KEY = "joeos-autonomous-build"

CAPABILITIES = [
    "agent.manage",
    "agent.read",
    "engineering.campaign.read",
    "engineering.campaign.manage",
    "engineering.campaign.start",
    "engineering.campaign.pause",
    "engineering.campaign.cancel",
    "engineering.package.read",
    "engineering.package.manage",
    "engineering.blocker.resolve",
]


def connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def owner_principal(db_path: Path) -> dict:
    """Build an owner-scoped principal from the live authority store."""
    from server.identity.authority_repository import SQLiteAuthorityRepository

    repository = SQLiteAuthorityRepository(lambda: connect(db_path))
    repository.prepare()
    user = repository.list_users()[0]
    organization = repository.list_organizations()[0]
    workspace = repository.list_workspaces()[0]
    return {
        "session_id": None,
        "device_id": None,
        "user": {"id": str(user.id), "display_name": user.display_name},
        "organization": {"id": str(organization.id)},
        "workspace": {"id": str(workspace.id), "name": workspace.name},
        "roles": ["joeos.owner"],
        "capabilities": list(CAPABILITIES),
    }


def seed_roles(db_path: Path) -> list:
    from server.actions.repository import SQLiteControlStore
    from server.actions.service import ActionService
    from server.engineering.campaign.roles import seed_engineering_agents

    service = ActionService(SQLiteControlStore(lambda: connect(db_path)))
    service.prepare()
    principal = owner_principal(db_path)
    return seed_engineering_agents(service, principal)


def create_and_import(db_path: Path, *, start: bool) -> dict:
    from server.engineering.campaign import (
        CampaignDefinition,
        CampaignService,
        CampaignStore,
        WorkPackageDefinition,
    )
    from server.engineering.campaign.roadmap import parse_roadmap_document

    store = CampaignStore(lambda: connect(db_path))
    service = CampaignService(store)
    service.prepare()
    principal = owner_principal(db_path)

    campaign = store.get_campaign_by_key(CAMPAIGN_KEY)
    if campaign is None:
        campaign = service.create_campaign(principal, CampaignDefinition(
            key=CAMPAIGN_KEY,
            title="JOEOS autonomous build campaign",
            description="Durable autonomous engineering over the existing agent fabric.",
            repository_path=str(BASE_DIR),
            base_branch="ai-rebuild",
            integration_branch="ai-rebuild",
            autonomy_policy_key="joeos.engineering.ai-rebuild.v1",
            worktree_root=str(BASE_DIR / "data" / "campaign-worktrees"),
            max_parallel_packages=1,
            max_attempts_per_package=3,
            heartbeat_timeout_ms=300_000,
        ))
        created = True
    else:
        created = False

    document = ROADMAP.read_text(encoding="utf-8")
    parsed = parse_roadmap_document(document)
    imported = service.import_roadmap(principal, campaign.campaign_id, list(parsed.entries))

    existing = {p.key for p in service.list_work_packages(principal, campaign.campaign_id)}
    materialized = 0
    for entry in imported.entries:
        if entry.key in existing:
            continue
        service.create_work_package(principal, campaign.campaign_id, WorkPackageDefinition(
            key=entry.key, title=entry.title,
            description=entry.description or "",
            acceptance_criteria=tuple(entry.acceptance_criteria),
            owner_agent_key=entry.owner_agent_key,
            verifier_agent_key=entry.verifier_agent_key,
            review_agent_key=entry.review_agent_key,
            dependencies=tuple(entry.dependencies),
            roadmap_order=entry.roadmap_order,
            priority=entry.priority,
            risk=entry.risk,
            stage_order=tuple(entry.stage_order),
        ))
        materialized += 1

    state = campaign.state
    if start and state != "active":
        service.start_campaign(principal, campaign.campaign_id)
        state = "active"

    return {
        "campaign_created": created,
        "campaign_id": campaign.campaign_id,
        "campaign_key": campaign.key,
        "state": state,
        "roadmap_entries": len(imported.entries),
        "roadmap_warnings": list(imported.warnings),
        "work_packages_materialized": materialized,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="activate-campaign")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--start", action="store_true", help="start the campaign if not active")
    parser.add_argument("--list", action="store_true", help="list campaigns and exit")
    arguments = parser.parse_args()

    db_path = Path(arguments.db).expanduser().resolve()
    if not db_path.is_file():
        print(json.dumps({"ok": False, "code": "database_not_found", "db": str(db_path)}))
        return 2

    try:
        if arguments.list:
            from server.engineering.campaign import CampaignService, CampaignStore

            service = CampaignService(CampaignStore(lambda: connect(db_path)))
            service.prepare()
            campaigns = [c.model_dump() for c in service.list_campaigns(owner_principal(db_path))]
            print(json.dumps({"ok": True, "campaigns": campaigns}, default=str))
            return 0
        roles = seed_roles(db_path)
        summary = create_and_import(db_path, start=arguments.start)
        summary["roles_created"] = [r["key"] for r in roles]
        print(json.dumps({"ok": True, **summary}, default=str))
        return 0
    except Exception as error:  # noqa: BLE001
        print(json.dumps({"ok": False, "code": type(error).__name__, "message": str(error)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
