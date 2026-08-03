"""REST API for the JoeOS Plugin and Extension Platform.

Every endpoint reads real plugin state; nothing is fabricated. Mutating
operations run through the authoritative lifecycle and permission services.
Secret values are never returned by this API.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from .models import (
    HealthRecord,
    PluginManifest,
    PluginOverview,
    PluginRecord,
    PublisherRecord,
)
from .service import PluginService

router = APIRouter(prefix="/api/v1/plugins", tags=["plugins"])


def get_plugin_service(request: Request) -> PluginService:
    service = getattr(request.app.state, "plugins_service", None)
    if service is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Plugin service is not initialized.")
    return service


def _require(service: PluginService, plugin_id: str) -> PluginRecord:
    record = service.get(plugin_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Plugin not found.")
    return record


# ---- overview ----

@router.get("/overview", response_model=PluginOverview)
def overview(service: PluginService = Depends(get_plugin_service)) -> PluginOverview:
    return service.overview()


# ---- plugins ----

@router.get("", response_model=List[PluginRecord])
def list_plugins(service: PluginService = Depends(get_plugin_service)) -> List[PluginRecord]:
    return list(service.list())


@router.get("/health")
def plugins_health(service: PluginService = Depends(get_plugin_service)) -> dict:
    return {"plugins": [record.model_dump() for record in service.health_records()]}


@router.get("/contributions")
def all_contributions(service: PluginService = Depends(get_plugin_service)) -> dict:
    return {"contributions": [record.model_dump() for record in service.active_contributions()]}


@router.get("/publishers")
def publishers(service: PluginService = Depends(get_plugin_service)) -> dict:
    return {"publishers": [record.model_dump() for record in service.publisher_list()]}


@router.get("/storage")
def platform_storage(service: PluginService = Depends(get_plugin_service)) -> dict:
    return service.storage_stats_global()


@router.post("/backup")
def backup(service: PluginService = Depends(get_plugin_service)) -> dict:
    path = service.backup()
    return {"backup_path": path}


@router.post("/development/link")
def link_development(
    payload: DevLinkRequest,
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    path = service.development_link(payload.plugin_id, payload.source_dir)
    return {"linked": payload.plugin_id, "path": path}


@router.post("/development/unlink")
def unlink_development(
    payload: DevLinkRequest,
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    service.development_unlink(payload.plugin_id)
    return {"unlinked": payload.plugin_id}


@router.get("/{plugin_id}", response_model=PluginRecord)
def get_plugin(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> PluginRecord:
    return _require(service, plugin_id)


@router.post("/install", status_code=status.HTTP_201_CREATED)
def install_plugin(payload: InstallRequest, service: PluginService = Depends(get_plugin_service)) -> PluginRecord:
    if payload.source == "directory":
        return service.install_directory(payload.package_path, source="local_development")
    return service.install_package(
        payload.package_path,
        source=payload.source,
        approval=payload.approval,
    )


@router.post("/{plugin_id}/enable")
def enable_plugin(
    plugin_id: str,
    payload: EnableRequest,
    service: PluginService = Depends(get_plugin_service),
) -> PluginRecord:
    return service.enable(plugin_id, scope=payload.scope, workspace=payload.workspace, project=payload.project)


@router.post("/{plugin_id}/disable")
def disable_plugin(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> PluginRecord:
    return service.disable(plugin_id)


@router.post("/{plugin_id}/activate")
def activate_plugin(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> PluginRecord:
    return service.activate(plugin_id)


@router.post("/{plugin_id}/deactivate")
def deactivate_plugin(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> PluginRecord:
    return service.deactivate(plugin_id)


@router.post("/{plugin_id}/uninstall")
def uninstall_plugin(
    plugin_id: str,
    payload: UninstallRequest,
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    service.uninstall(plugin_id, delete_data=payload.delete_data)
    return {"status": "removed", "plugin_id": plugin_id}


@router.post("/{plugin_id}/update")
def update_plugin(
    plugin_id: str,
    payload: UpdateRequest,
    service: PluginService = Depends(get_plugin_service),
) -> PluginRecord:
    _require(service, plugin_id)
    return service.update(plugin_id, payload.package_path, approval=payload.approval)


@router.post("/{plugin_id}/rollback")
def rollback_plugin(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> PluginRecord:
    return service.rollback(plugin_id)


@router.post("/{plugin_id}/quarantine")
def quarantine_plugin(
    plugin_id: str,
    payload: QuarantineRequest,
    service: PluginService = Depends(get_plugin_service),
) -> PluginRecord:
    return service.quarantine_plugin(plugin_id, payload.reason)


@router.post("/{plugin_id}/restore")
def restore_plugin(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> PluginRecord:
    return service.restore(plugin_id)


@router.post("/safe-mode/enter")
def enter_safe_mode(service: PluginService = Depends(get_plugin_service)) -> dict:
    service.enter_safe_mode()
    return {"safe_mode": True}


@router.post("/safe-mode/exit")
def exit_safe_mode(service: PluginService = Depends(get_plugin_service)) -> dict:
    service.exit_safe_mode()
    return {"safe_mode": False}


# ---- permissions ----

@router.get("/{plugin_id}/permissions")
def plugin_permissions(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return {
        "summary": service.permission_summary(plugin_id).model_dump(),
        "grants": [grant.model_dump() for grant in service.permission_grants(plugin_id)],
    }


@router.post("/{plugin_id}/permissions/grant")
def grant_permission(
    plugin_id: str,
    payload: GrantRequest,
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    _require(service, plugin_id)
    service.grant_permission(plugin_id, payload.permission, scope=payload.scope, scope_target=payload.scope_target)
    return {"granted": payload.permission}


@router.post("/{plugin_id}/permissions/revoke")
def revoke_permission(
    plugin_id: str,
    payload: GrantRequest,
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    _require(service, plugin_id)
    service.revoke_permission(plugin_id, payload.permission, scope_target=payload.scope_target)
    return {"revoked": payload.permission}


# ---- contributions ----

@router.get("/{plugin_id}/contributions")
def plugin_contributions(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return {"contributions": [record.model_dump() for record in service.contribution_list(plugin_id)]}


@router.post("/{plugin_id}/contributions/{contribution_id}/invoke")
def invoke_contribution(
    plugin_id: str,
    contribution_id: str,
    payload: InvokeRequest,
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    return service.invoke_contribution(plugin_id, contribution_id, payload.params)


# ---- storage / settings / secrets / events ----

@router.get("/{plugin_id}/storage")
def plugin_storage(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return service.storage_stats(plugin_id)


@router.get("/{plugin_id}/settings")
def plugin_settings(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return {"settings": service.settings_all(plugin_id)}


@router.put("/{plugin_id}/settings/{key}")
def set_setting(
    plugin_id: str,
    key: str,
    payload: SettingRequest,
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    _require(service, plugin_id)
    value = service.set_setting(plugin_id, key, payload.value)
    return {"key": key, "value": value}


@router.get("/{plugin_id}/secrets")
def plugin_secrets(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return {"references": list(service.secret_references(plugin_id))}


@router.post("/{plugin_id}/secrets")
def set_secret(
    plugin_id: str,
    payload: SecretRequest,
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    _require(service, plugin_id)
    return service.set_secret(plugin_id, payload.name, payload.value)


@router.delete("/{plugin_id}/secrets/{name}")
def delete_secret(plugin_id: str, name: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    service.revoke_secret(plugin_id, name)
    return {"revoked": name}


@router.get("/{plugin_id}/events")
def plugin_events(
    plugin_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    _require(service, plugin_id)
    return {"events": list(service.event_recent(plugin_id, limit=limit))}


# ---- health / diagnostics ----

@router.get("/{plugin_id}/health", response_model=HealthRecord)
def plugin_health(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> HealthRecord:
    _require(service, plugin_id)
    return service.health_record(plugin_id)


@router.get("/{plugin_id}/logs")
def plugin_logs(
    plugin_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    _require(service, plugin_id)
    return {"logs": [record.model_dump() for record in service.logs(plugin_id, limit=limit)]}


@router.get("/{plugin_id}/logs/export")
def export_logs(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return {"logs": service.export_logs(plugin_id)}


@router.get("/{plugin_id}/activity")
def plugin_activity(
    plugin_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    service: PluginService = Depends(get_plugin_service),
) -> dict:
    _require(service, plugin_id)
    return {"activity": list(service.activity(plugin_id, limit=limit))}


@router.get("/{plugin_id}/resources")
def plugin_resources(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return service.resource_snapshot(plugin_id)


# ---- publishers / compatibility / integrity / updates ----

@router.post("/publishers/{publisher_id}/trust")
def set_publisher_trust(
    publisher_id: str,
    payload: TrustRequest,
    service: PluginService = Depends(get_plugin_service),
) -> PublisherRecord:
    return service.set_publisher_trust(publisher_id, payload.trusted)


@router.get("/{plugin_id}/compatibility")
def plugin_compatibility(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return service.compatibility(plugin_id)


@router.post("/{plugin_id}/verify-integrity")
def verify_integrity(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return service.verify_integrity(plugin_id)


@router.get("/{plugin_id}/update-history")
def update_history(plugin_id: str, service: PluginService = Depends(get_plugin_service)) -> dict:
    _require(service, plugin_id)
    return {"history": list(service.update_history(plugin_id))}


# ---- request models ----

from pydantic import BaseModel, Field  # noqa: E402


class InstallRequest(BaseModel):
    package_path: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="local_package", max_length=60)
    approval: Optional[dict] = None


class EnableRequest(BaseModel):
    scope: str = Field(default="global", max_length=40)
    workspace: str = Field(default="", max_length=120)
    project: str = Field(default="", max_length=120)


class UninstallRequest(BaseModel):
    delete_data: bool = False


class UpdateRequest(BaseModel):
    package_path: str = Field(min_length=1, max_length=2000)
    approval: Optional[dict] = None


class QuarantineRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class GrantRequest(BaseModel):
    permission: str = Field(min_length=1, max_length=100)
    scope: str = Field(default="granted_global", max_length=40)
    scope_target: str = Field(default="", max_length=240)


class InvokeRequest(BaseModel):
    params: dict = Field(default_factory=dict)


class SettingRequest(BaseModel):
    value: object = None


class SecretRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=4000)


class TrustRequest(BaseModel):
    trusted: bool = True


class DevLinkRequest(BaseModel):
    plugin_id: str = Field(min_length=1, max_length=100)
    source_dir: str = Field(min_length=1, max_length=2000)