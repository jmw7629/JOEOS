"""Runner-local secret provider.

Secrets are resolved only immediately before executor launch, never returned to
the backend, never written to the journal, and scrubbed from output. Supported
storage: systemd credentials (interface) and runner-owned 0600 credential files
(fallback). No plaintext secret-retrieval API exists.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class ResolvedSecret:
    name: str
    value: str


class SecretResolutionError(Exception):
    pass


class RunnerLocalSecretProvider:
    """Resolves runner-local secrets by stable name.

    `development_values` maps stable names to values for deterministic tests
    only. Production uses systemd credentials or 0600 credential files.
    """

    def __init__(self, credential_directory: str = "",
                 development_values: Optional[Dict[str, str]] = None,
                 allowed_names: Sequence = ()) -> None:
        self._credential_directory = credential_directory
        self._development_values = development_values or {}
        self._allowed = set(allowed_names)

    def list_names(self) -> List[str]:
        names = set(self._development_values.keys())
        if self._credential_directory and os.path.isdir(self._credential_directory):
            names.update(
                path.name for path in Path(self._credential_directory).iterdir()
                if path.is_file() and not path.name.startswith(".")
            )
        return sorted(names)

    def resolve(self, name: str) -> ResolvedSecret:
        if self._allowed and name not in self._allowed:
            raise SecretResolutionError("secret reference is not allowed: %s" % name)
        if name in self._development_values:
            return ResolvedSecret(name=name, value=self._development_values[name])
        if self._credential_directory:
            path = Path(self._credential_directory) / name
            if path.is_file():
                mode = path.stat().st_mode & 0o777
                if mode & 0o077:
                    raise SecretResolutionError("secret file must be 0600: %s" % path)
                value = path.read_text(encoding="utf-8").rstrip("\n")
                return ResolvedSecret(name=name, value=value)
        raise SecretResolutionError("secret not available locally: %s" % name)

    def write_temporary(self, name: str, value: str, work_root: str) -> str:
        """Writes a protected temporary credential file for an executor-specific
        injection mechanism. Deleted by the caller after use."""
        fd, path = tempfile.mkstemp(prefix="joeos-secret-", dir=work_root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(value)
            return path
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    @staticmethod
    def redact(text: str, secrets: Sequence[str]) -> str:
        result = text
        for secret in secrets:
            if secret and len(secret) >= 4:
                result = result.replace(secret, "[REDACTED]")
        return result

    @staticmethod
    def scan_for_leakage(text: str, secrets: Sequence[str]) -> bool:
        for secret in secrets:
            if secret and len(secret) >= 4 and secret in text:
                return True
        return False


from typing import Sequence  # noqa: E402
