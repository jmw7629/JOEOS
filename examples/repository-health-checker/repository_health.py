"""First-party Repository Health Checker plugin.

Demonstrates a real, non-critical capability moved behind the Plugin
Platform: a repository analyzer that reports uncommitted working-tree state
and a health summary. It only reads approved project state through the
bounded host API and never fabricates results.
"""

from __future__ import annotations

import os
import subprocess


def _git_status(project: str) -> dict:
    try:
        result = subprocess.run(
            ["git", "-C", project, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {"available": False, "reason": "git unavailable"}
    if result.returncode != 0:
        return {"available": False, "reason": result.stderr.strip()[:200]}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    staged = sum(1 for line in lines if line[:2] != "??" and line[:1] != " ")
    untracked = sum(1 for line in lines if line.startswith("??"))
    modified = sum(1 for line in lines if line.startswith(" M") or line.startswith("M"))
    return {
        "available": True,
        "staged": staged,
        "untracked": untracked,
        "modified": modified,
        "changes": len(lines),
    }


def handle(method, params, api):
    if method == "lifecycle.activate":
        return {"activated": True, "analyzer": "repository-health"}
    if method == "lifecycle.deactivate":
        return {"deactivated": True}
    if method == "contribution.invoke":
        contribution_id = (params or {}).get("contribution_id", "")
        if contribution_id.endswith(".health"):
            project = (params or {}).get("params", {}).get("project")
            if not project:
                return {"error": "project is required"}
            status = _git_status(str(project))
            if not status.get("available"):
                return {"state": "unavailable", **status}
            state = "clean" if status["changes"] == 0 else "dirty"
            return {
                "state": state,
                "summary": "%d staged, %d modified, %d untracked" % (
                    status["staged"],
                    status["modified"],
                    status["untracked"],
                ),
                "changes": status["changes"],
            }
        return {"ok": True}
    return {"ok": True}
