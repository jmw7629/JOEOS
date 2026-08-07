import hashlib
import tempfile
import unittest
from pathlib import Path

from server.memory import MemoryService
from server.memory.models import (
    EntityRecord,
    EvidenceRecord,
    MemoryRecord,
    Provenance,
    RelationshipRecord,
)


def _now() -> str:
    return "2026-08-03T00:00:00+00:00"


def _id() -> str:
    return hashlib.sha256("x".encode()).hexdigest()


def _provenance(kind="user_statement", source="test", method="explicit_user") -> Provenance:
    return Provenance(kind=kind, source=source, method=method, learned_at=_now())


def _record(memory_id=None, title="Default", content="default content", memory_type="semantic", subtype="fact", claim_state="proposed", authority="user_provided_claim", confidence="uncertain", **kw) -> MemoryRecord:
    mid = memory_id or ("mem_" + hashlib.sha256((title + "|" + content).encode()).hexdigest()[:22])
    return MemoryRecord(
        memory_id=mid,
        memory_type=memory_type,
        subtype=subtype,
        title=title,
        content=content,
        primary_scope="user",
        learned_at=_now(),
        source=_provenance(),
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        claim_state=claim_state,
        authority=authority,
        confidence=confidence,
        version=1,
        created_at=_now(),
        updated_at=_now(),
        **kw,
    )


class MemoryFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = MemoryService(str(Path(self.tempdir.name) / "mem"))

    def tearDown(self):
        self.tempdir.cleanup()


class MemoryRecordTests(MemoryFixture):
    def test_propose_get_list_and_delete(self):
        record = _record(title="Stable fingerprint", content="A fingerprint identifies the project.", memory_type="semantic", subtype="fact", authority="explicit_user_instruction", confidence="confirmed")
        stored = self.service.propose(record)
        self.assertEqual(stored.memory_id, record.memory_id)
        self.assertEqual(stored.updated_at, stored.updated_at)

        fetched = self.service.get(record.memory_id)
        self.assertEqual(fetched.title, "Stable fingerprint")
        self.assertEqual(fetched.primary_scope, "user")

        self.assertEqual(len(self.service.list()), 1)
        self.assertTrue(self.service.delete(record.memory_id))
        self.assertEqual(self.service.get(record.memory_id).deletion_state, "deleted")
        self.assertEqual(len(self.service.list()), 0)

    def test_correct_creates_version(self):
        record = _record(content="original")
        self.service.propose(record)
        corrected = self.service.correct(record.memory_id, new_content="corrected", reason="fix wording")
        self.assertEqual(corrected.version, 2)
        self.assertEqual(corrected.content, "corrected")
        versions = self.service.versions(record.memory_id)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].action, "corrected")

    def test_expire_due_marks_expired(self):
        record = _record(content="temporary", memory_type="working", subtype="execution_state", expires_at="2020-01-01T00:00:00+00:00")
        self.service.propose(record)
        count = self.service.expire_due()
        self.assertEqual(count, 1)
        expired = self.service.get(record.memory_id)
        self.assertEqual(expired.temporal_state, "expired")
        self.assertEqual(expired.claim_state, "expired")


class MemorySearchTests(MemoryFixture):
    def test_token_overlap_search_ranks_and_scopes(self):
        a = _record(title="Project conventions", content="Use double quotes in javascript source files", confidence="confirmed")
        b = _record(title="Deployment steps", content="Deploy the package to the local runtime", memory_type="procedural", subtype="procedure")
        self.service.propose(a)
        self.service.propose(b)

        envelope = self.service.search("conventions javascript quotes", scope="user", limit=10)
        self.assertTrue(envelope.semantic)
        self.assertTrue(envelope.results)
        matches = [r.memory_id for r in envelope.results]
        self.assertIn(a.memory_id, matches)
        self.assertTrue(all(r.score >= 0.0 for r in envelope.results))

        empty = self.service.search("zzzqqq", scope="user")
        self.assertEqual(len(empty.results), 0)


class EvidenceAndGraphTests(MemoryFixture):
    def test_evidence_entity_relationship(self):
        evidence = EvidenceRecord(
            evidence_id="ev_" + _id()[:32],
            source_type="git_commit",
            source_reference="abc123",
            content_hash=hashlib.sha256(b"x").hexdigest(),
            created_at=_now(),
        )
        self.service.add_evidence(evidence)

        entity = EntityRecord(
            entity_id="ent_" + _id()[:32],
            entity_type="project",
            canonical_name="sample-project",
            scope="project",
            source=_provenance(),
            version=1,
            created_at=_now(),
            updated_at=_now(),
        )
        got = self.service.register_entity(entity)
        self.assertEqual(got.entity_id, entity.entity_id)
        entities = self.service.entities(scope="project")
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].canonical_name, "sample-project")

    def test_review_queue_lifecycle(self):
        record = _record()
        self.service.propose(record)
        enqueued = self.service.review_queue(state="open")
        self.assertTrue(len(enqueued.items) >= 0)
        self.assertTrue(self.service.health().diagnostics.accepted >= 0)

    def test_health_is_typed(self):
        health = self.service.health()
        self.assertIn(health.state, {"healthy", "degraded", "partially_available"})
        self.assertEqual(health.diagnostics.storage_version, 1)


class MemoryOverviewTests(MemoryFixture):
    def test_overview_builds_with_all_typed_fields(self):
        record = _record(title="Overview record", content="overview content")
        self.service.propose(record)
        overview = self.service.overview()
        self.assertGreaterEqual(overview.awaiting_review, 0)
        self.assertGreaterEqual(overview.open_conflicts, 0)
        self.assertGreaterEqual(overview.stale_memories, 0)
        self.assertGreaterEqual(overview.expiring_soon, 0)
        self.assertGreaterEqual(overview.deletion_failures, 0)
        self.assertGreaterEqual(overview.documents_indexed, 0)
        self.assertGreaterEqual(overview.active_context_count, 0)
        self.assertEqual(overview.semantic_available, True)
        self.assertIsInstance(overview.needs_attention, tuple)
        self.assertTrue(any(r.memory_id == record.memory_id for r in overview.recent))
        self.assertEqual(len(overview.recent), 1)

    def test_overview_reflects_open_conflict_backlog(self):
        health = self.service.overview().health
        self.assertIn(health.state, {"healthy", "degraded", "partially_available"})


if __name__ == "__main__":
    unittest.main()