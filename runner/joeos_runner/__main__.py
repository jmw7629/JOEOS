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
    allowed = config.allowed_executor_ids()
    return lambda key: REGISTERED_EXECUTORS.get(key) if key in allowed else None


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
