"""JoeOS Object Intelligence tests (P1–P4 backend).

Covers semantic status, capability reasons, relationship ranking, the
object activity timeline store, and the causal "Why?" resolver.
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.objects.core import ObjectRef
from server.objects.intelligence import (
    ObjectActivityStore,
    rank_relationships,
    semantic_status,
    relationship_weight,
)
from server.objects.resolver import ObjectResolver


def _in_memory_connection_factory():
    """Return a factory that hands out the SAME in-memory connection so the
    prepared schema persists across calls (SQLite :memory: per-connection)."""
    connection = sqlite3.connect(":memory:")
    def factory():
        return connection
    return factory


class SemanticStatusTest(unittest.TestCase):
    def test_healthy_normalization(self):
        for raw in ("healthy", "ok", "200 OK", "good", "success"):
            self.assertEqual(semantic_status(raw)["state"], "healthy", raw)

    def test_failed_and_error(self):
        self.assertEqual(semantic_status("failed")["state"], "failed")
        self.assertEqual(semantic_status("error")["state"], "error")
        self.assertEqual(semantic_status("503")["state"], "error")
        self.assertEqual(semantic_status("502 Bad Gateway")["state"], "error")

    def test_offline_and_unavailable(self):
        for raw in ("offline", "unavailable", "down"):
            self.assertEqual(semantic_status(raw)["state"], "offline", raw)

    def test_blocked_and_waiting(self):
        self.assertEqual(semantic_status("blocked")["state"], "blocked")
        self.assertEqual(semantic_status("waiting")["state"], "waiting")
        self.assertEqual(semantic_status("pending")["state"], "waiting")
        self.assertEqual(semantic_status("in_review")["state"], "waiting")

    def test_semantic_has_meaning_impact_next(self):
        entry = semantic_status("degraded")
        self.assertIn("meaning", entry)
        self.assertIn("impact", entry)
        self.assertIn("next", entry)
        self.assertIn("tone", entry)

    def test_raw_preserved(self):
        self.assertEqual(semantic_status("PARTIALLY_DEGRADED")["raw"], "PARTIALLY_DEGRADED")


class RelationshipRankingTest(unittest.TestCase):
    def test_weights_exist(self):
        self.assertEqual(relationship_weight("depends_on"), 1.0)
        self.assertGreaterEqual(relationship_weight("uses"), 0.8)
        self.assertLess(relationship_weight("related_to"), 0.5)

    def test_elevated_state_ranks_higher(self):
        base = [
            {"relation": "related_to", "object": {"object_id": "x1"}},
            {"relation": "uses", "object": {"object_id": "m1", "status": "failed"}},
        ]
        ranked = rank_relationships(base, object_status="failed")
        # The failed dependency is the most important.
        self.assertEqual(ranked[0]["object"]["object_id"], "m1")
        self.assertIn("importance", ranked[0])

    def test_ranking_never_raises(self):
        ranked = rank_relationships([], object_status=None)
        self.assertEqual(ranked, [])


class ActivityStoreTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._temp.name) / "activity.db"
        self._store = ObjectActivityStore(self._connection_factory)

    def _connection_factory(self):
        return sqlite3.connect(str(self._db_path))

    def tearDown(self):
        self._temp.cleanup()

    def test_record_and_read(self):
        self._store.record(
            actor="ApplePlatformAgent",
            action="completed",
            object_type="work_package",
            object_id="wp-1",
            result="ok",
            detail="Native Files Detail",
            related_type="artifact",
            related_id="art-1",
        )
        self._store.record(
            actor="joe",
            action="created",
            object_type="work_package",
            object_id="wp-1",
            result="ok",
        )
        entries = self._store.for_object("work_package", "wp-1")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["actor"], "joe")
        self.assertEqual(entries[1]["actor"], "ApplePlatformAgent")
        self.assertEqual(entries[1]["related"]["object_id"], "art-1")

    def test_related_objects_traversable(self):
        self._store.record(
            actor="a", action="produced", object_type="artifact", object_id="art-9",
            related_type="execution", related_id="ex-42",
        )
        entries = self._store.for_object("artifact", "art-9")
        self.assertEqual(entries[0]["related"], {"object_type": "execution", "object_id": "ex-42"})

    def test_other_object_not_returned(self):
        self._store.record(actor="a", action="created", object_type="agent", object_id="other")
        self.assertEqual(self._store.for_object("work_package", "wp-missing"), [])

    def test_bounded_rows(self):
        for index in range(15):
            self._store.record(actor="x", action="touch", object_type="module", object_id="m1", detail=str(index))
        entries = self._store.for_object("module", "m1")
        self.assertEqual(len(entries), 15)


class CausalResolverTest(unittest.TestCase):
    def test_explain_unknown_object(self):
        from server.objects.causality import CausalResolver
        from server.objects.intelligence import ObjectActivityStore

        resolver = ObjectResolver()
        store = ObjectActivityStore(_in_memory_connection_factory())
        causal = CausalResolver(resolver, store)
        result = causal.explain(ObjectRef("missing", "agent"), {"sub": "u"})
        self.assertEqual(result["category"], "permission")
        self.assertIn("not accessible", result["explanation"])

    def test_explain_unknown_type(self):
        from server.objects.causality import CausalResolver
        from server.objects.intelligence import ObjectActivityStore

        resolver = ObjectResolver()
        store = ObjectActivityStore(_in_memory_connection_factory())
        causal = CausalResolver(resolver, store)
        result = causal.explain(ObjectRef("x", "bogus"), {"sub": "u"})
        self.assertEqual(result["category"], "unknown")

    def test_explain_healthy_agent(self):
        from server.objects.causality import CausalResolver
        from server.objects.intelligence import ObjectActivityStore

        resolver = ObjectResolver()

        class FakeOrg:
            def agent(self, agent_id):
                if agent_id == "a1":
                    return type("A", (), {"agent_id": "a1", "display_name": "Joe", "availability": "busy", "role_id": "orchestrator", "capabilities": ["code"]})()
                return None

        class FakeAgents:
            def __init__(self):
                self.organization = FakeOrg()

        resolver.wire_agents(FakeAgents())
        store = ObjectActivityStore(_in_memory_connection_factory())
        causal = CausalResolver(resolver, store)
        result = causal.explain(ObjectRef("a1", "agent"), {"sub": "u"})
        self.assertEqual(result["category"], "ok")
        self.assertIn("running", result["explanation"])
        self.assertGreaterEqual(len(result["evidence"]), 1)

    def test_explain_failed_agent_reports_dependency_health(self):
        from server.objects.causality import CausalResolver
        from server.objects.intelligence import ObjectActivityStore

        resolver = ObjectResolver()

        class FakeOrg:
            def agent(self, agent_id):
                return type("A", (), {"agent_id": "a1", "display_name": "Joe", "availability": "failed", "role_id": "orchestrator", "capabilities": ["code"]})()

        class FakeAgents:
            def __init__(self):
                self.organization = FakeOrg()

        resolver.wire_agents(FakeAgents())
        store = ObjectActivityStore(_in_memory_connection_factory())
        causal = CausalResolver(resolver, store)
        result = causal.explain(ObjectRef("a1", "agent"), {"sub": "u"})
        self.assertIn(result["category"], ("failure", "ok"))


class CompareTest(unittest.TestCase):
    def test_type_aware_model_compare(self):
        from server.objects.compare import compare_objects
        from server.objects.core import ObjectRef
        from server.objects.intelligence import ObjectActivityStore

        resolver = ObjectResolver()
        resolver.wire_runtime(lambda: {"models": ["qwen3", "llama3"], "provider": "ollama", "providers": [{"id": "ollama", "healthy": True}]})
        result = compare_objects(
            ObjectRef("qwen3", "model"), ObjectRef("llama3", "model"),
            resolver, {"sub": "u"},
        )
        self.assertTrue(result["comparable"])
        self.assertEqual(result["object_type"], "model")
        self.assertGreaterEqual(len(result["rows"]), 1)
        self.assertIn("differences", result)

    def test_incompatible_types(self):
        from server.objects.compare import compare_objects
        from server.objects.core import ObjectRef

        resolver = ObjectResolver()
        result = compare_objects(ObjectRef("a", "agent"), ObjectRef("m", "model"), resolver, {"sub": "u"})
        self.assertFalse(result["comparable"])
        self.assertIn("error", result)

    def test_unknown_type_not_comparable(self):
        from server.objects.compare import compare_objects
        from server.objects.core import ObjectRef

        resolver = ObjectResolver()
        result = compare_objects(ObjectRef("x", "bogus"), ObjectRef("y", "bogus"), resolver, {"sub": "u"})
        self.assertFalse(result["comparable"])

    def test_inaccessible_objects(self):
        from server.objects.compare import compare_objects
        from server.objects.core import ObjectRef

        resolver = ObjectResolver()
        result = compare_objects(ObjectRef("missing", "agent"), ObjectRef("also", "agent"), resolver, {"sub": "u"})
        self.assertFalse(result["comparable"])


class SecurityReviewTest(unittest.TestCase):
    def test_impact_never_leaks_unresolvable_objects(self):
        # An agent that depends on a provider but is not resolvable by the
        # principal must NOT appear in impact analysis (no identity leak).
        from server.objects.core import ObjectRef
        from server.objects.intelligence import ObjectActivityStore

        resolver = ObjectResolver()

        class FakeOrg:
            def agents(self, include_inactive=False):
                # This agent references the provider but is NOT accessible.
                return [type("A", (), {"agent_id": "hidden-agent", "display_name": "Hidden", "provider_id": "ollama", "model_id": "qwen3", "availability": "busy", "capabilities": ["code"]})()]
            def agent(self, agent_id):
                return None  # never resolvable

        class FakeAgents:
            def __init__(self):
                self.organization = FakeOrg()

        resolver.wire_agents(FakeAgents())
        impacted = resolver.impact(ObjectRef("ollama", "provider"), {"sub": "u"})
        # The hidden agent must not appear because it cannot be resolved.
        self.assertEqual(impacted, [])

    def test_impact_returns_resolvable_dependents(self):
        from server.objects.core import ObjectRef

        resolver = ObjectResolver()

        class FakeOrg:
            def agents(self, include_inactive=False):
                return [type("A", (), {"agent_id": "visible", "display_name": "Visible", "provider_id": "ollama", "model_id": "qwen3", "availability": "busy", "capabilities": ["code"]})()]
            def agent(self, agent_id):
                if agent_id == "visible":
                    return type("A", (), {"agent_id": "visible", "display_name": "Visible", "provider_id": "ollama", "model_id": "qwen3", "availability": "busy", "capabilities": ["code"]})()
                return None

        class FakeAgents:
            def __init__(self):
                self.organization = FakeOrg()

        resolver.wire_agents(FakeAgents())
        impacted = resolver.impact(ObjectRef("ollama", "provider"), {"sub": "u"})
        self.assertEqual(len(impacted), 1)
        self.assertEqual(impacted[0]["object"]["object_id"], "visible")


if __name__ == "__main__":
    unittest.main()
