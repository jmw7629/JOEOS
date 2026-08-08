"""Phase P3G canary: full campaign activation flow against a real scratch
database. Bootstraps authority, seeds the eight engineering roles through the
authoritative ActionService registry, creates the `joeos-autonomous-build`
campaign, imports the production roadmap YAML, and starts it. Mirrors
scripts/activate_campaign.py but keeps state on a temporary SQLite file."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.actions.repository import SQLiteControlStore
from server.actions.service import ActionService
from server.engineering.campaign import (
    CampaignDefinition,
    CampaignService,
    CampaignStore,
)
from server.engineering.campaign.roles import AGENT_ROLES, seed_engineering_agents
from server.identity.authority_repository import SQLiteAuthorityRepository
from server.identity.authority_service import AuthorityService
from server.identity.key_protection import PairingKeyProtector
from server.identity.repository import SQLiteDeviceIdentityRepository

BASE_DIR = Path(__file__).resolve().parent.parent
ROADMAP = BASE_DIR / "docs" / "roadmap" / "joeos-autonomous-build.roadmap.yaml"

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


class ActivationFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "joeos.db"

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            return connection

        self.connect = connect
        self.authority = AuthorityService(
            SQLiteAuthorityRepository(connect),
            SQLiteDeviceIdentityRepository(connect, PairingKeyProtector(b"k" * 32)),
        )
        self.authority.prepare()
        if not self.authority.is_bootstrapped():
            self.authority.bootstrap(
                display_name="Owner",
                organization_name="JoeOS",
                workspace_name="home",
            )
        self.actions = ActionService(SQLiteControlStore(connect))
        self.actions.prepare()
        self.campaigns = CampaignService(CampaignStore(connect))
        self.campaigns.prepare()
        self.principal = self._principal()

    def tearDown(self):
        self.tempdir.cleanup()

    def _principal(self):
        records = self.authority.list_users()
        org = self.authority.list_organizations()[0]
        workspace = self.authority.list_workspaces()[0]
        return {
            "session_id": None,
            "device_id": None,
            "user": {"id": str(records[0]["id"]), "display_name": records[0]["display_name"]},
            "organization": {"id": str(org["id"])},
            "workspace": {"id": str(workspace["id"]), "name": workspace["name"]},
            "roles": ["joeos.owner"],
            "capabilities": list(CAPABILITIES),
        }


class ActivationCanaryTests(ActivationFixture):
    def test_seed_engineering_agents_is_idempotent(self):
        first = seed_engineering_agents(self.actions, self.principal)
        second = seed_engineering_agents(self.actions, self.principal)
        self.assertEqual(len(first), len(AGENT_ROLES))
        self.assertEqual(second, [])
        self.assertEqual(sorted(r["key"] for r in first), sorted(p["key"] for p in AGENT_ROLES))

    def test_full_activation_flow(self):
        seed_engineering_agents(self.actions, self.principal)
        campaign = self.campaigns.create_campaign(self.principal, CampaignDefinition(
            key="joeos-autonomous-build",
            title="JOEOS autonomous build campaign",
            description="canary activation",
            repository_path=str(BASE_DIR),
            base_branch="ai-rebuild",
            integration_branch="ai-rebuild",
            autonomy_policy_key="joeos.engineering.ai-rebuild.v1",
            worktree_root=str(BASE_DIR / "data" / "campaign-worktrees"),
            max_parallel_packages=1,
            max_attempts_per_package=3,
            heartbeat_timeout_ms=300_000,
        ))
        self.assertEqual(campaign.state, "proposed")

        from server.engineering.campaign.roadmap import parse_roadmap_document

        document = ROADMAP.read_text(encoding="utf-8")
        parsed = parse_roadmap_document(document)
        self.assertGreaterEqual(len(parsed.entries), 6, "roadmap must declare work packages")
        imported = self.campaigns.import_roadmap(
            self.principal, campaign.campaign_id, list(parsed.entries))
        self.assertGreaterEqual(len(imported.entries), 6)
        stored = self.campaigns.roadmap(self.principal, campaign.campaign_id)
        self.assertGreaterEqual(len(stored.entries), 6)

        from server.engineering.campaign import WorkPackageDefinition

        for entry in stored.entries:
            self.campaigns.create_work_package(self.principal, campaign.campaign_id,
                                               WorkPackageDefinition(
                                                   key=entry.key, title=entry.title,
                                                   description=entry.description or "",
                                                   owner_agent_key="engineering.builder",
                                                   stage_order=tuple(entry.stage_order),
                                                   dependencies=tuple(entry.dependencies)))

        self.campaigns.start_campaign(self.principal, campaign.campaign_id)
        refreshed = self.campaigns.get_campaign(self.principal, campaign.campaign_id)
        self.assertEqual(refreshed.state, "active")

        packages = self.campaigns.list_work_packages(self.principal, campaign.campaign_id)
        self.assertGreaterEqual(len(packages), 6)

    def test_roadmap_yaml_schema_parse(self):
        from server.engineering.campaign.roadmap import parse_roadmap_document

        document = ROADMAP.read_text(encoding="utf-8")
        parsed = parse_roadmap_document(document)
        self.assertEqual(parsed.schema_version, 1)
        keys = [e.key for e in parsed.entries]
        self.assertEqual(len(keys), len(set(keys)), "work package keys must be unique")


if __name__ == "__main__":
    unittest.main()
