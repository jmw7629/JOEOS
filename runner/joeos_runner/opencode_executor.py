"""Constrained OpenCode integration for the engineering campaign.

OpenCode is never driven through the interactive TUI. This module invokes the
documented noninteractive interface `opencode run --format json` inside a
bounded worktree, with a fixed model, no `--auto` approval, and a hard output
limit. The runner remains the only execution plane; OpenCode simply produces a
bounded JSON transcript that the campaign records as evidence.
"""

from __future__ import annotations

import json
import os
from typing import Callable, Dict, List, Optional

from .process import ProcessResult, run_process

# The `opencode` binary is only discoverable on the runtime host; the executor is
# inert (denied) when the binary is absent so a missing integration is never
# reported as a success. Override per-host with JOEOS_OPENCODE_BIN (VPS default
# retained for backward compatibility).
DEFAULT_OPENCODE_BIN = os.getenv(
    "JOEOS_OPENCODE_BIN", "/home/joewillisny/.opencode/bin/opencode")

# Allowlist of model keys the executor may drive. Operators may extend the set
# at deploy time with JOEOS_OPENCODE_ALLOWED_MODELS (comma-separated) so hosts
# with local providers (e.g. Halo's Ollama) do not require a code change.
ALLOWED_MODELS = {
    "openrouter/deepseek-v4-flash",
    "opencode/deepseek-v4-flash-free",
    *(m.strip() for m in os.getenv("JOEOS_OPENCODE_ALLOWED_MODELS", "").split(",")
      if m.strip()),
}

MAX_TRANSCRIPT_BYTES = 64 * 1024


class OpenCodeError(Exception):
    pass


class OpenCodeCodingExecutor:
    """Invokes `opencode run --format json` in a bounded directory.

    Parameters (all validated, never shell tokens):
      - model: an allowlisted model key
      - prompt: the bounded instruction text
      - dir: an absolute path inside the approved worktree root
      - timeout_ms: bounded by the caller
    """

    key = "joeos.engineering.opencode"

    def __init__(
        self,
        *,
        binary: str = DEFAULT_OPENCODE_BIN,
        worktree_root: str = "",
        allowed_models: Optional[set] = None,
        adapter: Optional[Callable[[list, dict], ProcessResult]] = None,
    ) -> None:
        self._binary = binary
        self._worktree_root = os.path.realpath(worktree_root)
        self._allowed_models = allowed_models or set(ALLOWED_MODELS)
        self._adapter = adapter

    def execute(self, parameters: Dict, target: str, *, root: str,
                environment: Optional[Dict[str, str]] = None,
                timeout_ms: int = 60_000) -> dict:
        model = str(parameters.get("model") or "")
        prompt = str(parameters.get("prompt") or "")
        directory = str(parameters.get("dir") or root)
        if model not in self._allowed_models:
            return {"status": "failed", "summary": "disallowed opencode model",
                    "exit_classification": "denied"}
        if not prompt.strip():
            return {"status": "failed", "summary": "empty opencode prompt",
                    "exit_classification": "denied"}
        if len(prompt) > 8000:
            return {"status": "failed", "summary": "opencode prompt exceeds 8000 chars",
                    "exit_classification": "denied"}
        if not os.path.isabs(directory) or any(c in directory for c in (";", "|", "&&", "`", "$(")):
            return {"status": "failed", "summary": "invalid opencode directory",
                    "exit_classification": "denied"}
        real_dir = os.path.realpath(directory)
        if self._worktree_root and not (
            real_dir == self._worktree_root or real_dir.startswith(self._worktree_root + os.sep)
        ):
            return {"status": "failed", "summary": "opencode directory outside approved worktree",
                    "exit_classification": "denied"}
        if not os.path.isdir(real_dir):
            return {"status": "failed", "summary": "opencode directory does not exist",
                    "exit_classification": "denied"}
        if not os.access(self._binary, os.X_OK):
            return {"status": "failed", "summary": "opencode binary unavailable",
                    "exit_classification": "denied"}
        arguments = [
            "run", "--format", "json", "--dir", real_dir,
            "--model", model, prompt,
        ]
        if self._adapter is not None:
            result = self._adapter([self._binary, *arguments], {"LC_ALL": "C"})
        else:
            result = run_process(executable=self._binary,
                                 arguments=arguments,
                                 cwd=real_dir,
                                 timeout_ms=min(timeout_ms, 600_000),
                                 environment={"LC_ALL": "C"})
        transcript = _extract_transcript(result)
        if result.exit_code != 0:
            return {"status": "failed", "summary": "opencode run failed",
                    "exit_classification": "failed",
                    "output": transcript[-2000:],
                    "data": {"transcript_bytes": len(transcript)}}
        return {"status": "succeeded", "summary": "opencode run completed",
                "exit_classification": "clean",
                "output": transcript[-2000:],
                "data": {"transcript_bytes": len(transcript)}}


def _extract_transcript(result: ProcessResult) -> str:
    output = (result.stdout or "")[:MAX_TRANSCRIPT_BYTES]
    try:
        payload = json.loads(output)
        if isinstance(payload, dict):
            if isinstance(payload.get("result"), str):
                return payload["result"]
            if isinstance(payload.get("content"), str):
                return payload["content"]
        if isinstance(payload, list):
            parts = []
            for event in payload:
                if isinstance(event, dict) and isinstance(event.get("content"), str):
                    parts.append(event["content"])
            if parts:
                return "\n".join(parts)
    except (ValueError, TypeError):
        pass
    return output
