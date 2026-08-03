from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query, status

from .commands import CommandError
from .filesystem import FileConflictError, PathBoundaryError
from .git import GitError
from .models import (
    CommandResult,
    CommandValidation,
    DirectoryListing,
    DocumentState,
    DocumentWriteRequest,
    DocumentWriteResult,
    GitStatus,
    ProjectEnvelope,
    ProjectRecord,
    SearchEnvelope,
    SecretPolicy,
    SecretScanResult,
)
from .projects import ProjectNotFoundError, ProjectPathError
from .service import EngineeringService

router = APIRouter(prefix="/api/v1", tags=["engineering"])


def get_engineering_service(request: Request) -> EngineeringService:
    service = getattr(request.app.state, "engineering_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Engineering workspace service is not initialized.",
        )
    return service


@router.post("/engineering/projects", response_model=ProjectRecord)
def register_project(
    payload: dict,
    service: EngineeringService = Depends(get_engineering_service),
) -> ProjectRecord:
    name = str(payload.get("name") or "")
    root_path = str(payload.get("path") or "")
    if not name or not root_path:
        raise HTTPException(status_code=422, detail="Both 'name' and 'path' are required.")
    try:
        return service.register_project(name, root_path)
    except ProjectPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/engineering/projects", response_model=ProjectEnvelope)
def list_projects(service: EngineeringService = Depends(get_engineering_service)) -> ProjectEnvelope:
    return service.list_projects()


@router.get("/engineering/projects/{project_id}", response_model=ProjectRecord)
def get_project(project_id: str, service: EngineeringService = Depends(get_engineering_service)) -> ProjectRecord:
    try:
        return service.get_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.put("/engineering/projects/{project_id}/trust", response_model=ProjectRecord)
def set_project_trust(
    project_id: str,
    payload: dict,
    service: EngineeringService = Depends(get_engineering_service),
) -> ProjectRecord:
    state = str(payload.get("state") or "")
    if state not in {"untrusted", "session", "trusted"}:
        raise HTTPException(status_code=422, detail="Invalid trust state.")
    try:
        return service.set_project_trust(project_id, state)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.delete("/engineering/projects/{project_id}", status_code=204)
def remove_project(project_id: str, service: EngineeringService = Depends(get_engineering_service)) -> None:
    try:
        service.remove_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.get("/engineering/projects/{project_id}/files", response_model=DirectoryListing)
def list_files(
    project_id: str,
    path: str = Query(default=""),
    include_hidden: bool = Query(default=False),
    service: EngineeringService = Depends(get_engineering_service),
) -> DirectoryListing:
    try:
        return service.list_directory(project_id, path, include_hidden)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except (PathBoundaryError,) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/engineering/projects/{project_id}/files/content", response_model=DocumentState)
def read_file(
    project_id: str,
    path: str = Query(min_length=1),
    service: EngineeringService = Depends(get_engineering_service),
) -> DocumentState:
    try:
        return service.read_document(project_id, path)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except (PathBoundaryError,) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/engineering/projects/{project_id}/files/content", response_model=DocumentWriteResult)
def write_file(
    project_id: str,
    request: DocumentWriteRequest,
    service: EngineeringService = Depends(get_engineering_service),
) -> DocumentWriteResult:
    try:
        return service.write_document(project_id, request)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except (PathBoundaryError, FileConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/engineering/projects/{project_id}/git", response_model=GitStatus)
def git_status(project_id: str, service: EngineeringService = Depends(get_engineering_service)) -> GitStatus:
    try:
        return service.git_status(project_id)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except (GitError,) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/engineering/projects/{project_id}/git/diff")
def git_diff(
    project_id: str,
    path: Optional[str] = Query(default=None),
    staged: bool = Query(default=False),
    service: EngineeringService = Depends(get_engineering_service),
) -> list:
    try:
        return [entry.model_dump() for entry in service.git_diff(project_id, path, staged)]
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except (GitError,) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/engineering/projects/{project_id}/git/stage", status_code=204)
def git_stage(project_id: str, payload: dict, service: EngineeringService = Depends(get_engineering_service)) -> None:
    paths = payload.get("paths") or []
    if not isinstance(paths, list) or not paths:
        raise HTTPException(status_code=422, detail="A non-empty 'paths' list is required.")
    try:
        service.git_stage(project_id, paths)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except (GitError,) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/engineering/projects/{project_id}/git/unstage", status_code=204)
def git_unstage(project_id: str, payload: dict, service: EngineeringService = Depends(get_engineering_service)) -> None:
    paths = payload.get("paths") or []
    if not isinstance(paths, list) or not paths:
        raise HTTPException(status_code=422, detail="A non-empty 'paths' list is required.")
    try:
        service.git_unstage(project_id, paths)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except (GitError,) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/engineering/projects/{project_id}/git/commit", response_model=CommandResult)
def git_commit(
    project_id: str,
    payload: dict,
    service: EngineeringService = Depends(get_engineering_service),
) -> CommandResult:
    message = str(payload.get("message") or "")
    approved = bool(payload.get("approved", False))
    if not message:
        raise HTTPException(status_code=422, detail="A commit message is required.")
    try:
        result = service.git_commit(project_id, message, approved=approved)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except (GitError,) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return CommandResult(
        execution_id=result.commit[:12],
        state="succeeded" if result.committed else "failed",
        stdout=result.commit,
        stderr="",
        exit_code=0,
        risk="low",
    )


@router.get("/engineering/projects/{project_id}/secrets", response_model=SecretScanResult)
def scan_secrets(project_id: str, service: EngineeringService = Depends(get_engineering_service)) -> SecretScanResult:
    try:
        return service.scan_secrets(project_id)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.get("/engineering/secrets/policy", response_model=SecretPolicy)
def secret_policy(service: EngineeringService = Depends(get_engineering_service)) -> SecretPolicy:
    return service._protector.policy()


@router.post("/engineering/projects/{project_id}/commands/validate", response_model=CommandValidation)
def validate_command(
    project_id: str,
    payload: dict,
    service: EngineeringService = Depends(get_engineering_service),
) -> CommandValidation:
    command = str(payload.get("command") or "")
    if not command:
        raise HTTPException(status_code=422, detail="A command is required.")
    try:
        return service.validate_command(project_id, command)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except CommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/engineering/projects/{project_id}/commands/execute", response_model=CommandResult)
def execute_command(
    project_id: str,
    payload: dict,
    service: EngineeringService = Depends(get_engineering_service),
) -> CommandResult:
    command = str(payload.get("command") or "")
    approved = bool(payload.get("approved", False))
    if not command:
        raise HTTPException(status_code=422, detail="A command is required.")
    try:
        return service.execute_command(project_id, command, approved=approved)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
    except (CommandError,) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/engineering/projects/{project_id}/search", response_model=SearchEnvelope)
def search_project(
    project_id: str,
    q: str = Query(min_length=1),
    file_pattern: Optional[str] = Query(default=None),
    service: EngineeringService = Depends(get_engineering_service),
) -> SearchEnvelope:
    try:
        return service.search(project_id, q, file_pattern=file_pattern)
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc


@router.get("/engineering/projects/{project_id}/activity")
def project_activity(
    project_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    service: EngineeringService = Depends(get_engineering_service),
) -> list:
    try:
        return [entry.model_dump() for entry in service.activity(project_id, limit)]
    except (ProjectNotFoundError,) as exc:
        raise HTTPException(status_code=404, detail="Project not found.") from exc
