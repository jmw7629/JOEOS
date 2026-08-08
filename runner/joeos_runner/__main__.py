"""Runner daemon entrypoint: `python -m joeos_runner --config <path>`.

Loads the validated configuration, the 0600 runner key, the bounded journal,
the runner-local secret provider, and the registered executor adapters, then
runs the long-lived daemon loop against the private backend endpoint.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Dict, Optional

from .configuration import RunnerConfiguration, RunnerConfigurationError
from .daemon import RunnerDaemon
from .executors import REGISTERED_EXECUTORS
from .identity import RunnerIdentityError, RunnerSigner
from .journal import ExecutionJournal, JournalError
from .secrets import RunnerLocalSecretProvider
from .transport import HTTPRunnerTransport

CONFIG_DEFAULT = "/etc/joeos-runner/config.json"


def _build_secret_provider(config: RunnerConfiguration) -> RunnerLocalSecretProvider:
    allowed = {name for name in config.secret_provider.allowed_names.split(",") if name}
    return RunnerLocalSecretProvider(
        credential_directory=config.secret_provider.credential_directory,
        allowed_names=allowed,
    )


def _executor_resolver(config: RunnerConfiguration):
    """Resolve a registered executor key to an executor instance.

    Base adapters come from REGISTERED_EXECUTORS; the config-aware DevOps
    executors (constrained git per repository registration, dev command
    templates, user services, deployments) are built from the typed
    configuration so the production daemon can actually dispatch them."""
    from .operations import (
        AppleBuildExecutor,
        DeploymentExecutor,
        DevCommandExecutor,
        GitExecutor,
        UserServiceExecutor,
    )

    def resolve(key: str) -> object:
        allowed = config.allowed_executor_ids()
        if key not in allowed:
            return None
        if key == DevCommandExecutor.key:
            return DevCommandExecutor()
        if key == GitExecutor.key:
            registrations = {
                repo.id: repo for repo in config.repository_registrations
            }
            first = next(iter(registrations.values()), None)
            if first is None:
                return None
            remotes = tuple(r for r in str(first.allowed_remotes).split(",") if r)
            protected = tuple(r for r in str(first.protected_branches).split(",") if r)
            return GitExecutor(
                root=first.root,
                allowed_remotes=remotes,
                protected_branches=protected,
                secret_scan=None,
                hooks_path=None,
            )
        if key == UserServiceExecutor.key:
            return UserServiceExecutor(registrations={
                svc.id: {"unit_name": svc.unit_name, "allowed_operations": svc.allowed_operations}
                for svc in config.service_registrations
            })
        if key == DeploymentExecutor.key:
            service = UserServiceExecutor(registrations={
                svc.id: {"unit_name": svc.unit_name, "allowed_operations": svc.allowed_operations}
                for svc in config.service_registrations
            })
            return DeploymentExecutor(release_root=config.work_root, service=service)
        if key == AppleBuildExecutor.key:
            return AppleBuildExecutor(
                host=str(getattr(config, "apple_build_host", "") or ""),
                user=str(getattr(config, "apple_build_user", "") or ""),
                identity_file=str(getattr(config, "apple_build_identity", "") or ""),
                mirror_dir=str(getattr(config, "apple_build_mirror", "") or ""),
                source_root=config.work_root,
                project_path="apps/mobile/Xcode/JoeOSClient.xcodeproj",
            )
        return REGISTERED_EXECUTORS.get(key)

    return resolve


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="joeos-runner", description="JoeOS private runner daemon."
    )
    parser.add_argument("--config", default=CONFIG_DEFAULT)
    arguments = parser.parse_args(argv)
    try:
        config = RunnerConfiguration.load(arguments.config)
    except RunnerConfigurationError as error:
        print("configuration error: %s" % error, file=sys.stderr)
        return 2
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        signer = RunnerSigner(config.key_path, "runner-key-1").load()
        journal = ExecutionJournal(config.journal_path, config.runner_id)
    except RunnerIdentityError as error:
        print("identity error: %s" % error, file=sys.stderr)
        return 2
    except JournalError as error:
        print("journal error: %s" % error, file=sys.stderr)
        return 2
    transport = HTTPRunnerTransport(
        config.backend_url,
        protocol_version=config.protocol_version,
        runner_version="0.1.0",
        catalog_digest="",
    )
    daemon = RunnerDaemon(
        config,
        signer,
        transport,
        journal,
        secret_provider=_build_secret_provider(config),
        executor_resolver=_executor_resolver(config),
    )
    try:
        return daemon.start()
    finally:
        transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
