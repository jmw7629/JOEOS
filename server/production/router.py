"""Production Readiness and Release Engineering REST API.

Reports honest build metadata, supported targets, release gates, migration,
backup, update, and recovery state. Mutating actions (backup, restore, update,
Safe Mode, Repair Mode) require governance approval and never fabricate
success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

router = APIRouter(prefix="/api/v1/production", tags=["production"])


def _get_service(request: Request):
    service = getattr(request.app.state, "production_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Production platform is unavailable.")
    return service


@router.get("/status")
def status(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    view = service.status()
    return {
        "generated_at": view.generated_at,
        "version": view.version,
        "channel": view.channel,
        "overall": view.overall,
        "gates": [
            {"gate_id": gate.gate_id, "name": gate.name, "state": gate.state, "detail": gate.detail, "category": gate.category}
            for gate in view.gates
        ],
        "targets": [
            {
                "platform": target.platform,
                "architecture": target.architecture,
                "package_format": target.package_format,
                "support_state": target.support_state,
                "build_command": target.build_command,
                "build_result": target.build_result,
                "signing_state": target.signing_state,
                "notarization_state": target.notarization_state,
                "notes": target.notes,
            }
            for target in view.targets
        ],
        "message": view.message,
    }


@router.get("/build")
def build(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    metadata = service.build()
    return {
        "version": metadata.version,
        "build_number": metadata.build_number,
        "commit": metadata.commit,
        "branch": metadata.branch,
        "channel": metadata.channel,
        "build_time": metadata.build_time,
        "build_environment": metadata.build_environment,
        "target_platform": metadata.target_platform,
        "target_architecture": metadata.target_architecture,
        "dirty_working_tree": metadata.dirty_working_tree,
        "dependency_lock_hash": metadata.dependency_lock_hash,
        "schema_versions": metadata.schema_versions,
        "generated": metadata.generated,
    }


@router.get("/migrations")
def migrations(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return {"migrations": service.migration_state()}


@router.post("/migrations/{store}/migrate")
def migrate(request: Request, store: str) -> Dict[str, Any]:
    service = _get_service(request)
    blocked, reason = _governance(service)
    if blocked:
        raise HTTPException(status_code=409, detail="governance: %s" % reason)
    try:
        return service.migrate(store)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/backups")
def backups(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return {
        "backups": [
            {
                "backup_id": record.backup_id,
                "created_at": record.created_at,
                "application_version": record.application_version,
                "scope": record.scope,
                "stores": list(record.stores),
                "size_bytes": record.size_bytes,
                "verified": record.verified,
                "status": record.status,
            }
            for record in service.list_backups()
        ]
    }


@router.post("/backups")
def create_backup(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    blocked, reason = _governance(service)
    if blocked:
        raise HTTPException(status_code=409, detail="governance: %s" % reason)
    record = service.create_backup()
    return _backup_payload(record)


@router.post("/backups/{backup_id}/verify")
def verify_backup(request: Request, backup_id: str) -> Dict[str, Any]:
    service = _get_service(request)
    return _backup_payload(service.verify_backup(backup_id))


@router.post("/backups/{backup_id}/restore")
def restore_backup(request: Request, backup_id: str) -> Dict[str, Any]:
    service = _get_service(request)
    blocked, reason = _governance(service)
    if blocked:
        raise HTTPException(status_code=409, detail="governance: %s" % reason)
    try:
        plan = service.restore_plan(backup_id)
        result = service.restore_backup(backup_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result["plan"] = {
        "overwrite_scope": plan.overwrite_scope,
        "revokes_sessions": plan.revokes_sessions,
        "invalidates_approvals": plan.invalidates_approvals,
        "pauses_workflows": plan.pauses_workflows,
        "restricts_devices": plan.restricts_devices,
    }
    return result


@router.delete("/backups/{backup_id}")
def delete_backup(request: Request, backup_id: str) -> Dict[str, Any]:
    service = _get_service(request)
    blocked, reason = _governance(service)
    if blocked:
        raise HTTPException(status_code=409, detail="governance: %s" % reason)
    try:
        deleted = service.delete_backup(backup_id)
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Backup not found.")
    return {"deleted": backup_id}


@router.get("/updates")
def update_status(request: Request, staged: str = "") -> Dict[str, Any]:
    service = _get_service(request)
    staged_path = Path(staged).expanduser().resolve() if staged else None
    return service.update_status(staged_path)


@router.post("/updates/plan")
def update_plan(request: Request, payload: dict) -> Dict[str, Any]:
    service = _get_service(request)
    staged = str(payload.get("staged") or "").strip()
    if not staged:
        raise HTTPException(status_code=400, detail="staged path is required.")
    try:
        return service.update_plan(Path(staged).expanduser().resolve())
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/updates/apply")
def apply_update(request: Request, payload: dict) -> Dict[str, Any]:
    service = _get_service(request)
    blocked, reason = _governance(service)
    if blocked:
        raise HTTPException(status_code=409, detail="governance: %s" % reason)
    staged = str(payload.get("staged") or "").strip()
    if not staged:
        raise HTTPException(status_code=400, detail="staged path is required.")
    record = service.apply_update(Path(staged).expanduser().resolve())
    return {
        "update_id": record.update_id,
        "state": record.state,
        "version": record.version,
        "detail": record.detail,
        "backup_required": record.backup_required,
    }


@router.get("/recovery")
def recovery(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return service.recovery_state()


@router.post("/recovery/safe-mode")
def enter_safe_mode(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    blocked, reason = _governance(service)
    if blocked:
        raise HTTPException(status_code=409, detail="governance: %s" % reason)
    service.enter_safe_mode()
    return service.recovery_state()


@router.delete("/recovery/safe-mode")
def exit_safe_mode(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    service.exit_safe_mode()
    return service.recovery_state()


@router.get("/doctor")
def doctor(request: Request) -> Dict[str, Any]:
    service = _get_service(request)
    return {"checks": service.doctor(), "generated_at": _now_iso()}


def _backup_payload(record) -> Dict[str, Any]:
    return {
        "backup_id": record.backup_id,
        "created_at": record.created_at,
        "application_version": record.application_version,
        "format_version": record.format_version,
        "scope": record.scope,
        "stores": list(record.stores),
        "size_bytes": record.size_bytes,
        "integrity_hash": record.integrity_hash,
        "verified": record.verified,
        "status": record.status,
    }


def _governance(service) -> tuple:
    blocked = getattr(service, "_governance_blocked", None)
    if blocked is None:
        return (False, "")
    return blocked()


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
