"""Integration gate for the engineering campaign.

Every push must clear the gate: clean working tree, HEAD on the integration
branch, no open blockers, and a passing validation battery. The gate reports
`unknown` (never success) when a check is unmeasured. It is a pure checker with
injected providers so tests can drive every branch deterministically.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple


class IntegrationGate:
    """Verifies pre-push conditions. `git_status` returns a dict with keys
    `branch`, `detached`, `clean` (bool) and `behind`; `tests` returns
    `(passed: bool, detail: str)`; `blockers` returns a list of open blocker
    dicts. Anything unmeasured is reported as `unknown`, never `passed`."""

    def __init__(
        self,
        *,
        git_status: Callable[[], Dict],
        tests: Optional[Callable[[], Tuple[bool, str]]] = None,
        blockers: Optional[Callable[[], List]] = None,
        integration_branch: str = "ai-rebuild",
        require_tests: bool = True,
    ) -> None:
        self._git_status = git_status
        self._tests = tests
        self._blockers = blockers
        self._integration_branch = integration_branch
        self._require_tests = require_tests

    def evaluate(self) -> Dict:
        checks: List[Dict] = []

        def check(name: str, passed: Optional[bool], detail: str) -> None:
            checks.append({"check": name, "passed": passed, "detail": detail})

        status = self._git_status()
        branch = status.get("branch")
        clean = status.get("clean")
        behind = status.get("behind", 0)
        if clean is None:
            check("clean_tree", None, "unmeasured")
        else:
            check("clean_tree", bool(clean), "clean" if clean else "uncommitted changes present")
        if branch is None:
            check("integration_branch", None, "unmeasured")
        else:
            on_branch = branch == self._integration_branch and not status.get("detached", False)
            check("integration_branch", on_branch,
                  "HEAD on %s" % self._integration_branch if on_branch else "HEAD off %s (branch=%s)" % (
                      self._integration_branch, branch))
        if behind and behind > 0:
            check("remote_fresh", False, "behind remote by %d commits" % behind)
        else:
            check("remote_fresh", True if behind is not None else None,
                  "not behind remote" if behind is not None else "unmeasured")
        if self._blockers is not None:
            open_blockers = [b for b in self._blockers() if b.get("state") == "open"]
            check("no_open_blockers", len(open_blockers) == 0,
                  "no open blockers" if not open_blockers else "%d open blocker(s)" % len(open_blockers))
        if self._tests is not None and self._require_tests:
            passed, detail = self._tests()
            check("validation_battery", passed, detail)
        all_passed = all(c["passed"] is True for c in checks)
        has_unknown = any(c["passed"] is None for c in checks)
        return {
            "passed": all_passed and not has_unknown,
            "unknown": has_unknown,
            "checks": checks,
            "detail": "integration gate passed" if all_passed and not has_unknown
            else ("integration gate unmeasured" if has_unknown else "integration gate failed"),
        }
