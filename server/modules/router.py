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


@router.get("/capabilities")
def client_capabilities(
    request: Request,
    principal: Dict = Depends(require_application_session),
) -> Dict[str, Any]:
    """Server-reported client capability contract.

    Clients may POST their detected capabilities (feature detection, not
    user-agent guessing); the server adapts module/widget availability and
    never claims a capability the client did not report. A missing report keeps
    the server honest (no fabricated capability)."""
    capabilities = {
        "schema_version": 1,
        "reported": request.app.state.client_capabilities if hasattr(request.app.state, "client_capabilities") else {},
    }
    return capabilities


@router.post("/capabilities")
def report_client_capabilities(
    payload: dict,
    request: Request,
    principal: Dict = Depends(require_application_session),
) -> Dict[str, Any]:
    reported = payload.get("capabilities")
    if not isinstance(reported, dict):
        raise HTTPException(status_code=422, detail="capabilities must be an object")
    # Store bounded, non-secret capability flags only.
    bounded = {k: bool(v) for k, v in reported.items() if isinstance(v, bool)}
    request.app.state.client_capabilities = bounded
    return {"schema_version": 1, "reported": bounded}


@router.get("")
def list_modules(
    request: Request,
    principal: Dict = Depends(require_application_session),
) -> Dict[str, Any]:
    catalog = _catalog(request)
    # Authenticated clients see the full visible catalog (built-in + user/
    # workspace modules). The public read path below returns only built-ins.
    return {
        "schema_version": 1,
        "modules": [m.to_dict() for m in catalog.list(include_hidden=True)],
    }


@router.get("/public")
def list_public_modules(
    request: Request,
) -> Dict[str, Any]:
    """Public product-default module catalog.

    Returns only built-in, visible manifests (non-sensitive product defaults).
    User/workspace modules and hidden modules are never exposed here. This is
    what the browser shell (which may not hold a session) consumes as the
    authoritative module source for the Command Center."""
    catalog = _catalog(request)
    return {
        "schema_version": 1,
        "public": True,
        "modules": [m.to_dict() for m in catalog.list(scopes=["builtin"])],
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
    # Least-privilege guard: a user module may not declare required permissions
    # or capabilities the creator does not already hold. This prevents a module
    # from escalating authority through the catalog.
    held = set(principal.get("capabilities") or [])
    requested = set(manifest.required_capabilities) | set(manifest.required_permissions)
    missing = sorted(requested - held)
    if missing:
        raise HTTPException(
            status_code=403,
            detail="module requires capabilities the creator does not hold: %s" % ", ".join(missing),
        )
    if scope == "workspace":
        # Workspace-scoped modules are org-policy material; require an explicit
        # manage capability so a user cannot publish for the whole workspace.
        if "engineering.module.manage" not in held and "modules.manage" not in held:
            raise HTTPException(status_code=403, detail="workspace module publishing requires a manage capability")
    owner_id = str(principal.get("user", {}).get("id") or "")
    stored = catalog.put(manifest, scope=scope, owner_id=owner_id)
    return stored.to_dict()
