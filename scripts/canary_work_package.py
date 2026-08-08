#!/usr/bin/env python3
"""Phase P3G canary: one real work package run end-to-end.

Creates a scratch git repository with a local bare remote, registers a campaign
against it, and advances a single work package through the full stage order
(eligibility, plan, worktree, implement, validate, review, commit, integrate,
push, complete) using the real GitExecutor (worktree/commit/ff-integrate/push)
and a deterministic stage handler that writes a tracked file as "implementation"
evidence. Verifies the final state and that the branch was pushed to the local
remote. Runs entirely under the scratch repo; the real repository is untouched.

Usage:
    python scripts/canary_work_package.py [--keep]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from server.engineering.campaign import (  # noqa: E402
    CampaignDefinition,
    CampaignService,
    CampaignStore,
    CampaignWorker,
    WorkPackageDefinition,
)

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

STAGES = ("eligibility", "plan", "worktree", "implement", "validate",
          "review", "commit", "integrate", "push", "complete")


def _run(command, cwd):
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError("command failed: %s\n%s" % (command, result.stderr))
    return result.stdout.strip()


def build_scratch_repo(root: Path) -> Path:
    repo = root / "repo"
    remote = root / "remote.git"
    repo.mkdir(parents=True)
    remote.mkdir(parents=True)
    _run(["git", "init", "--bare", "-q", str(remote)], root)
    _run(["git", "init", "-q", "-b", "ai-rebuild"], repo)
    _run(["git", "config", "user.email", "canary@joeos.local"], repo)
    _run(["git", "config", "user.name", "Canary"], repo)
    (repo / "README.md").write_text("# canary\n")
    _run(["git", "add", "README.md"], repo)
    _run(["git", "commit", "-q", "-m", "initial commit"], repo)
    _run(["git", "remote", "add", "origin", str(remote)], repo)
    _run(["git", "branch", "-M", "ai-rebuild"], repo)
    _run(["git", "push", "-q", "-u", "origin", "ai-rebuild"], repo)
    return repo


def deterministic_handler(principal, campaign, package, stage, attempt):
    """A deterministic stage handler that produces real, verifiable evidence
    while driving the real GitExecutor for the git stages (worktree, commit,
    integrate, push). Only the implementation content is deterministic."""
    from runner.joeos_runner.operations import GitExecutor

    worktree_root = Path(campaign.worktree_root)
    branch = "campaign-%s" % package.key
    path = worktree_root / branch
    repo = campaign.repository_path

    def git(root, operation, **params):
        return GitExecutor(str(root), allowed_remotes=("origin",)).execute(
            {"operation": operation, **params}, str(root), root=str(root))

    if stage == "worktree":
        result = git(repo, "worktree_add", branch=branch, path=str(path))
        return {"passed": result.get("exit_classification") == "clean",
                "detail": result.get("summary", ""),
                "evidence": (branch, str(path))}
    if stage == "implement":
        target = path / "implemented.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("implemented %s by deterministic canary\n" % package.key)
        return {"passed": True, "detail": "implemented %s" % package.key,
                "evidence": ("implemented.txt",)}
    if stage == "validate":
        marker = path / "validated.txt"
        marker.write_text("validated\n")
        return {"passed": True, "detail": "validation battery passed",
                "evidence": ("validated.txt",)}
    if stage == "review":
        return {"passed": True, "detail": "review passed",
                "evidence": ("review:clean",)}
    if stage == "commit":
        staged = git(path, "stage_all")
        if staged.get("exit_classification") != "clean":
            return {"passed": False, "detail": staged.get("summary", "stage failed")}
        committed = git(path, "commit",
                        message="campaign: %s (canary)" % package.key)
        return {"passed": committed.get("exit_classification") == "clean",
                "detail": committed.get("summary", ""),
                "evidence": (committed.get("data", {}).get("commit", ""),)}
    if stage == "integrate":
        result = git(repo, "ff_integrate", branch=branch)
        return {"passed": result.get("exit_classification") == "clean",
                "detail": result.get("summary", ""),
                "evidence": (result.get("data", {}).get("commit", ""),)}
    if stage == "push":
        result = git(repo, "push_branch", branch="ai-rebuild", remote="origin")
        return {"passed": result.get("exit_classification") == "clean",
                "detail": result.get("summary", ""),
                "evidence": ("push:origin",)}
    return {"passed": True, "detail": "stage advanced"}


def main() -> int:
    parser = argparse.ArgumentParser(prog="canary-work-package")
    parser.add_argument("--keep", action="store_true", help="keep scratch dir")
    args = parser.parse_args()

    scratch = tempfile.TemporaryDirectory(prefix="joeos-canary-")
    root = Path(scratch.name)
    try:
        repo = build_scratch_repo(root)
        db_path = root / "canary.db"

        def connect():
            connection = sqlite3.connect(str(db_path), timeout=10)
            connection.row_factory = sqlite3.Row
            return connection

        service = CampaignService(CampaignStore(connect))
        service.prepare()
        principal = {
            "session_id": None,
            "user": {"id": "canary", "display_name": "Canary"},
            "organization": {"id": "org-canary"},
            "workspace": {"id": "ws-canary"},
            "capabilities": list(CAPABILITIES),
        }
        campaign = service.create_campaign(principal, CampaignDefinition(
            key="canary",
            title="Canary work package",
            description="end-to-end canary against a scratch repo",
            repository_path=str(repo),
            base_branch="ai-rebuild",
            integration_branch="ai-rebuild",
            autonomy_policy_key="joeos.engineering.ai-rebuild.v1",
            worktree_root=str(root / "worktrees"),
            max_parallel_packages=1,
            max_attempts_per_package=3,
            heartbeat_timeout_ms=300_000,
        ))
        package = service.create_work_package(principal, campaign.campaign_id,
                                              WorkPackageDefinition(
                                                  key="canary-wp", title="Canary WP",
                                                  description="",
                                                  owner_agent_key="engineering.builder",
                                                  stage_order=STAGES))
        service.start_campaign(principal, campaign.campaign_id)
        service.start_work_package(principal, package.package_id)

        # Wire the real git executor for the executable git stages.
        from runner.joeos_runner.operations import GitExecutor

        worker = CampaignWorker(service, stage_handler=deterministic_handler)
        guard = 0
        while True:
            guard += 1
            worker.tick()
            state = service.get_work_package(principal, package.package_id).state
            if state == "completed":
                break
            if guard > 30:
                print(json_fail("package did not complete in 30 ticks; state=%s" % state))
                return 1

        remote_head = _run(["git", "rev-parse", "origin/ai-rebuild"], repo)
        local_head = _run(["git", "rev-parse", "HEAD"], repo)
        pushed = remote_head == local_head
        summary = {
            "ok": True,
            "package_state": "completed",
            "ticks": guard,
            "campaign_state": service.get_campaign(principal, campaign.campaign_id).state,
            "package_key": package.key,
            "checkpoints": len(service.checkpoints(principal, campaign.campaign_id)),
            "pushed_to_remote": pushed,
        }
        if not pushed:
            summary["ok"] = False
        print(json_safe(summary))
        return 0 if pushed else 1
    except Exception as error:  # noqa: BLE001
        print(json_fail("%s: %s" % (type(error).__name__, error)))
        return 1
    finally:
        if not args.keep:
            scratch.cleanup()


def json_safe(value):
    import json

    return json.dumps(value, default=str)


def json_fail(message):
    return json_safe({"ok": False, "code": "canary_failed", "message": message})


if __name__ == "__main__":
    sys.exit(main())
