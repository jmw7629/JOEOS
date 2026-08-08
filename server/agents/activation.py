"""Production Agent Fabric activation.

Idempotently registers the local Ollama provider, synchronizes the live Ollama
model inventory into the authoritative ModelRegistry, creates the production
agent team (Joe, Architect, Builder, Researcher, Verifier, Security), binds each
agent to a real installed model, and registers only safe foundational read
tools in the ToolBroker.

Everything here flows through the authoritative control plane (ActionService);
nothing is fabricated. Models that are installed in Ollama but missing from the
registry are registered; models that disappear are marked disabled (historical
records are never deleted). Provider and model binding is local-first and
private; cloud fallback is never automatic.
"""

from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional, Sequence
from uuid import UUID

from server.actions.service import ActionDeniedError, ActionService

JOEOS_PROVIDER_KEY = "ollama"
JOEOS_PROVIDER_DISPLAY = "Ollama (local)"

# Model binding: preferred model key per agent, with an authorized local
# fallback. Keys must match the live Ollama inventory; unknown keys are simply
# not used (the selection layer reports the actually-used model).
#
# Resource constraint (measured on the 2-vCPU / 7.8 GiB VPS): 14B models are
# OOM-killed at load, and the 7B family only stays resident in isolation. Under
# the combined backend+runtime load (e.g. a real run while the server is up)
# the 7B llama-server is evicted/crashes, so the ACTIVE bindings use models
# that reliably run on this host. The 7B family remains registered and can be
# bound when more RAM is available. The selection layer always reports the
# model actually used, so attribution stays honest.
AGENT_MODELS = {
    "joeos.joe": {
        "preferred": "qwen2.5-coder:1.5b-opencode-safe",
        "fallback": "qwen2.5-coder:1.5b",
    },
    "joeos.architect": {
        "preferred": "qwen2.5-coder:1.5b",
        "fallback": "qwen2.5-coder:1.5b-fast",
    },
    "joeos.builder": {
        "preferred": "qwen2.5-coder:1.5b-opencode-safe",
        "fallback": "qwen2.5-coder:1.5b-fast",
    },
    "joeos.researcher": {
        "preferred": "qwen2.5-coder:1.5b",
        "fallback": "qwen2.5-coder:1.5b-fast",
    },
    "joeos.verifier": {
        "preferred": "qwen2.5-coder:1.5b",
        "fallback": "qwen2.5-coder:1.5b-fast",
    },
    "joeos.security": {
        "preferred": "qwen2.5-coder:1.5b",
        "fallback": "qwen2.5-coder:1.5b-fast",
    },
}


def _joe_system_instructions() -> str:
    return (
        "You are Joe, Joe's personal AI orchestrator. Responsibilities: "
        "communicate naturally with the operator, understand objectives, answer "
        "directly when the work is simple, plan tasks when it is complex, "
        "delegate to the right specialist, collect their results, request "
        "independent verification when the work is consequential, and present a "
        "clear final result with provenance. You never bypass the ToolBroker, "
        "policy, or approval gates. You never invent tool output or claim a "
        "delegated agent did work it did not do. Keep results concise, honest, "
        "and evidence-based."
    )


def _architect_system_instructions() -> str:
    return (
        "You are the Architect. Responsibilities: software and system "
        "architecture, decomposition, design, dependency analysis, technical "
        "plans, architecture review, and identifying implementation risks. "
        "Return structured analysis with status, summary, evidence or "
        "references, risks, and a recommended next action. Never modify files "
        "unless a write tool is explicitly invoked through the ToolBroker and "
        "approved. Never present speculation as fact."
    )


def _builder_system_instructions() -> str:
    return (
        "You are the Builder. Responsibilities: implementation planning, "
        "coding, repository modifications only through authorized tools, "
        "bounded development operations, patch generation, and build/test "
        "repair. You do NOT have unrestricted shell authority. Any write or "
        "build action must go through the ToolBroker, proposal, policy, and "
        "approval path. Never claim a change landed unless verified by the "
        "authoritative tool result."
    )


