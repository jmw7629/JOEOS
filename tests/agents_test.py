import tempfile
import unittest
from pathlib import Path

from server.agents import AgentsService
from server.agents.models import (
    AgentProfile,
    ApprovalRecord,
    ArtifactRecord,
    AssignmentExplanation,
    CollaborationMessage,
    ConsultationRecord,
    DebateRecord,
    DisagreementRecord,
    EscalationRecord,
    HandoffRecord,
    InterventionRecord,
    ModelRoute,
    OrgMemoryProposal,
    QualityGate,
    ReviewFinding,
    ReviewRecord,
)


def _artifact_id(seed: str) -> str:
    import hashlib
    return "art_" + hashlib.sha256(seed.encode()).hexdigest()[:22]


class AgentsFixture(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.service = AgentsService(str(Path(self.tempdir.name) / "agents"))
        self.org = self.service.get_or_create_organization()
        self.role = self.service.organization.create_role(
            "Engineer", required_capabilities=("coding", "reviewing")
        )
        self.agent = self.service.organization.create_agent(
            "Reliable Agent", self.role.role_id, team="Engineering"
        )
        self.reviewer = self.service.organization.create_agent(
            "Reviewer", self.role.role_id, team="Engineering"
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def _mission(self, title="Build feature", objective="Ship a feature") -> dict:
        mission = self.service.missions.create_mission(title, objective)
        self.service.missions.new_charter(mission.mission_id, objective, ("tests pass",))
        self.service.missions.approve_charter(mission.mission_id)
        self.service.missions.start(mission.mission_id)
        return {
            "mission": mission,
            "mission_id": mission.mission_id,
        }

    def _explanation(self, task_id: str, agent_id: str) -> AssignmentExplanation:
        return AssignmentExplanation(
            task_id=task_id,
            selected_agent=agent_id,
            role_match=1.0,
            capability_match=1.0,
            model_match=True,
            permission_match=True,
            confidence="high",
            reason="best available match",
        )


class OrganizationTests(AgentsFixture):
    def test_organization_is_created_once(self):
        org = self.service.get_or_create_organization()
        self.assertEqual(org.organization_id, self.org.organization_id)
        self.assertEqual(self.service.organization.get_organization().name, "JoeOS AI Organization")

    def test_units_roles_agents(self):
        unit = self.service.organization.create_unit("Engineering", "software_engineering")
        self.assertTrue(unit.unit_id.startswith("unit_"))
        self.assertEqual(len(self.service.organization.units()), 1)
        self.assertIsNotNone(self.service.organization.unit(unit.unit_id))
        self.assertTrue(self.service.organization.set_unit_enabled(unit.unit_id, False).enabled is False)

        self.assertTrue(self.agent.agent_id.startswith("agent_"))
        self.assertEqual(self.agent.role_id, self.role.role_id)
        fetched = self.service.organization.agent(self.agent.agent_id)
        self.assertEqual(fetched.display_name, "Reliable Agent")
        self.assertEqual(self.service.organization.agents()[0].display_name, "Reliable Agent")

    def test_agent_state_updates_and_enable(self):
        updated = self.service.organization.update_agent_state(
            self.agent.agent_id, status="active", availability="busy"
        )
        self.assertEqual(updated.status, "active")
        self.assertEqual(updated.availability, "busy")
        disabled = self.service.organization.set_agent_enabled(self.agent.agent_id, False)
        self.assertFalse(disabled.enabled)
        self.assertEqual(disabled.availability, "offline")
        self.assertEqual(len(self.service.organization.agents()), 1)

    def test_agent_overview_tolerates_corrupt_status_values(self):
        import sqlite3

        with sqlite3.connect(self.service.storage.path()) as conn:
            conn.execute(
                "UPDATE org_agents SET status = 'None', availability = 'None' WHERE agent_id = ?",
                (self.agent.agent_id,),
            )
            conn.commit()
        agents = self.service.organization.agents()
        self.assertEqual(len(agents), 2)
        corrupted = next(a for a in agents if a.agent_id == self.agent.agent_id)
        self.assertEqual(corrupted.status, "configured")
        self.assertEqual(corrupted.availability, "offline")


class MissionTests(AgentsFixture):
    def test_mission_lifecycle(self):
        mission = self.service.missions.create_mission("Fix bug", "Reproduce and fix the crash")
        self.assertEqual(mission.status, "draft")
        charter = self.service.missions.new_charter(mission.mission_id, "Reproduce and fix the crash", ("crash gone",), business_value="reliability")
        self.assertFalse(charter.approved)
        self.assertTrue(self.service.missions.approve_charter(mission.mission_id))
        self.assertTrue(self.service.missions.get_mission(mission.mission_id).status, "ready")
        self.assertTrue(self.service.missions.start(mission.mission_id))
        self.assertTrue(self.service.missions.get_mission(mission.mission_id).status in {"planning", "active"})

    def test_task_graph_and_assignment(self):
        ctx = self._mission()
        mission_id = ctx["mission_id"]
        t1 = self.service.missions.create_task(mission_id, "Plan", "Plan the work")
        t2 = self.service.missions.create_task(mission_id, "Implement", "Implement the work", dependencies=(t1.task_id,))
        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)
        self.service.missions.update_task_state(t1.task_id, "complete", note="planned")
        task = self.service.missions.assign(t2.task_id, self.agent.agent_id, self._explanation(t2.task_id, self.agent.agent_id))
        self.assertEqual(task.assigned_agent, self.agent.agent_id)
        self.assertEqual(task.status, "staffed")
        graph = self.service.missions.graph(mission_id)
        self.assertEqual(len(graph.tasks), 2)
        self.assertEqual(len(graph.cycles), 0)
        self.assertEqual(len(graph.dependencies), 1)

    def test_cycle_detection_and_deadlock(self):
        ctx = self._mission()
        mission_id = ctx["mission_id"]
        t1 = self.service.missions.create_task(mission_id, "A", "a")
        t2 = self.service.missions.create_task(mission_id, "B", "b")
        self.service.missions.add_dependency(mission_id, t1.task_id, t2.task_id)
        self.service.missions.add_dependency(mission_id, t2.task_id, t1.task_id)
        self.service.missions.update_task_state(t1.task_id, "blocked", note="needs B")
        self.service.missions.update_task_state(t2.task_id, "blocked", note="needs A")
        graph = self.service.missions.graph(mission_id)
        self.assertEqual(len(graph.cycles), 1)
        events = self.service.detection.scan_mission(mission_id)
        kinds = [e.kind for e in events]
        self.assertIn("deadlock", kinds)

    def test_dependency_blocked_propagation(self):
        ctx = self._mission()
        mission_id = ctx["mission_id"]
        t1 = self.service.missions.create_task(mission_id, "Blocking", "b")
        t2 = self.service.missions.create_task(mission_id, "Blocked", "b", blocking=(t1.task_id,))
        self.service.missions.update_task_state(t1.task_id, "complete", note="done")
        self.assertEqual(self.service.missions.task(t2.task_id).status, "not_started")

    def test_task_count_limit_blocks_creation(self):
        ctx = self._mission()
        mission_id = ctx["mission_id"]
        self.assertEqual(self.service.missions.create_task(mission_id, "X", "y", depth=99), None)


class CollaborationTests(AgentsFixture):
    def test_message_send_and_secret_redaction(self):
        msg = CollaborationMessage(
            message_id="msg_" + "a" * 20,
            sender="user",
            recipient=self.agent.agent_id,
            message_type="assignment",
            content="Please use api_key abc123 in the next step",
            trace_id="trace_" + "b" * 16,
        )
        stored = self.service.collaboration.send_message(msg)
        self.assertIn("***", stored.content)
        self.assertTrue(stored.redacted)
        fetched = self.service.collaboration.messages(recipient=self.agent.agent_id)
        self.assertEqual(len(fetched), 1)

    def test_handoff_lifecycle(self):
        handoff = HandoffRecord(
            handoff_id="hnd_" + "c" * 20,
            mission_id="mission_x",
            sending_agent=self.agent.agent_id,
            receiving_agent=self.reviewer.agent_id,
            objective="Review the change",
            incomplete_work="Run tests",
        )
        self.service.collaboration.send_handoff(handoff)
        self.assertEqual(self.service.collaboration.handoff(handoff.handoff_id).state, "sent")
        responded = self.service.collaboration.respond_handoff(handoff.handoff_id, "accept", note="on it")
        self.assertEqual(responded.state, "accepted")
        self.assertEqual(self.service.collaboration.handoff(handoff.handoff_id).response_note, "on it")

    def test_artifact_register_and_validate(self):
        artifact = ArtifactRecord(
            artifact_id=_artifact_id("code"),
            artifact_type="code_patch",
            title="Fix patch",
            producer=self.agent.agent_id,
            storage_reference="/tmp/patch.diff",
            content_hash="0" * 64,
        )
        self.service.collaboration.register_artifact(artifact)
        self.assertEqual(self.service.collaboration.artifact(artifact.artifact_id).review_state, "unreviewed")
        validated = self.service.collaboration.validate_artifact(artifact.artifact_id, "passed")
        self.assertEqual(validated.validation_state, "passed")

    def test_review_and_gate(self):
        ctx = self._mission()
        mission_id = ctx["mission_id"]
        gate = QualityGate(
            gate_id="gate_" + "d" * 20,
            mission_id=mission_id,
            gate_type="code_review",
            required_reviewer_role="Engineer",
        )
        self.service.collaboration.create_gate(gate)
        review = ReviewRecord(
            review_id="rev_" + "e" * 20,
            mission_id=mission_id,
            gate_id=gate.gate_id,
            reviewer=self.reviewer.agent_id,
            implementer=self.agent.agent_id,
        )
        self.service.collaboration.request_review(review)
        finding = ReviewFinding(finding_id="fnd_" + "f" * 20, review_id=review.review_id, severity="low", summary="Minor style issue")
        completed = self.service.collaboration.complete_review(
            review.review_id, conclusion="pass_with_conditions", findings=(finding,), confidence="medium"
        )
        self.assertEqual(completed.conclusion, "pass_with_conditions")
        self.assertEqual(self.service.collaboration.gate(gate.gate_id).state, "passed_with_conditions")

    def test_disagreement_and_consensus(self):
        ctx = self._mission()
        mission_id = ctx["mission_id"]
        disagreement = DisagreementRecord(
            disagreement_id="dis_" + "g" * 20,
            mission_id=mission_id,
            participants=(self.agent.agent_id, self.reviewer.agent_id),
            subject="Approach",
            positions=("A", "B"),
        )
        self.service.collaboration.open_disagreement(disagreement)
        resolved = self.service.collaboration.resolve_disagreement(disagreement.disagreement_id, method="evidence_review", notes="evidence favors A")
        self.assertEqual(resolved.state, "resolved")
        consensus = self.service.collaboration.record_consensus(
            __import__("server.agents.models", fromlist=["ConsensusResult"]).ConsensusResult(
                consensus_id="con_" + "h" * 20,
                subject="Approach",
                participants=(self.agent.agent_id,),
                method="majority_recommendation",
                positions=("A",),
                conclusion="Use A",
                authority="advisory",
            )
        )
        self.assertEqual(consensus.conclusion, "Use A")

    def test_debate_lifecycle(self):
        debate = DebateRecord(
            debate_id="dbt_" + "i" * 20,
            question="Which design?",
            participants=(self.agent.agent_id, self.reviewer.agent_id),
            max_rounds=4,
        )
        self.service.collaboration.create_debate(debate)
        advanced = self.service.collaboration.advance_debate(debate.debate_id, rounds=1)
        self.assertEqual(advanced.round_count, 1)
        concluded = self.service.collaboration.conclude_debate(debate.debate_id, synthesis="Option A")
        self.assertEqual(concluded.state, "concluded")

    def test_consultation(self):
        consultation = ConsultationRecord(
            consultation_id="cns_" + "j" * 20,
            question="Best approach?",
            requester=self.agent.agent_id,
            specialist=self.reviewer.agent_id,
        )
        self.service.collaboration.request_consultation(consultation)
        responded = self.service.collaboration.respond_consultation(
            consultation.consultation_id, response="Use A", conclusion="A", confidence="high"
        )
        self.assertEqual(responded.state, "responded")
        self.assertEqual(responded.conclusion, "A")


class GovernanceTests(AgentsFixture):
    def test_escalation_lifecycle(self):
        ctx = self._mission()
        escalation = EscalationRecord(
            escalation_id="esc_" + "k" * 20,
            source=self.agent.agent_id,
            mission_id=ctx["mission_id"],
            reason="approval_required",
            required_decision="Approve release",
        )
        self.service.governance.open_escalation(escalation)
        self.assertEqual(self.service.governance.escalation(escalation.escalation_id).state, "open")
        resolved = self.service.governance.resolve_escalation(escalation.escalation_id, response="Approved")
        self.assertEqual(resolved.state, "resolved")
        self.assertEqual(self.service.governance.open_escalation_count(), 0)

    def test_intervention_lifecycle(self):
        intervention = InterventionRecord(
            intervention_id="int_" + "l" * 20,
            need="User decision required",
            rationale="Two valid paths",
            options=("A", "B"),
            recommended_option="A",
            work_can_continue=True,
        )
        self.service.governance.open_intervention(intervention)
        responded = self.service.governance.respond_intervention(intervention.intervention_id, response="Choose B", approved=True, work_can_continue=False)
        self.assertEqual(responded.state, "approved")
        self.assertFalse(responded.work_can_continue)

    def test_approval_self_approval_blocked(self):
        approval = ApprovalRecord(
            approval_id="apr_" + "m" * 20,
            requester=self.agent.agent_id,
            action="deploy to production",
            risk="high",
        )
        self.service.governance.request_approval(approval)
        self.assertIsNone(self.service.governance.approve(approval.approval_id, approver=self.agent.agent_id))
        approved = self.service.governance.approve(approval.approval_id, approver="user")
        self.assertEqual(approved.state, "approved")
        denied = ApprovalRecord(approval_id="apr_" + "n" * 20, requester=self.agent.agent_id, action="remove directory")
        self.service.governance.request_approval(denied)
        self.assertEqual(self.service.governance.deny(denied.approval_id, approver="user").state, "denied")
        self.assertEqual(self.service.governance.pending_count(), 0)


class RoutingTests(AgentsFixture):
    def test_local_first_route(self):
        ctx = self._mission()
        route = self.service.routing.select(
            agent_id=self.agent.agent_id,
            mission_id=ctx["mission_id"],
            required_capabilities=("coding",),
        )
        self.assertEqual(route.provider, "local")
        self.assertTrue(route.model.startswith("local-"))
        self.assertIn("nothing leaves this device", route.disclosure)
        self.assertEqual(len(self.service.routing.routes(agent_id=self.agent.agent_id)), 1)

    def test_remote_policy_disclosure(self):
        route = self.service.routing.select(agent_id=self.agent.agent_id, policy="remote_only")
        self.assertEqual(route.provider, "remote")
        self.assertIn("configured, not actively running", route.disclosure)


class DetectionAndHealthTests(AgentsFixture):
    def test_stagnation_detection(self):
        ctx = self._mission()
        mission_id = ctx["mission_id"]
        events = self.service.detection.scan_mission(mission_id, stagnation_minutes=0)
        self.assertIn("stagnation", [e.kind for e in events])
        self.assertGreaterEqual(self.service.detection.open_count(), 1)

    def test_health_and_overview(self):
        health = self.service.current_health()
        self.assertIn(health.state, {"healthy", "attention_required", "degraded", "blocked"})
        overview = self.service.overview()
        self.assertEqual(overview.organization.organization_id, self.org.organization_id)
        self.assertGreaterEqual(len(overview.agents), 2)
        self.assertGreaterEqual(len(overview.roles), 1)

    def test_agent_performance_snapshot(self):
        snapshot = self.service.health.agent_performance(self.agent.agent_id)
        self.assertEqual(snapshot.tasks_completed, 0)
        self.assertEqual(snapshot.validation_pass_rate, 1.0)

    def test_memory_proposals(self):
        proposal = OrgMemoryProposal(
            proposal_id="prp_" + "o" * 20,
            kind="verified_outcome",
            title="Verified approach",
            content="Use the local-first routing approach.",
            proposer=self.agent.agent_id,
        )
        self.service.memory_proposals.propose(proposal)
        self.assertEqual(self.service.memory_proposals.pending_count(), 1)
        accepted = self.service.memory_proposals.review(proposal.proposal_id, action="accept", note="good", reviewer="user")
        self.assertEqual(accepted.state, "accepted")
        self.assertEqual(self.service.memory_proposals.pending_count(), 0)


class MissionEnvelopeTests(AgentsFixture):
    def test_envelope_and_budget(self):
        ctx = self._mission()
        mission_id = ctx["mission_id"]
        envelope = self.service.mission_envelope(mission_id)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.mission.mission_id, mission_id)
        self.assertIsNotNone(envelope.charter)
        budget = self.service.budget.check_mission_budget(mission_id)
        self.assertEqual(budget.state, "ok")


class StorageTests(AgentsFixture):
    def test_storage_stats_and_backup(self):
        stats = self.service.storage_stats()
        self.assertTrue(stats["path"].endswith("agents.db"))
        self.assertGreater(stats["size_bytes"], 0)
        self.assertEqual(stats["version"], 1)


if __name__ == "__main__":
    unittest.main()