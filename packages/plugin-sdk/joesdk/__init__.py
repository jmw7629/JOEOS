"""JoeOS Plugin SDK.

A bounded, versioned SDK for building plugins that run inside the isolated
Extension Host. The SDK exposes typed manifest helpers, integrity and
packaging utilities, and the host entry protocol. It never imports JoeOS core
internals.
"""

from __future__ import annotations

from .manifest import (
    API_VERSION,
    PluginManifest,
    contribution,
    dependency,
    manifest,
    permission,
    setting,
)
from .packaging import (
    calculate_integrity,
    package_plugin,
    validate_manifest,
    validate_package,
)

__all__ = [
    "API_VERSION",
    "PluginManifest",
    "calculate_integrity",
    "contribution",
    "dependency",
    "manifest",
    "package_plugin",
    "permission",
    "setting",
    "validate_manifest",
    "validate_package",
]
