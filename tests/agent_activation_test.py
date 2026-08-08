"""Agent Fabric activation, execution, delegation, and task-graph tests.

Covers the authoritative control-plane path with a substitute executor so the
suite runs offline while still exercising the real state machine, persistence,
delegation bounds, task dependencies, and model binding."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4

from server.actions.repository import SQLiteControlStore
from server.actions.service import ActionDeniedError, ActionService
from server.agents.activation import (
    AGENT_DEFINITIONS,
    SAFE_TOOL_DEFINITIONS,
    activate_agent_fabric,
)

CAPS = [
    "agent.read", "agent.manage", "agent.run", "tool.read", "policy.read",
    "action.read", "action.propose", "action.cancel", "approval.read",
]

INSTALLED = [
    "qwen2.5-coder:1.5b", "qwen2.5-coder:7b", "qwen2.5-coder:7b-opencode-safe",
    "qwen2.5-coder:14b-agentic", "qwen2.5-coder:7b-agentic",
    "qwen2.5-coder:1.5b-fast", "deepseek-r1:14b", "qwen2.5-coder:14b",
]


def principal():
    return {
        "session_id": UUID(int=1), "device_id": UUID(int=2),
        "user": {"id": UUID(int=3), "display_name": "O", "status": "active"},
        "organization": {"id": UUID(int=4)},
        "workspace": {"id": UUID(int=5), "name": "Default"},
        "roles": ["joeos.owner"],
        "capabilities": list(CAPS),
    }


class AgentFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "agents.db"

        def connect():
            connection = sqlite3.connect(str(self.database), timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection

        self.store = SQLiteControlStore(connect)
        self.service = ActionService(self.store, now=lambda: 1_700_000_000_000)
        self.service.prepare()
        self.p = principal()
        self.summary = activate_agent_fabric(
            self.service, self.p, installed_models=list(INSTALLED),
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _install_executor(self, content="ok"):
        async def executor(messages, tools, decision):
            return {"content": content, "token_usage": 7}
        self.service._executor = executor

    def _agent_id(self, key):
        return next(a for a in self.service.list_agents(self.p) if a["key"] == key)["id"]


class ActivationTests(AgentFixture):
    def test_provider_registered_local_private(self):
        providers = self.service.list_providers(self.p)
        self.assertEqual(len(providers), 1)
        provider = providers[0]
        self.assertEqual(provider["key"], "ollama")
        self.assertEqual(provider["location"], "local")
        self.assertEqual(provider["provider_type"], "ollama")
        self.assertEqual(provider["status"], "active")

    def test_models_synced(self):
        models = self.service.list_models(self.p)
        keys = {m["key"] for m in models}
        self.assertGreaterEqual(len(keys), len(INSTALLED))
        for name in INSTALLED:
            self.assertIn(name, keys)

    def test_agents_created_and_bound(self):
        agents = self.service.list_agents(self.p)
        keys = {a["key"] for a in agents}
        for definition in AGENT_DEFINITIONS:
            self.assertIn(definition["key"], keys)
        for agent in agents:
            self.assertEqual(agent["default_provider_policy"], "ollama")
            self.assertNotEqual(agent["default_model_policy"], "backend")

    def test_tools_registered(self):
        tools = self.service.list_tools(self.p)
        tool_keys = {t["key"] for t in tools}
        for tool in SAFE_TOOL_DEFINITIONS:
            self.assertIn(tool["key"], tool_keys)

    def test_activation_idempotent(self):
        second = activate_agent_fabric(self.service, self.p, installed_models=list(INSTALLED))
        self.assertEqual(second["agents"], [])
        self.assertEqual(second["models_registered"], [])
        self.assertEqual(second["tools"], [])

    def test_missing_model_disabled(self):
        # A model that disappears from the runtime is disabled, not deleted.
        reduced = [m for m in INSTALLED if m != "qwen2.5-coder:7b"]
        activate_agent_fabric(self.service, self.p, installed_models=reduced)
        models = {m["key"]: m for m in self.service.list_models(self.p)}
        self.assertIn("qwen2.5-coder:7b", models)
        self.assertEqual(models["qwen2.5-coder:7b"]["status"], "disabled")


class AgentRunExecutionTests(AgentFixture):
    def test_architect_run_executes_and_persists(self):
        self._install_executor("Architect result")
        agent_id = self._agent_id("joeos.architect")
        run = self.service.start_agent_run(
            self.p, agent_id=agent_id, conversation_id=uuid4(), message_id=uuid4(),
            objective="Describe your role.",
        )
        self.assertEqual(run["objective"], "Describe your role.")
        executed = asyncio.run(self.service.execute_agent_run(self.p, run["id"]))
        self.assertEqual(executed["status"], "succeeded")
        self.assertEqual(executed["result"], "Architect result")
        self.assertEqual(executed["provider_key"], "ollama")
        self.assertIsNotNone(executed["model_key"])
        refreshed = self.service.get_agent_run(self.p, run["id"])
        self.assertEqual(refreshed["status"], "succeeded")
        self.assertTrue(refreshed["result"])

    def test_executor_failure_marks_run_failed(self):
        async def failing(messages, tools, decision):
            raise RuntimeError("connect: refused")
        self.service._executor = failing
        agent_id = self._agent_id("joeos.builder")
        run = self.service.start_agent_run(
            self.p, agent_id=agent_id, conversation_id=uuid4(), message_id=uuid4(),
            objective="work",
        )
        executed = asyncio.run(self.service.execute_agent_run(self.p, run["id"]))
        self.assertEqual(executed["status"], "failed")
        self.assertEqual(executed["failure"], "OLLAMA_UNAVAILABLE")

    def test_cancel_run(self):
        self._install_executor()
        agent_id = self._agent_id("joeos.researcher")
        run = self.service.start_agent_run(
            self.p, agent_id=agent_id, conversation_id=uuid4(), message_id=uuid4(),
            objective="read",
        )
        self.service.cancel_agent_run(self.p, run["id"])
        refreshed = self.service.get_agent_run(self.p, run["id"])
        self.assertEqual(refreshed["status"], "cancelled")


class DelegationTests(AgentFixture):
    def test_delegation_creates_real_child_run(self):
        self._install_executor("child result")
        joe_id = self._agent_id("joeos.joe")
        arch_id = self._agent_id("joeos.architect")
        parent = self.service.start_agent_run(
            self.p, agent_id=joe_id, conversation_id=uuid4(), message_id=uuid4(),
            objective="delegate to architect",
        )
        child = asyncio.run(self.service.delegate_agent_run(
            self.p, parent_run_id=parent["id"], child_agent_id=arch_id,
            objective="architect subtask",
        ))
        self.assertNotEqual(child["id"], parent["id"])
        self.assertEqual(child["parent_run_id"], parent["id"])
        self.assertEqual(child["delegation_depth"], 1)
        self.assertEqual(child["status"], "succeeded")
        children = self.store.list_child_runs(parent["id"])
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0].agent_id, arch_id)

    def test_delegation_depth_limit(self):
        self._install_executor()
        joe_id = self._agent_id("joeos.builder")
        # builder has max_delegation_depth=0
        run = self.service.start_agent_run(
            self.p, agent_id=joe_id, conversation_id=uuid4(), message_id=uuid4(),
            objective="root",
        )
        with self.assertRaises(ActionDeniedError):
            asyncio.run(self.service.delegate_agent_run(
                self.p, parent_run_id=run["id"], child_agent_id=joe_id,
                objective="child",
            ))


class TaskGraphTests(AgentFixture):
    def test_task_graph_execution_order(self):
        self._install_executor("done")
        joe_id = self._agent_id("joeos.joe")
        arch_id = self._agent_id("joeos.architect")
        verify_id = self._agent_id("joeos.verifier")
        run = self.service.start_agent_run(
            self.p, agent_id=joe_id, conversation_id=uuid4(), message_id=uuid4(),
            objective="graph",
        )
        graph = self.service.create_task_graph(self.p, run_id=run["id"], tasks=[
            {"key": "a", "title": "Analyze", "objective": "analyze",
             "assigned_agent_id": arch_id, "dependencies": ""},
            {"key": "b", "title": "Verify", "objective": "verify",
             "assigned_agent_id": verify_id, "dependencies": "a"},
            {"key": "c", "title": "Summarize", "objective": "summarize",
             "assigned_agent_id": joe_id, "dependencies": "b"},
        ])
        self.assertEqual(len(graph["tasks"]), 3)
        result = asyncio.run(self.service.execute_task_graph(self.p, run["id"]))
        states = {t["title"]: t["state"] for t in result["tasks"]}
        self.assertEqual(states["Analyze"], "succeeded")
        self.assertEqual(states["Verify"], "succeeded")
        self.assertEqual(states["Summarize"], "succeeded")
        # Each task produced a real child run bound to its assigned agent.
        children = self.store.list_child_runs(run["id"])
        self.assertGreaterEqual(len(children), 3)

    def test_task_graph_accepts_http_wire_models(self):
        # The HTTP router passes pydantic model objects (not dicts); the service
        # must normalize them (regression for the live task-graph path).
        from server.actions.models import TaskGraphRequest, TaskNodeRequest
        self._install_executor("done")
        joe_id = self._agent_id("joeos.joe")
        arch_id = self._agent_id("joeos.architect")
        verify_id = self._agent_id("joeos.verifier")
        run = self.service.start_agent_run(
            self.p, agent_id=joe_id, conversation_id=uuid4(), message_id=uuid4(),
            objective="graph",
        )
        request = TaskGraphRequest(tasks=[
            TaskNodeRequest(key="a", title="Analyze", objective="analyze",
                            assigned_agent_id=arch_id, dependencies=""),
            TaskNodeRequest(key="b", title="Verify", objective="verify",
                            assigned_agent_id=verify_id, dependencies="a"),
        ])
        graph = self.service.create_task_graph(self.p, run_id=run["id"], tasks=request.tasks)
        self.assertEqual(len(graph["tasks"]), 2)
        result = asyncio.run(self.service.execute_task_graph(self.p, run["id"]))
        states = {t["title"]: t["state"] for t in result["tasks"]}
        self.assertEqual(states["Analyze"], "succeeded")
        self.assertEqual(states["Verify"], "succeeded")

    def test_task_graph_failure_propagates(self):
        async def failing(messages, tools, decision):
            raise RuntimeError("server error")
        self.service._executor = failing
        joe_id = self._agent_id("joeos.joe")
        arch_id = self._agent_id("joeos.architect")
        run = self.service.start_agent_run(
            self.p, agent_id=joe_id, conversation_id=uuid4(), message_id=uuid4(),
            objective="graph",
        )
        self.service.create_task_graph(self.p, run_id=run["id"], tasks=[
            {"key": "a", "title": "Analyze", "objective": "analyze",
             "assigned_agent_id": arch_id, "dependencies": ""},
            {"key": "b", "title": "Verify", "objective": "verify",
             "assigned_agent_id": arch_id, "dependencies": "a"},
        ])
        result = asyncio.run(self.service.execute_task_graph(self.p, run["id"]))
        states = {t["title"]: t["state"] for t in result["tasks"]}
        self.assertEqual(states["Analyze"], "failed")
        self.assertIn(states["Verify"], ("blocked", "failed"))


class OverviewTests(AgentFixture):
    def test_overview_returns_authoritative_state(self):
        overview = self.service.overview(self.p)
        self.assertGreaterEqual(overview["agents"]["total"], len(AGENT_DEFINITIONS))
        self.assertGreaterEqual(overview["providers"]["total"], 1)
        self.assertGreaterEqual(overview["models"]["total"], len(INSTALLED))
        self.assertGreaterEqual(overview["tools"]["total"], len(SAFE_TOOL_DEFINITIONS))


if __name__ == "__main__":
    unittest.main()
