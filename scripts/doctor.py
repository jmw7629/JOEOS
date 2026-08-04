#!/usr/bin/env python3
"""JoeOS production `doctor` command.

Runs non-destructive diagnostic checks against the JoeOS data directory and
prints a pass / warning / fail / unavailable / unsupported report with
actionable remediation. Never modifies the system unless a repair action is
explicitly invoked (none are implemented here). Exits non-zero if any check is
in a fail state.

Usage:
    python scripts/doctor.py
    JOEOS_DB_PATH=/path/to/joeos.db python scripts/doctor.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

EXIT_PASS = 0
EXIT_WARN = 1
EXIT_FAIL = 2


def _data_root() -> Path:
    configured = os.getenv("JOEOS_DB_PATH", "").strip()
    if configured:
        return Path(configured).expanduser().resolve().parent
    return ROOT / "data"


def main() -> int:
    parser = argparse.ArgumentParser(description="JoeOS production diagnostics.")
    parser.add_argument("--version", action="store_true", help="Print the JoeOS version and exit.")
    args = parser.parse_args()

    from server.production import ProductionService

    if args.version:
        from version import current_version
        print("joeos %s" % current_version())
        return EXIT_PASS

    data_root = _data_root()
    service = ProductionService(str(data_root / "production"), application_version=_version(), data_root=data_root)
    checks = service.doctor()
    worst = 0
    for check in checks:
        state = str(check["state"])
        marker = {"pass": "PASS", "warning": "WARN", "fail": "FAIL", "unavailable": "UNAVAILABLE", "unsupported": "UNSUPPORTED"}.get(state, state.upper())
        print("%-12s %-18s %s" % (marker, check["check"], check["detail"]))
        if state == "fail":
            worst = max(worst, EXIT_FAIL)
        elif state == "warning" and worst < EXIT_WARN:
            worst = EXIT_WARN
    return worst


def _version() -> str:
    try:
        from version import current_version
        return current_version()
    except Exception:
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
