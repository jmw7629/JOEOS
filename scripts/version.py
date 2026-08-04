"""JoeOS version authority.

The release version is defined once in ``joeos_backend.py`` as
``JOEOS_VERSION``. This module reads it and verifies consistency across the
shipped web manifest and the internal package manifests. Package versions are
independent by policy: the web app version (manifest) must match the backend;
the internal packages version independently and are only reported.

Importable by tests and the release tool without a running server.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Tuple

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "joeos_backend.py"
MANIFEST = ROOT / "manifest.webmanifest"
# Internal packages version independently; they are reported, not enforced.
REPORTED_PACKAGES = ("sdk", "plugin-sdk", "shared", "ui")


class VersionError(RuntimeError):
    pass


def current_version() -> str:
    """The authoritative JoeOS release version."""
    source = BACKEND.read_text(encoding="utf-8")
    match = re.search(r'JOEOS_VERSION\s*=\s*"([^"]+)"', source)
    if not match:
        raise VersionError("JOEOS_VERSION is not defined in joeos_backend.py.")
    version = match.group(1).strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise VersionError("JOEOS_VERSION is not a semantic version: %r" % version)
    return version


def manifest_version() -> str:
    try:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VersionError("manifest.webmanifest is not valid JSON: %s" % exc) from exc
    version = str(payload.get("version", "")).strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise VersionError("manifest.webmanifest version is missing or invalid: %r" % version)
    return version


def package_version(name: str) -> str:
    package_json = ROOT / "packages" / name / "package.json"
    if not package_json.exists():
        return ""
    try:
        payload = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VersionError("packages/%s/package.json is invalid: %s" % (name, exc)) from exc
    return str(payload.get("version", "")).strip()


def components() -> Dict[str, Any]:
    """Report every version the release tooling cares about."""
    return {
        "backend": current_version(),
        "manifest": manifest_version(),
        "packages": {name: package_version(name) for name in REPORTED_PACKAGES},
    }


def check_consistency() -> Tuple[bool, Dict[str, Any], list]:
    """Return (consistent, components, problems). The backend version is
    authoritative; the web manifest must match it. Internal packages version
    independently and are reported only."""
    comps = components()
    backend = comps["backend"]
    problems = []
    if comps["manifest"] != backend:
        problems.append("manifest version %s does not match backend %s" % (comps["manifest"], backend))
    return (not problems, comps, problems)