def _researcher_system_instructions() -> str:
    return (
        "You are the Researcher. Responsibilities: information retrieval, "
        "repository research, documentation research, file research, "
        "synthesis, provenance preservation, and evidence collection. Always "
        "cite the source of each claim. Never fabricate references or "
        "evidence. Report what is known and explicitly mark what is unknown."
    )


def _verifier_system_instructions() -> str:
    return (
        "You are the Verifier. Responsibilities: independently verify claims, "
        "inspect implementations, run authorized tests, inspect test output, "
        "challenge incomplete work, and confirm acceptance criteria. You do "
        "not simply agree with the Builder. If evidence is missing or the work "
        "fails a criterion, return FAILED_VERIFICATION with specific findings. "
        "Return VERIFIED or PARTIALLY_VERIFIED only when the evidence supports "
        "it."
    )


def _security_system_instructions() -> str:
    return (
        "You are the Security reviewer. Responsibilities: security review, "
        "secret scanning, permissions review, unsafe-tool detection, "
        "trust-boundary review, provider privacy review, and connector "
        "security review. You may block a recommendation or implementation "
        "result. Never approve your own work. Report findings as SECURITY_OK "
        "or SECURITY_BLOCK with specific evidence. Never fabricate "
        "vulnerabilities or compliance claims."
    )


AGENT_DEFINITIONS: Sequence[Dict] = (
    {
        "key": "joeos.joe",
        "display_name": "Joe",
        "description": "Primary personal AI orchestrator",
        "purpose": "Understand objectives, plan, delegate, verify, and report.",
        "system_instructions": _joe_system_instructions(),
        "allowed_tools": "joeos.read_memory,joeos.search_knowledge,joeos.system_status,joeos.read_documentation,joeos.list_agents",
        "denied_tools": "",
        "required_capabilities": "",
        "max_delegation_depth": 3,
        "max_parallel_tasks": 1,
        "max_runtime_ms": 120_000,
        "max_token_budget": 8192,
        "data_boundary": "restricted",
        "approval_policy": "backend",
    },
    {
        "key": "joeos.architect",
        "display_name": "Architect",
        "description": "Software/system architecture and planning",
        "purpose": "Decompose objectives, design, and identify risks.",
        "system_instructions": _architect_system_instructions(),
        "allowed_tools": "joeos.read_memory,joeos.search_knowledge,joeos.system_status,joeos.read_documentation,joeos.list_agents",
        "denied_tools": "",
        "required_capabilities": "",
        "max_delegation_depth": 1,
        "max_parallel_tasks": 1,
        "max_runtime_ms": 120_000,
        "max_token_budget": 8192,
        "data_boundary": "restricted",
        "approval_policy": "backend",
    },
    {
        "key": "joeos.builder",
        "display_name": "Builder",
        "description": "Implementation planning and bounded coding",
        "purpose": "Plan and produce implementation work through authorized tools.",
        "system_instructions": _builder_system_instructions(),
        "allowed_tools": "joeos.read_memory,joeos.search_knowledge,joeos.system_status,joeos.read_documentation,joeos.list_agents",
        "denied_tools": "",
        "required_capabilities": "",
        "max_delegation_depth": 0,
        "max_parallel_tasks": 1,
        "max_runtime_ms": 120_000,
        "max_token_budget": 8192,
        "data_boundary": "restricted",
        "approval_policy": "backend",
    },
    {
        "key": "joeos.researcher",
        "display_name": "Researcher",
        "description": "Repository and documentation research",
        "purpose": "Find, read, and synthesize evidence with provenance.",
        "system_instructions": _researcher_system_instructions(),
        "allowed_tools": "joeos.read_memory,joeos.search_knowledge,joeos.system_status,joeos.read_documentation,joeos.list_agents",
        "denied_tools": "",
        "required_capabilities": "",
        "max_delegation_depth": 0,
        "max_parallel_tasks": 1,
        "max_runtime_ms": 120_000,
        "max_token_budget": 8192,
        "data_boundary": "restricted",
        "approval_policy": "backend",
    },
    {
        "key": "joeos.verifier",
        "display_name": "Verifier",
        "description": "Independent verification and acceptance checking",
        "purpose": "Independently verify work against acceptance criteria.",
        "system_instructions": _verifier_system_instructions(),
        "allowed_tools": "joeos.read_memory,joeos.search_knowledge,joeos.system_status,joeos.read_documentation,joeos.list_agents",
        "denied_tools": "",
        "required_capabilities": "",
        "max_delegation_depth": 0,
        "max_parallel_tasks": 1,
        "max_runtime_ms": 120_000,
        "max_token_budget": 8192,
        "data_boundary": "restricted",
        "approval_policy": "backend",
    },
    {
        "key": "joeos.security",
        "display_name": "Security",
        "description": "Security review and trust-boundary analysis",
        "purpose": "Review security-sensitive work and block unsafe results.",
        "system_instructions": _security_system_instructions(),
        "allowed_tools": "joeos.read_memory,joeos.search_knowledge,joeos.system_status,joeos.read_documentation,joeos.list_agents",
        "denied_tools": "",
        "required_capabilities": "",
        "max_delegation_depth": 0,
        "max_parallel_tasks": 1,
        "max_runtime_ms": 120_000,
        "max_token_budget": 8192,
        "data_boundary": "restricted",
        "approval_policy": "backend",
    },
)


