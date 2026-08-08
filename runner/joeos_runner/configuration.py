"""Strict typed runner configuration.

Unknown fields fail validation. File and private-key permissions are checked.
Secret values and the private key are never embedded in the configuration; the
private key lives in a separate 0600 file and secrets come from the runner-local
secret provider.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ALLOWED_ENV_OVERRIDES = frozenset({"JOEOS_RUNNER_LOG_LEVEL"})


class RunnerConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class RepositoryRegistration:
    id: str
    root: str
    allowed_remotes: str = ""
    protected_branches: str = "main,master,production,release"
    allowed_branch_prefixes: str = ""
    max_changed_files: int = 500
    max_changed_bytes: int = 5 * 1024 * 1024
    secret_scan_template: str = ""
    hooks_policy: str = "disabled"
    submodule_policy: str = "disabled"
    status: str = "active"


@dataclass(frozen=True)
class ServiceRegistration:
    id: str
    unit_name: str
    owner: str = "joeos"
    allowed_operations: str = "status,start,stop,restart,verify_active,health_check,logs_bounded"
    health_check: str = ""
    timeout_ms: int = 60_000
    status: str = "active"


@dataclass(frozen=True)
class SecretProviderConfig:
    provider: str = "development"
    credential_directory: str = ""
    allowed_names: str = ""


@dataclass(frozen=True)
class RunnerConfiguration:
    backend_url: str
    installation_id: str
    runner_id: str
    organization_id: str
    workspace_id: str
    identity_path: str
    key_path: str
    journal_path: str
    state_path: str
    work_root: str
    workspace_roots: str
    repository_registrations: List[RepositoryRegistration] = field(default_factory=list)
    service_registrations: List[ServiceRegistration] = field(default_factory=list)
    allowed_executors: str = "joeos.test.deterministic,joeos.dev.command,joeos.git.repository,joeos.user.service,joeos.deployment"
    max_concurrency: int = 1
    max_job_runtime_ms: int = 600_000
    heartbeat_interval_ms: int = 15_000
    lease_renewal_interval_ms: int = 30_000
    reconnect_initial_ms: int = 1_000
    reconnect_max_ms: int = 60_000
    reconnect_jitter_ms: int = 500
    output_limit_bytes: int = 1_048_576
    artifact_limit_bytes: int = 10 * 1024 * 1024
    log_level: str = "info"
    secret_provider: SecretProviderConfig = field(default_factory=SecretProviderConfig)
    protocol_version: int = 1
    allowed_env_overrides: str = "JOEOS_RUNNER_LOG_LEVEL"
    apple_build_host: str = ""
    apple_build_user: str = ""
    apple_build_identity: str = ""
    apple_build_mirror: str = ""

    @classmethod
    def load(cls, path: str, environ: Optional[Dict[str, str]] = None) -> "RunnerConfiguration":
        config_path = Path(path).expanduser()
        if not config_path.is_file():
            raise RunnerConfigurationError("configuration file not found: %s" % config_path)
        _check_mode(config_path, owner_read_only=True, what="configuration file")
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RunnerConfigurationError("configuration is not valid JSON: %s" % error) from error
        return cls.from_dict(raw, environ=environ, config_path=config_path)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], *, environ: Optional[Dict[str, str]] = None,
                  config_path: Optional[Path] = None) -> "RunnerConfiguration":
        if not isinstance(raw, dict):
            raise RunnerConfigurationError("configuration root must be an object")
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(raw) - known
        if unknown:
            raise RunnerConfigurationError("unknown configuration field(s): %s" % ", ".join(sorted(unknown)))
        env = environ or os.environ
        if raw.get("allowed_env_overrides", cls.allowed_env_overrides):
            configured = set(str(raw["allowed_env_overrides"]).split(",")) if isinstance(raw.get("allowed_env_overrides"), str) else set()
            invalid = configured - ALLOWED_ENV_OVERRIDES
            if invalid:
                raise RunnerConfigurationError("disallowed environment override(s): %s" % ", ".join(sorted(invalid)))
        required = ["backend_url", "installation_id", "runner_id", "organization_id",
                    "workspace_id", "identity_path", "key_path"]
        for name in required:
            if not str(raw.get(name, "")).strip():
                raise RunnerConfigurationError("missing required field: %s" % name)
        backend_url = str(raw["backend_url"])
        cls._validate_backend_url(backend_url)
        key_path = str(raw["key_path"])
        if config_path is not None:
            if not Path(key_path).is_absolute():
                key_path = str((config_path.parent / key_path).resolve())
        _check_mode(Path(key_path), owner_read_only=True, what="runner private key")
        registrations = [
            RepositoryRegistration(**repo) for repo in raw.get("repository_registrations", [])
        ]
        services = [
            ServiceRegistration(**svc) for svc in raw.get("service_registrations", [])
        ]
        secret = raw.get("secret_provider", {})
        secret_config = SecretProviderConfig(**secret) if isinstance(secret, dict) else SecretProviderConfig()
        values = dict(raw)
        values["backend_url"] = backend_url
        values["key_path"] = key_path
        values["repository_registrations"] = registrations
        values["service_registrations"] = services
        values["secret_provider"] = secret_config
        allowed = set(str(values.get("allowed_env_overrides", cls.allowed_env_overrides)).split(","))
        for var in sorted(allowed & ALLOWED_ENV_OVERRIDES):
            if var in env:
                values["log_level"] = env[var]
        return cls(**values)

    @staticmethod
    def _validate_backend_url(url: str) -> None:
        if url.startswith("http://") and not (url.startswith("http://127.0.0.1") or url.startswith("http://localhost") or url.startswith("http://[::1]")):
            raise RunnerConfigurationError("refusing unsafe public HTTP backend endpoint: %s" % url)
        if not (url.startswith("https://") or url.startswith("http://")):
            raise RunnerConfigurationError("backend endpoint must use http or https")
        if "@" in url or "?" in url or "#" in url:
            raise RunnerConfigurationError("backend endpoint must not contain credentials, query, or fragment")

    def allowed_executor_ids(self) -> set:
        return {e for e in str(self.allowed_executors).split(",") if e}

    def resolve_repository(self, repository_id: str) -> Optional[RepositoryRegistration]:
        for repo in self.repository_registrations:
            if repo.id == repository_id:
                return repo
        return None

    def resolve_service(self, service_id: str) -> Optional[ServiceRegistration]:
        for service in self.service_registrations:
            if service.id == service_id:
                return service
        return None

    def effective_summary(self) -> Dict[str, Any]:
        """Non-secret effective configuration summary (never the private key)."""
        return {
            "backend_url": self.backend_url,
            "installation_id": self.installation_id,
            "runner_id": self.runner_id,
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "identity_path": self.identity_path,
            "key_path": self.key_path,
            "journal_path": self.journal_path,
            "work_root": self.work_root,
            "workspace_roots": self.workspace_roots,
            "allowed_executors": self.allowed_executors,
            "max_concurrency": self.max_concurrency,
            "heartbeat_interval_ms": self.heartbeat_interval_ms,
            "reconnect_initial_ms": self.reconnect_initial_ms,
            "reconnect_max_ms": self.reconnect_max_ms,
            "output_limit_bytes": self.output_limit_bytes,
            "log_level": self.log_level,
            "secret_provider": self.secret_provider.provider,
            "protocol_version": self.protocol_version,
            "repository_registrations": [repo.id for repo in self.repository_registrations],
            "service_registrations": [svc.id for svc in self.service_registrations],
            "apple_build_host": self.apple_build_host,
        }


def _check_mode(path: Path, *, owner_read_only: bool, what: str) -> None:
    if not path.exists():
        raise RunnerConfigurationError("%s not found: %s" % (what, path))
    mode = stat.S_IMODE(path.stat().st_mode)
    if owner_read_only:
        if mode & 0o077:
            raise RunnerConfigurationError("%s must be readable only by its owner (0600): %s" % (what, path))
