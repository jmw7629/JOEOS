from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .models import BootstrapDocument

router = APIRouter(prefix="/api/v1")


@router.get("/bootstrap", response_model=BootstrapDocument)
def get_bootstrap(request: Request) -> Dict[str, Any]:
    service = request.app.state.bootstrap_service
    document = service.discover()
    response = JSONResponse(
        status_code=200,
        content=document.model_dump(mode="json"),
        media_type="application/json",
    )
    response.headers["Cache-Control"] = "no-store"
    return response