SAFE_TOOL_DEFINITIONS: Sequence[Dict] = (
    {
        "key": "joeos.system_status",
        "display_name": "JoeOS system status",
        "description": "Read the authoritative JoeOS runtime, service, and telemetry status.",
        "version": "1.0.0",
        "category": "read_only",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "risk": "informational",
        "side_effect": "none",
        "execution_availability": "server",
    },
    {
        "key": "joeos.list_agents",
        "display_name": "List agents",
        "description": "List the authoritative agent profiles and their configured model/provider.",
        "version": "1.0.0",
        "category": "read_only",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "risk": "informational",
        "side_effect": "none",
        "execution_availability": "server",
    },
    {
        "key": "joeos.read_memory",
        "display_name": "Read memory records",
        "description": "Read knowledge/memory records within the caller's workspace scope.",
        "version": "1.0.0",
        "category": "retrieval",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "additionalProperties": False,
        },
        "risk": "informational",
        "side_effect": "none",
        "execution_availability": "server",
    },
    {
        "key": "joeos.search_knowledge",
        "display_name": "Search knowledge",
        "description": "Search the JoeOS knowledge/memory index for relevant records.",
        "version": "1.0.0",
        "category": "retrieval",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}},
            "additionalProperties": False,
        },
        "risk": "informational",
        "side_effect": "none",
        "execution_availability": "server",
    },
    {
        "key": "joeos.read_documentation",
        "display_name": "Read documentation",
        "description": "Read a documentation file path within the JoeOS repository (read-only).",
        "version": "1.0.0",
        "category": "read_only",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        "risk": "informational",
        "side_effect": "none",
        "execution_availability": "server",
    },
)


def _model_key_for_agent(agent_key: str, installed: Sequence[str]) -> Optional[str]:
    binding = AGENT_MODELS.get(agent_key)
    if not binding:
        return None
    for candidate in (binding["preferred"], binding["fallback"]):
        if candidate in installed:
            return candidate
    return None


