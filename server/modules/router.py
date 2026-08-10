"""Module catalog API — cross-platform module manifest endpoints."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from server.identity.authority_router import require_application_session

from .catalog import ModuleCatalog
from .manifest import ManifestValidationError, module_manifest_from

router = APIRouter(prefix="/api/v1/modules", tags=["modules"])


def _catalog(request: Request) -> ModuleCatalog:
    catalog = getattr(request.app.state, "module_catalog", None)
    if catalog is None:
        raise HTTPException(status_code=503, detail="module catalog unavailable")
    return catalog


@router.get("")
def list_modules(
    request: Request,
    principal: Dict = Depends(require_application_session),
) -> Dict[str, Any]:
    catalog = _catalog(request)
    return {
        "schema_version": 1,
        "modules": [m.to_dict() for m in catalog.list(include_hidden=True)],
    }


@router.get("/{module_id}")
def get_module(
    module_id: str,
    request: Request,
    principal: Dict = Depends(require_application_session),
) -> Dict[str, Any]:
    catalog = _catalog(request)
    manifest = catalog.get(module_id)
    if manifest is None:
        raise HTTPException(status_code=404, detail="module not found")
    return manifest.to_dict()


@router.post("", status_code=201)
def create_module(
    payload: dict,
    request: Request,
    principal: Dict = Depends(require_application_session),
) -> Dict[str, Any]:
    """Store a validated user/workspace module manifest. Scope defaults to
    'user' (owner-scoped); a workspace-scoped module requires the request body
    to carry scope='workspace' and the principal must own the workspace."""
    catalog = _catalog(request)
    scope = str(payload.get("scope") or "user")
    manifest_payload = payload.get("manifest")
    if not isinstance(manifest_payload, dict):
        raise HTTPException(status_code=422, detail="manifest is required")
    try:
        manifest = module_manifest_from(__import__("json").dumps(manifest_payload))
    except ManifestValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if scope not in ("user", "workspace"):
        raise HTTPException(status_code=422, detail="scope must be user or workspace")
    owner_id = str(principal.get("user", {}).get("id") or "")
    stored = catalog.put(manifest, scope=scope, owner_id=owner_id)
    return stored.to_dict()
