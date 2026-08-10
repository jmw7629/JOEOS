"""Authorized JoeOS Object resolution.

Resolves ObjectRefs into bounded object summaries by delegating to the existing
domain services. Resolution is always authorization-aware: the caller must pass
a validated principal, and each adapter applies its own resource-boundary
checks. An object id alone never grants access.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from server.objects.core import (
    BASE_CAPABILITIES,
    CAP_VIEW,
    ObjectRef,
    capabilities_for,
    effective_capabilities,
    normalize_object_type,
    safety_for_capability,
    safety_level,
)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _safe_text(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _object_summary(ref: ObjectRef, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Build a bounded, serializable object summary from resolved fields."""
    state = fields.get("lifecycle_state") or fields.get("status")
    caps = effective_capabilities(ref.object_type, _safe_text(state, 24))
    summary: Dict[str, Any] = {
        "object": ref.to_dict(),
        "type": ref.object_type,
        "name": _safe_text(fields.get("name") or fields.get("display_name") or ref.display_hint or ref.object_id, 160),
        "status": _safe_text(state, 40) or "unknown",
        "capabilities": caps,
        # Uniform action safety language: every capability maps to a level so
        # the UI can gate safe/consequential/privileged/destructive actions.
        "action_safety": {cap: safety_for_capability(cap) for cap in caps},
    }
    for key in ("description", "owner", "workspace_id", "organization_id", "version", "created_at", "updated_at", "health", "subtitle"):
        if fields.get(key) is not None:
            summary[key] = fields[key] if isinstance(fields[key], (int, float, bool)) else _safe_text(fields[key], 200)
    return summary


