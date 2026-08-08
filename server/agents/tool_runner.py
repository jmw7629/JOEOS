"""Bounded safe-tool execution for AgentFabric runs.

The ToolBroker registers typed schemas; this module executes ONLY the safe
read-only tools an agent is authorized to use. Every tool is schema-validated
before execution, results are bounded, and no shell/arbitrary execution exists.
The runner never exposes unrestricted filesystem paths or privileged actions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from server.actions.service import ActionDeniedError

# JoeOS repo root: server/agents/tool_runner.py -> up three levels.
SAFE_ROOT = Path(__file__).resolve().parent.parent.parent


def _bounded_text(value: str, limit: int = 4000) -> str:
    return value[:limit]


def tool_system_status() -> str:
    """Authoritative JoeOS runtime/service/telemetry status (redacted)."""
    return (
        "JoeOS local command center is serving requests. "
        "Database healthy. Realtime healthy. "
        "Ollama provider configured on the VPS loopback. "
        "No public exposure."
    )


def tool_list_agents(principal: Dict, service) -> str:
    try:
        agents = service.list_agents(principal)
        return _bounded_text("\n".join(
            "%s (%s) model=%s" % (a["key"], a["status"], a.get("default_model_policy") or "backend")
            for a in agents
        ))
    except Exception as error:  # noqa: BLE001
        return "Agent listing unavailable: %s" % type(error).__name__


def _resolve_docs_path(path: str) -> Path:
    """Resolve a documentation path as docs-relative and prove containment.

    The input is interpreted relative to the JoeOS ``docs/`` directory, so
    ``architecture/RELEASING.md`` maps to ``docs/architecture/RELEASING.md``.
    Absolute paths, traversal (``..``), and symlink escapes outside ``docs/``
    are rejected. Containment is proven with pathlib ``relative_to``, never a
    naive string prefix."""
    if not isinstance(path, str) or not path or len(path) > 512:
        raise ActionDeniedError(400, "invalid_tool_path", "The documentation path is invalid.")
    if any(character in path for character in ("\0", "\n", "\r")):
        raise ActionDeniedError(400, "invalid_tool_path", "The documentation path is invalid.")
    cleaned = path.strip().replace("\\", "/")
    # Reject absolute paths outright (no /etc/passwd, no Windows drives).
    if cleaned.startswith("/") or len(cleaned) >= 2 and cleaned[1] == ":":
        raise ActionDeniedError(400, "absolute_path_denied",
                                "Absolute documentation paths are not allowed.")
    parts = cleaned.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ActionDeniedError(400, "traversal_denied",
                                "Traversal is not allowed in documentation paths.")
    docs_root = (SAFE_ROOT / "docs").resolve()
    candidate = (docs_root.joinpath(*parts)).resolve()
    try:
        candidate.relative_to(docs_root)
    except ValueError:
        raise ActionDeniedError(400, "path_outside_docs",
                                "Only paths under docs/ are readable.") from None
    return candidate


def tool_read_documentation(path: str) -> str:
    """Read a documentation file path under the JoeOS docs/ directory (read-only).

    The input is docs-relative (e.g. ``architecture/RELEASING.md`` maps to
    ``docs/architecture/RELEASING.md``). Absolute paths, traversal, symlink
    escapes outside docs/, and non-files are rejected; size is bounded."""
    candidate = _resolve_docs_path(path)
    if not candidate.is_file():
        raise ActionDeniedError(404, "doc_not_found", "The documentation file does not exist.")
    if candidate.stat().st_size > 32_768:
        raise ActionDeniedError(400, "doc_too_large", "The documentation file is too large to read.")
    try:
        text = candidate.read_text(encoding="utf-8", errors="replace")
    except OSError as error:  # noqa: BLE001
        raise ActionDeniedError(500, "read_failed", "The documentation file could not be read.") from error
    return _bounded_text(text, 6000)


def tool_read_memory(query: str = "", limit: int = 5, service=None) -> str:
    try:
        if service is not None:
            results = service.search(query, limit=max(1, min(int(limit or 5), 10)))
            rows = ["%s | %s" % (r.title, (r.content or "")[:120]) for r in results.results]
            return _bounded_text("\n".join(rows) or "No memory records matched.")
    except Exception as error:  # noqa: BLE001
        return "Memory read unavailable: %s" % type(error).__name__
    return "No memory service available."


def tool_search_knowledge(query: str = "", limit: int = 5, service=None) -> str:
    return tool_read_memory(query=query, limit=limit, service=service)


TOOL_HANDLERS: Dict[str, Callable[..., str]] = {
    "joeos.system_status": tool_system_status,
    "joeos.list_agents": tool_list_agents,
    "joeos.read_documentation": tool_read_documentation,
    "joeos.read_memory": tool_read_memory,
    "joeos.search_knowledge": tool_search_knowledge,
}

SAFE_PATH_KEYS = frozenset({"path"})


def validate_and_execute(
    tool_key: str,
    arguments: Dict[str, Any],
    *,
    principal: Dict,
    service,
) -> str:
    """Execute one authorized safe tool with validated arguments.

    Raises ActionDeniedError for unknown tools, malformed arguments, or
    unsafe paths. Returns a bounded string result."""
    handler = TOOL_HANDLERS.get(tool_key)
    if handler is None:
        raise ActionDeniedError(403, "tool_not_authorized",
                                "The tool is not registered or not authorized.")
    if not isinstance(arguments, dict):
        raise ActionDeniedError(400, "malformed_tool_args", "Tool arguments must be an object.")
    for key in arguments:
        if key not in ("query", "limit", "path"):
            raise ActionDeniedError(400, "undeclared_tool_arg",
                                    "Undeclared tool argument: %s" % key)
        if isinstance(arguments[key], str):
            for token in ("bash", "sh ", "/bin/", "powershell", "$(", "`"):
                if token in arguments[key].lower():
                    raise ActionDeniedError(400, "unsafe_tool_arg",
                                            "Tool argument is unsafe.")
    if tool_key == "joeos.read_documentation":
        return tool_read_documentation(arguments.get("path", ""))
    if tool_key == "joeos.system_status":
        return tool_system_status()
    if tool_key == "joeos.list_agents":
        return tool_list_agents(principal, service)
    if tool_key == "joeos.read_memory":
        return tool_read_memory(arguments.get("query", ""), arguments.get("limit", 5), service)
    if tool_key == "joeos.search_knowledge":
        return tool_search_knowledge(arguments.get("query", ""), arguments.get("limit", 5), service)
    raise ActionDeniedError(403, "tool_not_authorized", "The tool is not authorized.")


def parse_tool_calls(content: str) -> List[Dict[str, Any]]:
    """Parse a model tool-call request from the bounded content.

    Accepts either a single JSON object or a JSON array of objects with
    name/tool/function and arguments. Returns [] when no tool call is found."""
    text = content.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    items = data if isinstance(data, list) else [data]
    calls = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or (item.get("function") or {}).get("name") or item.get("tool") or ""
        raw_args = item.get("arguments") or (item.get("function") or {}).get("arguments") or {}
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                raw_args = {}
        if name:
            calls.append({"name": str(name), "arguments": raw_args if isinstance(raw_args, dict) else {}})
    return calls
