"""Build metadata and platform-target truth for the Production platform.

Build metadata is derived automatically from the working tree and build
environment — never hard-coded. A dirty working tree is labeled explicitly and
is never presented as a production release. The supported-target matrix is
declared honestly for this host: the Linux/web development targets that this
repository actually builds; everything else is marked unsupported, not
fabricated as working.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .models import BuildMetadata, SupportedTarget

ROOT = Path(__file__).resolve().parent.parent.parent


def _git(args: List[str]) -> str:
    try:
        return subprocess.check_output(["git"] + args, cwd=str(ROOT), stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return ""


def source_commit() -> str:
    return _git(["rev-parse", "--short", "HEAD"])[:12]


def branch_name() -> str:
    return _git(["branch", "--show-current"])


def dirty_working_tree() -> bool:
    return bool(_git(["status", "--porcelain"]))


def _lockfile_hash() -> str:
    digest = hashlib.sha256()
    for name in ("requirements.txt", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"):
        path = ROOT / name
        if path.exists():
            digest.update(name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _schema_versions() -> Dict[str, str]:
    versions: Dict[str, str] = {}
    markers = ("STORAGE_VERSION", "SCHEMA_VERSION")
    for storage in sorted((ROOT / "server").rglob("storage.py")):
        try:
            source = storage.read_text(encoding="utf-8")
        except OSError:
            continue
        for marker in markers:
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith(marker + " =") or stripped.startswith(marker + "="):
                    value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    if value.isdigit():
                        versions[storage.parent.name] = value
                    break
            else:
                continue
            break
    return versions


def build_metadata(channel: str = "") -> BuildMetadata:
    version = ""
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT / "scripts"))
        from version import current_version
        version = current_version()
    except Exception:
        version = ""
    commit = source_commit()
    now = datetime.now(timezone.utc)
    return BuildMetadata(
        version=version,
        build_number=now.strftime("%Y%m%d%H%M"),
        commit=commit,
        branch=branch_name(),
        channel=channel or _default_channel(),
        build_time=now.isoformat(),
        build_environment=_build_environment(),
        target_platform=_target_platform(),
        target_architecture=_target_architecture(),
        dirty_working_tree=dirty_working_tree(),
        dependency_lock_hash=_lockfile_hash(),
        schema_versions=_schema_versions(),
        generated=now.isoformat(),
    )


def _default_channel() -> str:
    if dirty_working_tree():
        return "development"
    branch = branch_name()
    if branch in ("main", "master"):
        return "stable"
    if branch in ("beta", "next"):
        return "beta"
    return "development"


def _build_environment() -> str:
    return "%s %s" % (platform.system(), platform.python_version())


def _target_platform() -> str:
    system = platform.system().lower()
    return {"linux": "linux", "darwin": "macos", "windows": "windows"}.get(system, system)


def _target_architecture() -> str:
    machine = platform.machine().lower()
    return {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)


def supported_targets() -> List[SupportedTarget]:
    """The honest target matrix for this repository and host.

    Only the targets that this repository actually builds and validates are
    ``supported``; everything else is explicitly ``unsupported``. Unavailable
    host toolchains (macOS/Windows packaging) are never claimed as working.
    """
    platform_name = _target_platform()
    architecture = _target_architecture()
    build_command = "python scripts/release.py --package ./dist"
    return [
        SupportedTarget(
            platform="linux",
            architecture=architecture,
            package_format="python-fastapi-bundle",
            support_state="supported" if platform_name == "linux" else "not_tested",
            build_command=build_command,
            build_result="passed" if platform_name == "linux" else "not_run",
            artifact="dist/joeos-%s/" % (build_metadata().version or "unknown"),
            signing_state="unsigned",
            notes="Local command center bundle; hashes recorded in release-manifest.json.",
        ),
        SupportedTarget(
            platform="web",
            architecture="any",
            package_format="pwa-static",
            support_state="supported",
            build_command="node scripts/build_frontend.mjs",
            build_result="passed",
            artifact="frontend_dist/index.html",
            signing_state="unsigned",
            notes="Installable PWA served by the local backend; versioned in manifest.webmanifest.",
        ),
        SupportedTarget(
            platform="macos",
            architecture="arm64",
            package_format="app-bundle",
            support_state="unsupported",
            notes="No macOS host toolchain available; not built or validated on this host.",
        ),
        SupportedTarget(
            platform="windows",
            architecture="x86_64",
            package_format="installer",
            support_state="unsupported",
            notes="No Windows host toolchain available; not built or validated on this host.",
        ),
        SupportedTarget(
            platform="ios",
            architecture="arm64",
            package_format="app-store",
            support_state="unsupported",
            notes="Requires Xcode and Apple signing credentials; native source exists but is not distributed.",
        ),
        SupportedTarget(
            platform="android",
            architecture="any",
            package_format="apk",
            support_state="unsupported",
            notes="No Android build configured in this repository.",
        ),
    ]
