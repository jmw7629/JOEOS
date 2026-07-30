"""Workspace and widget configuration domain."""

from .router import router as workspace_router
from .service import WorkspaceService

__all__ = ["WorkspaceService", "workspace_router"]