def activate_agent_fabric(
    service: ActionService,
    principal: Dict,
    *,
    installed_models: Sequence[str],
    ollama_health: str = "healthy",
    ollama_version: Optional[str] = None,
) -> Dict:
    """Idempotently activate the production Agent Fabric.

    Returns a summary of what was registered/created. Safe to call on every
    backend start; existing records are left untouched (only missing records
    are created)."""
    summary = {
        "provider": None,
        "models_registered": [],
        "models_disabled": [],
        "agents": [],
        "tools": [],
        "bindings": [],
    }

    # 1. Provider: Ollama local-private, server-side, enabled when healthy.
    existing_provider = service._store.get_provider_by_key(JOEOS_PROVIDER_KEY)
    if existing_provider is None:
        service.register_provider(
            principal,
            key=JOEOS_PROVIDER_KEY,
            display_name=JOEOS_PROVIDER_DISPLAY,
            provider_type="ollama",
            location="local",
            transport="http",
            endpoint_reference="loopback",
            auth_reference_type="none",
            streaming=True,
            tool_calling=True,
            structured_output=True,
            context_window=131072,
            privacy_class="restricted",
            allowed_data_classes="restricted",
        )
        summary["provider"] = JOEOS_PROVIDER_KEY
    provider = service._store.get_provider_by_key(JOEOS_PROVIDER_KEY)
    if provider is None:  # pragma: no cover - defensive
        raise RuntimeError("Ollama provider could not be registered")
    if provider.status != "active" or provider.health != ollama_health:
        service.set_provider_status(principal, provider.id, "active", ollama_health)
    if summary["provider"] is None:
        summary["provider"] = provider.key

    # 2. Model registry sync: installed->active, registered-but-missing->disabled.
    installed_set = set(installed_models)
    registered = {
        m.key: m for m in service._store.list_models(provider.id)
    }
    for name in installed_models:
        existing = registered.get(name)
        if existing is None:
            service.register_model(
                principal,
                provider_id=provider.id,
                key=name,
                display_name=name,
                model_identifier=name,
                streaming=True,
                tool_calling=True,
                structured_output=True,
                reasoning=("deepseek" in name.lower()),
                context_limit=131072,
                privacy_class="restricted",
                allowed_data_classes="restricted",
            )
            summary["models_registered"].append(name)
        elif existing.status != "active":
            service.set_model_status(principal, existing.id, "active")
    for key, model in registered.items():
        if key not in installed_set and model.status == "active":
            service.set_model_status(principal, model.id, "disabled")
            summary["models_disabled"].append(key)

    # 3. Safe foundational tools (read-only only).
    for tool_def in SAFE_TOOL_DEFINITIONS:
        existing = service._store.get_tool_by_key(tool_def["key"])
        if existing is None:
            service.register_tool(principal, **tool_def)
            summary["tools"].append(tool_def["key"])

    # 4. Production agent team with real model bindings.
    for definition in AGENT_DEFINITIONS:
        existing = service._store.get_agent_by_key(
            principal["organization"]["id"], principal["workspace"]["id"], definition["key"]
        )
        binding = _model_key_for_agent(definition["key"], installed_models) or "backend"
        if existing is None:
            agent = service.create_agent(
                principal,
                default_provider_policy=JOEOS_PROVIDER_KEY,
                default_model_policy=binding,
                **{k: v for k, v in definition.items()},
            )
            summary["agents"].append(definition["key"])
            if binding != "backend":
                summary["bindings"].append("%s=%s" % (definition["key"], binding))
        elif (
            existing.default_provider_policy != JOEOS_PROVIDER_KEY
            or existing.default_model_policy != binding
        ):
            # Rebind an existing agent when the authoritative model inventory
            # or binding policy changed (e.g. resource-constrained models).
            # This creates a new immutable AgentVersion.
            service.update_agent(
                principal, existing.id, existing.revision,
                default_provider_policy=JOEOS_PROVIDER_KEY,
                default_model_policy=binding,
            )
            if binding != "backend":
                summary["bindings"].append("%s=%s (rebound)" % (definition["key"], binding))

    return summary
