"""Owner-hold regressions; no production services, database, or model calls."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
BYTE = "jmw7629/stickdeath-byte"
VITROS = "jmw7629/vitros-web-dashboard"


class OwnerHoldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.backend = types.ModuleType("backend")
        self.backend.__file__ = str(self.root / "backend.py")
        source = "".join(p.read_text() for p in sorted((HERE / "server").glob("*.part")))
        exec(compile(source, self.backend.__file__, "exec"), self.backend.__dict__)
        self.marker = self.root / ".config/joeos-opencode-bridge/STICKDEATH_BYTE_STOPPED_BY_OWNER"
        self.marker.parent.mkdir(parents=True)
        self.backend.BYTE_OWNER_STOP = self.marker
        with mock.patch.dict(sys.modules, {"backend": self.backend}):
            spec = importlib.util.spec_from_file_location("test_runtime", HERE / "runtime_server.py")
            self.runtime = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.runtime)

    def make_hold(self):
        self.marker.write_text("private owner instruction must never leave the host")

    def test_absent_marker_leaves_routes_unchanged(self):
        self.assertIsNone(self.backend.execution_hold(BYTE))
        self.assertIsNone(self.backend.execution_hold(VITROS))
        self.assertIsNone(self.backend.execution_hold("unapproved/repo"))

    def test_present_marker_does_not_read_contents(self):
        self.make_hold()
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("content read")):
            hold = self.backend.execution_hold(BYTE)
        self.assertEqual(hold["reason_code"], "owner-paused")
        self.assertEqual(hold["http_status"], 409)
        self.assertNotIn(str(self.root), json.dumps(hold))
        self.assertNotIn("private owner", json.dumps(hold))

    def test_broken_marker_symlink_still_holds(self):
        self.marker.symlink_to(self.root / "missing")
        self.assertEqual(self.backend.execution_hold(BYTE)["reason_code"], "owner-paused")

    def test_inaccessible_marker_fails_closed(self):
        with mock.patch.object(Path, "lstat", side_effect=PermissionError("private path")):
            hold = self.backend.execution_hold(BYTE)
        self.assertEqual(hold["reason_code"], "owner-hold-unavailable")
        self.assertEqual(hold["http_status"], 503)
        self.assertNotIn("private path", json.dumps(hold))

    def test_issue_helper_rejects_hold_before_side_effects(self):
        self.make_hold()
        with mock.patch.object(self.backend.tempfile, "NamedTemporaryFile", side_effect=AssertionError("temp write")), \
             mock.patch.object(self.backend.subprocess, "run", side_effect=AssertionError("dispatch")):
            with self.assertRaises(self.backend.ExecutionHoldError):
                self.backend.create_github_issue(BYTE, "fixture", "fixture prompt")

    def test_issue_helper_vitros_and_absent_hold_still_use_approved_route(self):
        for repo in (BYTE, VITROS):
            expected = f"https://github.com/{repo}/issues/999"
            with mock.patch.object(self.backend.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, expected, "")) as dispatch:
                self.assertEqual(self.backend.create_github_issue(repo, "fixture", "fixture"), (999, expected))
            self.assertEqual(dispatch.call_args.args[0][:5], ["gh", "issue", "create", "--repo", repo])
        self.make_hold()
        self.assertIsNone(self.backend.execution_hold(VITROS))

    def test_issue_helper_rechecks_hold_before_dispatch(self):
        hold = {"reason_code": "owner-paused", "reason": "BYTE is paused by owner; execution is disabled", "http_status": 409}
        with mock.patch.object(self.backend, "execution_hold", side_effect=[None, hold]), \
             mock.patch.object(self.backend.subprocess, "run", side_effect=AssertionError("dispatch")):
            with self.assertRaises(self.backend.ExecutionHoldError):
                self.backend.create_github_issue(BYTE, "fixture", "fixture")
        self.assertEqual(list(self.root.glob("*.db")), [])

    def test_http_queue_rejects_before_model_prompt_database_or_github(self):
        self.make_hold()
        handler = object.__new__(self.backend.H)
        handler.path = "/api/tasks/fixture/queue"
        handler.who = lambda: {"name": "Fixture editor", "level": 2}
        handler.need = lambda level: {"name": "Fixture editor", "level": 2}
        handler.sendj = lambda payload, status=200: (payload, status)
        handler.body = lambda: (_ for _ in ()).throw(AssertionError("body/route should not be consumed"))
        with mock.patch.object(self.backend, "task_by_id", return_value={"id": "fixture", "project": "BYTE"}), \
             mock.patch.object(self.backend, "project_by_name", return_value={"repo": BYTE}), \
             mock.patch.object(self.backend, "con", side_effect=AssertionError("database mutation")), \
             mock.patch.object(self.backend, "route_model", side_effect=AssertionError("model route")), \
             mock.patch.object(self.backend, "create_github_issue", side_effect=AssertionError("GitHub")), \
             mock.patch.object(self.backend, "add_run_event", side_effect=AssertionError("event")):
            payload, status = handler.do_POST()
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "owner-paused")
        self.assertEqual(list(self.root.glob("*.db")), [])

    def test_r3_remains_unconditionally_non_dispatchable(self):
        with self.assertRaises(RuntimeError):
            self.backend.create_github_issue("jmw7629/StickDeath-Infinity-", "fixture", "fixture")

    def health_fixture(self, active_child=False, executor_evidence_complete=True):
        rt = self.runtime
        current = rt.time.time()
        rt.SYNC_STATE.update(last_attempt=int(current), last_ok=int(current), last_error="")
        rt._sqlite_health = lambda: {"state": "healthy"}
        rt._model_health = lambda: {"state": "tested_ok"}
        rt._service_state = lambda service: "active"
        rt._pending_runs = lambda repo, now: {"count": 0}
        rt._latest_bridge_activity = lambda repo, **kw: (current, True)
        rt._active_executor = lambda repo, **kw: {"running": active_child and repo == BYTE, "count": int(active_child and repo == BYTE), "evidence_complete": executor_evidence_complete}
        rt._execution_readiness = lambda repo, now, **kw: {"state": "ready", "reason_code": "success", "reason": "last execution completed successfully", "last_execution_age_seconds": 5, "last_run_ref": "issue-1", "freshness_window_seconds": 1800}
        return rt.health_snapshot()

    def test_health_owner_hold_overrides_previous_success_without_exemption(self):
        self.make_hold()
        health = self.health_fixture()
        byte = health["components"]["bridges"]["stickdeath"]
        self.assertTrue(health["ok"])
        self.assertTrue(health["ai_chat_ready"])
        self.assertFalse(health["operational"])
        self.assertFalse(health["ai_ops"])
        self.assertTrue(byte["required"])
        self.assertTrue(byte["owner_paused"])
        self.assertEqual(byte["state"], "paused")
        self.assertEqual(byte["execution_state"], "blocked")
        self.assertEqual(byte["pending_count"], 0)
        self.assertIn("stickdeath-owner-paused", health["warnings"])
        self.assertNotIn("STOPPED_BY_OWNER", json.dumps(health))
        self.assertNotIn(str(self.root), json.dumps(health))

    def test_running_child_during_hold_is_not_claimed_stopped(self):
        self.make_hold()
        byte = self.health_fixture(active_child=True)["components"]["bridges"]["stickdeath"]
        self.assertEqual(byte["state"], "failed")
        self.assertEqual(byte["progress_state"], "running-despite-owner-hold")
        self.assertTrue(byte["external_running"])
        self.assertEqual(byte["execution_state"], "blocked")
        self.assertEqual(byte["execution_reason_code"], "owner-paused")
        self.assertEqual(byte["pending_count"], 0)

    def test_running_child_partial_scan_during_hold_is_still_observed(self):
        self.make_hold()
        byte = self.health_fixture(active_child=True, executor_evidence_complete=False)["components"]["bridges"]["stickdeath"]
        self.assertEqual(byte["state"], "failed")
        self.assertEqual(byte["progress_state"], "running-despite-owner-hold")
        self.assertTrue(byte["external_running"])
        self.assertFalse(byte["external_evidence_complete"])
        self.assertEqual(byte["execution_state"], "blocked")
        self.assertEqual(byte["execution_reason_code"], "owner-paused")

    def test_health_missing_hold_evidence_never_goes_green(self):
        with mock.patch.object(Path, "lstat", side_effect=PermissionError("private path")):
            health = self.health_fixture()
        self.assertFalse(health["operational"])
        byte = health["components"]["bridges"]["stickdeath"]
        self.assertEqual(byte["execution_reason_code"], "owner-hold-unavailable")
        self.assertFalse(byte["owner_paused"])
        self.assertNotIn("private path", json.dumps(health))

    def test_absent_hold_health_unchanged(self):
        self.assertTrue(self.health_fixture()["operational"])

    def installer_functions(self):
        source = (HERE.parent / "install.sh").read_text()
        return source[source.index("byte_owner_hold_clear() {"):source.index("\ncheck_bridge_service() {")]

    def run_installer_fixture(self, command):
        fakebin = self.root / "bin"
        fakebin.mkdir(exist_ok=True)
        calls = self.root / "unexpected-calls"
        for name in ("git", "systemctl"):
            target = fakebin / name
            target.write_text('#!/bin/sh\necho unexpected >> "$FIXTURE_CALLS"\nexit 99\n')
            target.chmod(0o700)
        env = dict(os.environ, HOME=str(self.root), PATH=str(fakebin) + ":" + os.environ["PATH"], FIXTURE_CALLS=str(calls))
        result = subprocess.run(["bash", "-c", self.installer_functions() + "\n" + command], env=env, text=True, capture_output=True, timeout=10)
        self.assertFalse(calls.exists(), result.stdout + result.stderr)
        return result

    def test_installer_hold_prevents_even_checkout_inspection_and_restart(self):
        self.make_hold()
        result = self.run_installer_fixture('refresh_bridge /nonexistent/fixture.env stickdeath-byte-opencode-bridge.service')
        self.assertEqual(result.returncode, 1)
        self.assertIn("paused by owner", result.stderr)
        self.assertNotIn("configuration file is absent", result.stderr)
        self.assertTrue(self.marker.exists())

    def test_installer_clear_marker_check_is_non_mutating(self):
        self.assertEqual(self.run_installer_fixture('byte_owner_hold_clear').returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_installer_inaccessible_evidence_fails_closed(self):
        self.marker.parent.rmdir()
        self.marker.parent.symlink_to(self.marker.parent)
        result = self.run_installer_fixture('refresh_bridge /nonexistent/fixture.env stickdeath-byte-opencode-bridge.service')
        self.assertEqual(result.returncode, 1)
        self.assertIn("evidence unavailable", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
