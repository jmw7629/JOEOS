from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


WidgetState = Literal["ready", "integration_required"]
Density = Literal["compact", "comfortable", "spacious"]
FontFamily = Literal["system", "rounded", "monospace", "serif"]


class WidgetSize(BaseModel):
    columns: int = Field(ge=1, le=12)
    rows: int = Field(ge=1, le=12)


class WorkspaceTheme(BaseModel):
    font_scale: float = Field(default=1.0, ge=0.75, le=1.5)
    font_family: FontFamily = "system"
    accent_hex: str = Field(default="#31D7FF", pattern=r"^#[0-9A-Fa-f]{6}$")
    text_hex: str = Field(default="#EAF6FF", pattern=r"^#[0-9A-Fa-f]{6}$")
    canvas_hex: str = Field(default="#050912", pattern=r"^#[0-9A-Fa-f]{6}$")
    density: Density = "comfortable"
    radius: int = Field(default=18, ge=0, le=32)
    glass_opacity: float = Field(default=0.72, ge=0.2, le=1.0)

    @field_validator("accent_hex", "text_hex", "canvas_hex")
    @classmethod
    def normalize_accent(cls, value: str) -> str:
        return value.upper()


class WidgetDefinition(BaseModel):
    id: str
    version: int = Field(ge=1)
    name: str
    description: str
    category: str
    icon: str
    state: WidgetState
    default_size: WidgetSize
    min_size: WidgetSize
    max_size: WidgetSize
    integration: Optional[str] = None


class WidgetInstance(BaseModel):
    instance_id: str
    widget_id: str
    widget_version: int = Field(ge=1)
    order: int = Field(ge=0)
    size: WidgetSize
    visible: bool = True
    settings: Dict[str, Any] = Field(default_factory=dict)


class WidgetInstanceUpdate(BaseModel):
    instance_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    widget_id: str = Field(min_length=1, max_length=100)
    widget_version: int = Field(ge=1)
    order: Optional[int] = Field(default=None, ge=0)
    size: WidgetSize
    visible: bool = True
    settings: Dict[str, Any] = Field(default_factory=dict)


class Workspace(BaseModel):
    id: str
    name: str
    revision: int = Field(ge=1)
    theme: WorkspaceTheme
    widgets: List[WidgetInstance]


class WorkspaceEnvelope(BaseModel):
    workspace: Workspace
    catalog: List[WidgetDefinition]
    catalog_version: int = Field(ge=1)


class WorkspaceUpdate(BaseModel):
    revision: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    theme: WorkspaceTheme
    widgets: List[WidgetInstanceUpdate] = Field(max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Workspace name cannot be blank.")
        return normalized


class ConfigurationGuideRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Configuration request cannot be blank.")
        return normalized


class ConfigurationChange(BaseModel):
    kind: Literal["theme", "widget"]
    action: Literal["set", "add", "show", "hide", "resize", "reorder"]
    field: Optional[str] = None
    widget_id: Optional[str] = None
    description: str


class ConfigurationProposal(BaseModel):
    proposal_id: str
    based_on_revision: int = Field(ge=1)
    summary: str
    theme: WorkspaceTheme
    widgets: List[WidgetInstanceUpdate]
    changes: List[ConfigurationChange]
    integration_requirements: List[str]
    warnings: List[str]
    requires_confirmation: bool = True
    secret_notice: str


class ConfigurationProposalEnvelope(BaseModel):
    proposal: ConfigurationProposal
