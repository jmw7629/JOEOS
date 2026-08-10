"""Declarative module system for the JoeOS operating environment.

A ModuleManifest is the stable, cross-platform contract describing a JoeOS
module: identity, navigation, permissions, capabilities, data sources, Joe
context, widgets, layout, and customization eligibility. Browser, iOS, and
Android render manifests through trusted native component registries; user
customization is data (manifests/config), never downloaded executable code.
"""

from .manifest import (
    ModuleManifest,
    ManifestValidationError,
    validate_manifest,
    module_manifest_from,
)
from .catalog import ModuleCatalog

__all__ = [
    "ModuleManifest",
    "ManifestValidationError",
    "validate_manifest",
    "module_manifest_from",
    "ModuleCatalog",
]
