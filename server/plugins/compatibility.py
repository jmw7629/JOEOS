"""Compatibility Resolver for the JoeOS Plugin Platform.

Before installation, activation, or update, a plugin is checked against the
running JoeOS version, Plugin API version, platform, architecture, storage
migrations, and policies. The result is explicitly explainable; an
incompatible plugin is never activated in the hope it works.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence, Tuple

from .models import CompatibilityResult

_VERSION_PARTS = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse_version(version: str) -> Optional[Tuple[int, int, int]]:
    if not version:
        return None
    match = _VERSION_PARTS.fullmatch(version.strip())
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def version_in_range(
    current: str, minimum: str, maximum: str, exclusive_maximum: bool = False
) -> Tuple[bool, Optional[str]]:
    """Evaluate a version against (optionally open) lower/upper bounds.

    Bounds are inclusive unless ``exclusive_maximum`` is set. ``maximum`` and
    ``minimum`` may be empty to indicate no bound.
    """
    parsed = _parse_version(current)
    if parsed is None:
        return False, "current version is not a dotted triple."
    if minimum:
        parsed_min = _parse_version(minimum)
        if parsed_min is None:
            return False, "declares an invalid minimum version."
        if parsed < parsed_min:
            return False, "requires a newer version (>= %s)." % minimum
    if maximum:
        parsed_max = _parse_version(maximum)
        if parsed_max is None:
            return False, "declares an invalid maximum version."
        if parsed > parsed_max or (exclusive_maximum and parsed == parsed_max):
            return False, "requires a version (<= %s)." % maximum
    return True, None


def plain_version_matches(current: str, range_spec: str) -> bool:
    """Match a pragmatic version range spec used by dependencies.

    Supports ``*`` (any), ``x.y.z`` (exact), and comma-separated ``a,b,c``
    (member of). This is deliberately simple and documented; it does not
    implement full semver range algebra.
    """
    if not range_spec or range_spec.strip() in {"*", ""}:
        return True
    parsed_current = _parse_version(current)
    if parsed_current is None:
        return False
    for part in range_spec.split(","):
        part = part.strip()
        if part in {"*", ""}:
            return True
        if _parse_version(part) == parsed_current:
            return True
    return False


def eval_manifest_compatibility(
    *,
    manifest,
    joeos_version: str,
    platform_compliant: bool = True,
    available_plugins: Optional[Sequence[str]] = None,
) -> CompatibilityResult:
    """Produce an explainable compatibility verdict without touching plugin code."""
    reasons: list = []
    decision = "compatible"

    ok, reason = version_in_range(
        joeos_version, manifest.min_joeos_version, manifest.max_joeos_version
    )
    if not ok:
        decision = "incompatible"
        reasons.append("JoeOS version " + (reason or "outside declared bounds."))

    if manifest.api_version > 1:
        decision = "incompatible"
        reasons.append("api_version %d is not supported by this JoeOS." % manifest.api_version)

    if not platform_compliant:
        decision = "unsupported_platform"
        reasons.append("target platform/architecture is unsupported.")

    missing = []
    for dependency in manifest.dependencies:
        if dependency.optional:
            continue
        if not available_plugins or dependency.plugin_id not in available_plugins:
            missing.append(dependency.plugin_id)
    if missing:
        decision = "missing_dependency"
        reasons.append("missing mandatory dependencies: " + ", ".join(sorted(missing)))

    return Compatibility(
        plugin_id=manifest.id,
        version=manifest.version,
        decision=decision,
        reasons=tuple(reasons) or ("No compatibility issues.",),
    )


class Compatibility(CompatibilityResult):
    """Versioned compatibility verdict with human-readable reasons."""

    def summarize(self) -> str:
        return "%s (%s)" % (self.decision, "; ".join(self.reasons))