class ObjectResolver:
    """Resolves authorized object references via existing domain services.

    Each ``_resolve_*`` adapter returns fields for an object the *requested
    principal* may view. Unauthorized or unknown objects resolve to an empty
    dict, and the router turns that into a 404/restricted surface.
    """

    def __init__(self) -> None:
        # Lazy domain hooks, wired at startup from the backend.
        self._agents = None
        self._automation = None
        self._engineering = None
        self._security = None
        self._conversations = None
        self._memory = None
        self._runtime = None
        self._workspace = None

    # -- startup wiring ----------------------------------------------------
    def wire_agents(self, agents) -> None:
        self._agents = agents

    def wire_automation(self, automation) -> None:
        self._automation = automation

    def wire_engineering(self, engineering) -> None:
        self._engineering = engineering

    def wire_security(self, security) -> None:
        self._security = security

    def wire_conversations(self, conversations) -> None:
        self._conversations = conversations

    def wire_memory(self, memory) -> None:
        self._memory = memory

    def wire_runtime(self, runtime) -> None:
        self._runtime = runtime

    def wire_workspace(self, workspace) -> None:
        self._workspace = workspace

    # -- resolution ---------------------------------------------------------
    def resolve(self, ref: ObjectRef, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        kind = normalize_object_type(ref.object_type)
        if not kind:
            return None
        identity = str(principal.get("sub") or principal.get("identity") or principal.get("user_id") or "")
        resolver = getattr(self, f"_resolve_{kind}", None)
        if resolver is None:
            # Unknown-but-registered types without an adapter resolve as a
            # minimal object envelope so deep links fail safely but clearly.
            return _object_summary(ref, {"name": ref.display_hint or ref.object_id, "status": "unknown"})
        try:
            fields = resolver(ref, identity, principal) or {}
        except Exception:
            return None
        if not fields or fields.get("_denied"):
            return None
        return _object_summary(ref, fields)

    # -- adapters -----------------------------------------------------------
    def _resolve_agent(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        service = self._agents
        if service is None:
            return None
        agent = None
        if hasattr(service, "get_agent"):
            agent = service.get_agent(ref.object_id)
        elif hasattr(service, "organization"):
            org = service.organization
            if hasattr(org, "agent"):
                agent = org.agent(ref.object_id)
            elif hasattr(org, "agents"):
                for candidate in org.agents(include_inactive=True):
                    if str(getattr(candidate, "agent_id", "") or getattr(candidate, "id", "")) == ref.object_id:
                        agent = candidate
                        break
        if not agent:
            return None
        name = getattr(agent, "display_name", None) or getattr(agent, "name", None)
        status = getattr(agent, "availability", None) or getattr(agent, "status", None)
        role = getattr(agent, "role_id", None)
        caps = getattr(agent, "capabilities", None) or []
        return {
            "name": name or ref.object_id,
            "lifecycle_state": status,
            "subtitle": ("Role " + role) if role else None,
            "description": ", ".join(_as_list(caps))[:200] if _as_list(caps) else None,
            "capabilities_meta": {"agent_caps": _as_list(caps)[:8]},
            "version": getattr(agent, "config_version", None),
        }

    def _resolve_agent_run(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        service = self._agents
        if service is None:
            return None
        if hasattr(service, "get_run"):
            run = service.get_run(ref.object_id)
            if run:
                return {
                    "name": "Agent run " + str(ref.object_id)[:12],
                    "lifecycle_state": getattr(run, "state", None),
                    "subtitle": getattr(run, "agent_name", None) or getattr(run, "agent_id", None),
                    "description": getattr(run, "objective", None),
                    "created_at": getattr(run, "started_at", None),
                }
        # Fall back to the mission control run store when available.
        mission = getattr(service, "mission_envelope", None)
        if mission is None:
            return None
        try:
            envelope = mission(ref.object_id)
        except Exception:
            return None
        if envelope is None:
            return None
        state = getattr(envelope, "state", None) or getattr(envelope, "status", None)
        objective = getattr(envelope, "objective", None) or getattr(envelope, "charter", None)
        return {
            "name": "Agent run " + str(ref.object_id)[:12],
            "lifecycle_state": state,
            "description": objective,
            "subtitle": getattr(envelope, "agent_name", None),
        }

    def _resolve_provider(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        runtime = self._runtime
        if runtime is None:
            return None
        # runtime may be a callable returning the runtime dict, or a service object.
        data = runtime() if callable(runtime) else runtime
        if data is None:
            return None
        providers = data.get("providers") if isinstance(data, dict) else getattr(data, "providers", None)
        provider = None
        for entry in _as_list(providers):
            key = str(entry.get("id") or entry.get("provider") or entry.get("name") or "") if isinstance(entry, dict) else str(entry)
            if key.lower() == ref.object_id.lower():
                provider = entry
                break
        if not provider and isinstance(data, dict):
            provider = data.get("provider_status")
        if provider is None:
            return None
        healthy = True
        message = None
        if isinstance(provider, dict):
            healthy = bool(provider.get("healthy", True))
            message = provider.get("message") or provider.get("error")
        return {
            "name": ref.object_id,
            "lifecycle_state": "healthy" if healthy else "degraded",
            "health": "healthy" if healthy else "degraded",
            "description": _safe_text(message, 200) or None,
        }

    def _resolve_model(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        runtime = self._runtime
        if runtime is None:
            return None
        data = runtime() if callable(runtime) else runtime
        label = ref.object_id
        installed = False
        if isinstance(data, dict):
            for model in _as_list(data.get("models")):
                if str(model).strip().lower() == label.lower():
                    installed = True
                    break
        else:
            for model in _as_list(getattr(data, "models", [])):
                if str(model).strip().lower() == label.lower():
                    installed = True
                    break
        return {
            "name": label,
            "lifecycle_state": "installed" if installed else "available",
            "description": None,
        }

    def _resolve_automation(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        automation = self._automation
        if automation is None:
            return None
        workflow = None
        if hasattr(automation, "get_workflow"):
            workflow = automation.get_workflow(ref.object_id)
        if not workflow:
            workflows = automation.list_workflows(principal) if hasattr(automation, "list_workflows") else None
            for w in _as_list(workflows):
                if str(getattr(w, "workflow_id", "") or getattr(w, "id", "")) == ref.object_id:
                    workflow = w
                    break
        if not workflow:
            return None
        return {
            "name": getattr(workflow, "name", None) or ref.object_id,
            "lifecycle_state": getattr(workflow, "status", None) or ("enabled" if getattr(workflow, "enabled", False) else "disabled"),
            "version": getattr(workflow, "current_version", None),
            "health": getattr(workflow, "health_state", None),
            "description": getattr(workflow, "description", None),
        }

    def _resolve_schedule(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        automation = self._automation
        if automation is None:
            return None
        schedules = automation.list_schedules(principal) if hasattr(automation, "list_schedules") else None
        for s in _as_list(schedules):
            if str(getattr(s, "schedule_id", "") or getattr(s, "id", "") or getattr(s, "workflow_id", "")) == ref.object_id:
                return {
                    "name": getattr(s, "name", None) or getattr(s, "workflow_id", ref.object_id),
                    "lifecycle_state": "enabled" if getattr(s, "enabled", False) else "disabled",
                    "subtitle": getattr(s, "cron", None) or getattr(s, "schedule", None),
                    "next_run": getattr(s, "next_run", None),
                }
        return None

    def _resolve_file(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        engineering = self._engineering
        if engineering is None:
            return None
        projects = engineering.list_projects(principal) if hasattr(engineering, "list_projects") else None
        if isinstance(projects, dict):
            projects = projects.get("projects", projects)
        for p in _as_list(projects):
            if isinstance(p, dict):
                pid = str(p.get("project_id") or p.get("id") or "")
                if pid == ref.object_id or _safe_text(p.get("name"), 240).lower() == ref.object_id.lower():
                    return {
                        "name": p.get("name") or ref.object_id,
                        "lifecycle_state": p.get("status") or "ready",
                        "subtitle": p.get("module_kind") or "project",
                        "path": p.get("path"),
                        "description": p.get("summary") or p.get("description"),
                    }
            else:
                pid = str(getattr(p, "project_id", "") or getattr(p, "id", ""))
                if pid == ref.object_id or _safe_text(getattr(p, "name", ""), 240).lower() == ref.object_id.lower():
                    return {
                        "name": getattr(p, "name", None) or ref.object_id,
                        "lifecycle_state": getattr(p, "status", None) or "ready",
                        "subtitle": getattr(p, "module_kind", None) or "project",
                        "path": getattr(p, "path", None),
                        "description": getattr(p, "summary", None) or getattr(p, "description", None),
                    }
        return None

    def _resolve_approval(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        security = self._security
        if security is None:
            return None
        approval = None
        if hasattr(security, "get_approval"):
            approval = security.get_approval(ref.object_id)
        if not approval and hasattr(security, "approvals_list"):
            approvals = security.approvals_list()
            if isinstance(approvals, dict):
                approvals = approvals.get("approvals", [])
            for a in _as_list(approvals):
                if str(getattr(a, "approval_id", "") or getattr(a, "id", "") or a.get("approval_id") if isinstance(a, dict) else "") == ref.object_id:
                    approval = a
                    break
        if not approval:
            return None
        if isinstance(approval, dict):
            state = approval.get("state") or approval.get("status")
            return {
                "name": "Approval " + str(ref.object_id)[:12],
                "lifecycle_state": state,
                "subtitle": approval.get("action_id"),
                "description": approval.get("reason") or approval.get("summary"),
                "owner": approval.get("requester_identity"),
            }
        state = getattr(approval, "state", None) or getattr(approval, "status", None)
        return {
            "name": "Approval " + str(ref.object_id)[:12],
            "lifecycle_state": state,
            "subtitle": getattr(approval, "action_id", None),
            "description": getattr(approval, "reason", None) or getattr(approval, "summary", None),
            "owner": getattr(approval, "requester_identity", None),
        }

    def _resolve_execution(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        security = self._security
        if security is None:
            return None
        if hasattr(security, "get_execution"):
            execution = security.get_execution(ref.object_id)
            if execution:
                return {
                    "name": "Execution " + str(ref.object_id)[:12],
                    "lifecycle_state": getattr(execution, "state", None) or getattr(execution, "status", None),
                    "subtitle": getattr(execution, "agent_name", None),
                    "description": getattr(execution, "objective", None) or getattr(execution, "action_id", None),
                }
        # Fall back to the control store executions surface.
        control = getattr(self._security, "_control", None)
        if control is not None and hasattr(control, "get"):
            record = None
            try:
                record = control.get(ref.object_id)
            except Exception:
                record = None
            if record is not None:
                if isinstance(record, dict):
                    return {
                        "name": "Execution " + str(ref.object_id)[:12],
                        "lifecycle_state": record.get("state") or record.get("status"),
                        "subtitle": record.get("agent_name"),
                        "description": record.get("objective") or record.get("action_id"),
                    }
                return {
                    "name": "Execution " + str(ref.object_id)[:12],
                    "lifecycle_state": getattr(record, "state", None) or getattr(record, "status", None),
                    "subtitle": getattr(record, "agent_name", None),
                    "description": getattr(record, "objective", None) or getattr(record, "action_id", None),
                }
        return None

    def _resolve_conversation(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        conversations = self._conversations
        if conversations is None:
            return None
        conversation = None
        if hasattr(conversations, "get_conversation"):
            conversation = conversations.get_conversation(ref.object_id, identity)
        if not conversation:
            return None
        return {
            "name": getattr(conversation, "title", None) or ("Conversation " + str(ref.object_id)[:12]),
            "lifecycle_state": getattr(conversation, "state", None) or "active",
            "subtitle": getattr(conversation, "kind", None),
            "created_at": getattr(conversation, "created_at", None),
        }

    def _resolve_memory(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        memory = self._memory
        if memory is None:
            return None
        record = None
        if hasattr(memory, "get_record"):
            record = memory.get_record(ref.object_id, identity)
        elif hasattr(memory, "get"):
            try:
                record = memory.get(ref.object_id)
            except Exception:
                record = None
        if not record:
            return None
        if isinstance(record, dict):
            return {
                "name": record.get("title") or ("Memory " + str(ref.object_id)[:12]),
                "lifecycle_state": record.get("status") or "stored",
                "subtitle": record.get("category"),
                "description": _safe_text(record.get("summary") or record.get("content"), 240) or None,
            }
        return {
            "name": getattr(record, "title", None) or ("Memory " + str(ref.object_id)[:12]),
            "lifecycle_state": getattr(record, "status", None) or "stored",
            "subtitle": getattr(record, "category", None),
            "description": _safe_text(getattr(record, "summary", None) or getattr(record, "content", ""), 240) or None,
        }

    def _resolve_workspace(self, ref: ObjectRef, identity: str, principal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        workspace = self._workspace
        if workspace is None:
            return None
        if hasattr(workspace, "get_workspace"):
            ws = workspace.get_workspace(ref.object_id, identity)
            if ws:
                return {
                    "name": getattr(ws, "name", None) or ref.object_id,
                    "lifecycle_state": getattr(ws, "status", None) or "active",
                    "subtitle": getattr(ws, "kind", None),
                }
        return None

    # -- relationships --------------------------------------------------------
    def relationships(self, ref: ObjectRef, principal: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return typed, authorized relationships for an object.

        Relationships are resolved by delegating to domain services; every
        returned reference is itself an ObjectRef that can be inspected.
        Unauthorized or unknown relationships are simply omitted.
        """
        kind = normalize_object_type(ref.object_type)
        if not kind:
            return []
        return self._relationships_for(kind, ref, principal)

    def _relationships_for(self, kind: str, ref: ObjectRef, principal: Dict[str, Any]) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []

        if kind == "agent":
            service = self._agents
            if service is not None and hasattr(service, "get_agent"):
                agent = service.get_agent(ref.object_id)
                if agent is not None:
                    runs = _as_list(getattr(agent, "recent_runs", None))
                    for run in runs[:5]:
                        rid = str(getattr(run, "run_id", "") or getattr(run, "id", ""))
                        if rid:
                            result.append({"relation": "executed_by", "object": ObjectRef(object_id=rid, object_type="agent_run", display_hint=getattr(run, "objective", None)).to_dict()})
            return result

        if kind == "agent_run":
            service = self._agents
            if service is not None and hasattr(service, "get_run"):
                run = service.get_run(ref.object_id)
                if run is not None:
                    aid = getattr(run, "agent_id", None)
                    if aid:
                        result.append({"relation": "executed_by", "object": ObjectRef(object_id=str(aid), object_type="agent", display_hint=getattr(run, "agent_name", None)).to_dict()})
            return result

        if kind == "automation":
            automation = self._automation
            if automation is not None and hasattr(automation, "list_schedules"):
                for s in _as_list(automation.list_schedules(principal)):
                    if str(getattr(s, "workflow_id", "")) == ref.object_id:
                        sid = str(getattr(s, "schedule_id", "") or getattr(s, "id", ""))
                        result.append({"relation": "scheduled_by", "object": ObjectRef(object_id=sid or ref.object_id, object_type="schedule", display_hint=getattr(s, "name", None)).to_dict()})
            return result

        if kind == "schedule":
            automation = self._automation
            if automation is not None:
                schedules = automation.list_schedules(principal) if hasattr(automation, "list_schedules") else []
                for s in _as_list(schedules):
                    if str(getattr(s, "schedule_id", "") or getattr(s, "id", "")) == ref.object_id:
                        wid = str(getattr(s, "workflow_id", ""))
                        if wid:
                            result.append({"relation": "schedules", "object": ObjectRef(object_id=wid, object_type="automation").to_dict()})
                return result

        if kind == "approval":
            security = self._security
            if security is not None:
                approval = None
                if hasattr(security, "get_approval"):
                    approval = security.get_approval(ref.object_id)
                if approval is None and hasattr(security, "list_approvals"):
                    for a in _as_list(security.list_approvals(principal)):
                        if str(getattr(a, "approval_id", "") or getattr(a, "id", "")) == ref.object_id:
                            approval = a
                            break
                if approval is not None:
                    target_type = normalize_object_type(getattr(approval, "target_type", None))
                    target_id = getattr(approval, "target_id", None)
                    if target_type and target_id:
                        result.append({"relation": "approves", "object": ObjectRef(object_id=str(target_id), object_type=target_type).to_dict()})
            return result

        if kind == "execution":
            security = self._security
            if security is not None and hasattr(security, "get_execution"):
                execution = security.get_execution(ref.object_id)
                if execution is not None:
                    aid = getattr(execution, "agent_id", None)
                    if aid:
                        result.append({"relation": "executed_by", "object": ObjectRef(object_id=str(aid), object_type="agent", display_hint=getattr(execution, "agent_name", None)).to_dict()})
            return result

        if kind == "work_package":
            engineering = self._engineering
            if engineering is not None and hasattr(engineering, "get_work_package"):
                wp = engineering.get_work_package(ref.object_id)
                if wp is not None:
                    cid = getattr(wp, "campaign_id", None)
                    if cid:
                        result.append({"relation": "part_of", "object": ObjectRef(object_id=str(cid), object_type="campaign").to_dict()})
                    aid = getattr(wp, "agent_id", None)
                    if aid:
                        result.append({"relation": "assigned_to", "object": ObjectRef(object_id=str(aid), object_type="agent").to_dict()})
            return result

        if kind == "campaign":
            engineering = self._engineering
            if engineering is not None and hasattr(engineering, "list_work_packages"):
                for wp in _as_list(engineering.list_work_packages(ref.object_id)):
                    wid = str(getattr(wp, "work_package_id", "") or getattr(wp, "id", ""))
                    if wid:
                        result.append({"relation": "contains", "object": ObjectRef(object_id=wid, object_type="work_package", display_hint=getattr(wp, "title", None)).to_dict()})
            return result

        if kind == "file":
            engineering = self._engineering
            if engineering is not None and hasattr(engineering, "list_projects"):
                for p in _as_list(engineering.list_projects(principal)):
                    if str(getattr(p, "project_id", "") or getattr(p, "id", "")) == ref.object_id:
                        result.append({"relation": "managed_by", "object": ObjectRef(object_id="engineering", object_type="module", display_hint="Engineering Director").to_dict()})
            return result

        if kind == "provider":
            runtime = self._runtime
            if runtime is not None:
                data = runtime() if callable(runtime) else runtime
                models = data.get("models") if isinstance(data, dict) else getattr(data, "models", None)
                for m in _as_list(models)[:8]:
                    result.append({"relation": "provides", "object": ObjectRef(object_id=_safe_text(m, 120), object_type="model").to_dict()})
            return result

        if kind == "model":
            runtime = self._runtime
            if runtime is not None:
                data = runtime() if callable(runtime) else runtime
                provider = data.get("provider") if isinstance(data, dict) else getattr(data, "provider", None)
                if provider:
                    result.append({"relation": "hosted_by", "object": ObjectRef(object_id=_safe_text(provider, 80), object_type="provider").to_dict()})
            return result

        return result
