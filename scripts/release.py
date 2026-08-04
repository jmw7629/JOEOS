#!/usr/bin/env python3
"""JoeOS release engineering tool.

Builds a self-contained, versioned release bundle and verifies version
consistency across the authoritative sources. The tool never mutates the
working tree: packaging writes only to the requested output directory, and
``--dry-run`` writes to a temporary directory. ``--check`` performs a
read-only consistency check.

Usage:
    python scripts/release.py --check
    python scripts/release.py --package ./dist
    python scripts/release.py --dry-run
    python scripts/release.py            # check + dry-run package
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from version import ROOT, check_consistency, components, current_version  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT)).decode().strip()[:12]
    except Exception:
        return ""


def _bundle_paths() -> list:
    """Files and directories that make up the self-contained release bundle."""
    entries = [
        ("joeos_backend.py", ROOT / "joeos_backend.py"),
        ("server", ROOT / "server"),
        ("web/index.html", ROOT / "index.html"),
        ("web/manifest.webmanifest", ROOT / "manifest.webmanifest"),
        ("web/sw.js", ROOT / "sw.js"),
        ("web/joeos-icon.svg", ROOT / "joeos-icon.svg"),
        ("sdk/index.js", ROOT / "packages" / "sdk" / "src" / "index.js"),
        ("requirements.txt", ROOT / "requirements.txt"),
        ("launcher/start_joeos.sh", ROOT / "start_joeos.sh"),
        ("launcher/start_joeos_secure.sh", ROOT / "start_joeos_secure.sh"),
        ("docs/RELEASING.md", ROOT / "docs" / "architecture" / "RELEASING.md"),
        ("docs/STATUS.md", ROOT / "STATUS.md"),
    ]
    missing = [label for label, path in entries if not path.exists()]
    if missing:
        raise FileNotFoundError("Release bundle is missing: %s" % ", ".join(missing))
    return entries


def _collect(destination: Path) -> None:
    for label, source in _bundle_paths():
        target = destination / label
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def _write_manifest(destination: Path) -> dict:
    version = current_version()
    files = {}
    for label, source in _bundle_paths():
        if source.is_dir():
            for path in sorted(source.rglob("*")):
                if path.is_file():
                    rel = (Path(label) / path.relative_to(source)).as_posix()
                    files[rel] = _sha256(path)
        else:
            files[label] = _sha256(source)
    manifest = {
        "name": "joeos",
        "version": version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": _source_commit(),
        "components": components(),
        "files": files,
    }
    (destination / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _build_frontend() -> None:
    build = ROOT / "scripts" / "build_frontend.mjs"
    if build.exists():
        subprocess.run(["node", str(build)], cwd=str(ROOT), check=True)


def check() -> int:
    consistent, comps, problems = check_consistency()
    print("Version: %s" % comps["backend"])
    print("  manifest: %s" % comps["manifest"])
    for name, version in comps["packages"].items():
        print("  package %s: %s" % (name, version or "(unversioned)"))
    if problems:
        for problem in problems:
            print("INCONSISTENT: %s" % problem)
        return 1
    print("Consistency: OK")
    return 0


def package(destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    _collect(destination)
    manifest = _write_manifest(destination)
    print("Packaged JoeOS %s into %s" % (manifest["version"], destination))
    print("  %d files · sha256 recorded in release-manifest.json" % len(manifest["files"]))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="JoeOS release engineering tool.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="Verify version consistency (read-only).")
    group.add_argument("--package", metavar="DIR", help="Package a versioned release bundle into DIR.")
    group.add_argument("--dry-run", action="store_true", help="Package into a temporary directory (no repo writes).")
    parser.add_argument("--skip-frontend-build", action="store_true", help="Do not run the frontend build step.")
    args = parser.parse_args()

    if args.check:
        return check()
    if args.package:
        if not args.skip_frontend_build:
            _build_frontend()
        return package(Path(args.package).resolve())
    if args.dry_run:
        if not args.skip_frontend_build:
            _build_frontend()
        with tempfile.TemporaryDirectory(prefix="joeos-release-") as scratch:
            return package(Path(scratch))
    # Default: check then dry-run package (a safe smoke release).
    if check() != 0:
        return 1
    if not args.skip_frontend_build:
        _build_frontend()
    with tempfile.TemporaryDirectory(prefix="joeos-release-") as scratch:
        return package(Path(scratch))


if __name__ == "__main__":
    raise SystemExit(main())
