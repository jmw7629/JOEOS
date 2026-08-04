"""Typed models for the JoeOS Production Readiness and Release Engineering
platform.

Everything is either measured/derived from the actual build and services
(never fabricated) or declared policy. Release gates are explicit and honest:
a gate is passing, warning, blocking, unavailable, unsupported, or not
configured — never an opaque score. Unknown is never shown as passing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class BuildMetadata:
    version: str = ""
    build_number: str = ""
    commit: str = ""
    branch: str = ""
    channel: str = "development"
    build_time: str = ""
    build_environment: str = ""
    target_platform: str = ""
    target_architecture: str = ""
    dirty_working_tree: bool = False
    dependency_lock_hash: str = ""
    schema_versions: Dict[str, str] = field(default_factory=dict)
    generated: str = ""


@dataclass(frozen=True)
class SupportedTarget:
    platform: str
    architecture: str
    package_format: str
    support_state: str = "unsupported"  # supported | planned | experimental | unsupported | not_tested
    build_command: str = ""
    build_result: str = ""
    artifact: str = ""
    signing_state: str = "unsigned"
    notarization_state: str = "not_applicable"
    notes: str = ""


@dataclass(frozen=True)
class ReleaseGate:
    gate_id: str
    name: str
    state: str = "unknown"  # passing | warning | blocking | unavailable | unsupported | not_configured | unknown
    detail: str = ""
    category: str = "validation"


@dataclass(frozen=True)
class ReleaseStatus:
    generated_at: str = ""
    version: str = ""
    channel: str = ""
    overall: str = "unknown"  # passing | warning | blocking | unavailable | unknown
    gates: Tuple[ReleaseGate, ...] = ()
    targets: Tuple[SupportedTarget, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class CompatibilityCheck:
    component: str
    current_version: str = ""
    required_version: str = ""
    minimum_supported: str = ""
    maximum_compatible: str = ""
    state: str = "compatible"  # compatible | compatible_with_warning | update_required | incompatible | unknown
    detail: str = ""


@dataclass(frozen=True)
class MigrationRecord:
    migration_id: str
    store: str
    source_version: int = 0
    target_version: int = 0
    status: str = "pending"  # pending | running | completed | failed | skipped
    created_at: str = ""
    backed_up: bool = False
    detail: str = ""


@dataclass(frozen=True)
class MigrationState:
    store: str = ""
    current_schema: int = 0
    target_schema: int = 0
    compatible: bool = True
    needs_migration: bool = False
    future_schema: bool = False
    locked: bool = False
    detail: str = ""


@dataclass(frozen=True)
class BackupRecord:
    backup_id: str
    created_at: str = ""
    application_version: str = ""
    format_version: int = 1
    scope: str = "full"
    stores: Tuple[str, ...] = ()
    size_bytes: int = 0
    integrity_hash: str = ""
    verified: bool = False
    encrypted: bool = False
    destination: str = "local"
    status: str = "created"  # created | verified | verification_failed | failed
    restore_compatible: bool = True
    excluded: Tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class RestorePlan:
    backup_id: str = ""
    stores: Tuple[str, ...] = ()
    overwrite_scope: str = "full"
    requires_migration: bool = False
    revokes_sessions: bool = True
    invalidates_approvals: bool = True
    pauses_workflows: bool = True
    restricts_devices: bool = True
    expected_risk: str = ""


@dataclass(frozen=True)
class UpdateRecord:
    update_id: str
    channel: str = "development"
    state: str = "idle"  # idle | checking | update_available | downloading | verifying | preparing | awaiting_approval | installing | validating | completed | failed | rollback_available
    version: str = ""
    artifact_hash: str = ""
    manifest_match: bool = False
    compatibility_ok: bool = True
    backup_required: bool = True
    detail: str = ""
    created_at: str = ""


@dataclass(frozen=True)
class RecoveryState:
    safe_mode: bool = False
    repair_mode: bool = False
    crash_loop_detected: bool = False
    interrupted_update: bool = False
    interrupted_migration: bool = False
    corrupted_cache: bool = False
    locked_database: bool = False
    low_disk: bool = False
    detail: str = ""
    generated_at: str = ""
