"""Runner-side CLI. Never prints the private key, connection credentials, or
secret values."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .configuration import RunnerConfiguration, RunnerConfigurationError
from .identity import RunnerIdentityError, RunnerSigner, initialize_identity
from .journal import ExecutionJournal, JournalError
from .executors import REGISTERED_EXECUTORS
from .operations import DEV_COMMAND_TEMPLATES

CONFIG_DEFAULT = "/etc/joeos-runner/config.json"
KEY_DEFAULT = "/etc/joeos-runner/runner-key.pem"
IDENTITY_DEFAULT = "/etc/joeos-runner/identity.json"


def _fail(code: str, message: str) -> int:
    print(json.dumps({"ok": False, "code": code, "message": message}))
    return 2


def _ok(payload: dict) -> int:
    print(json.dumps({**payload, "ok": True}, default=str, sort_keys=True))
    return 0


def _load_config(arguments) -> RunnerConfiguration:
    return RunnerConfiguration.load(arguments.config)


def _load_signer(config: RunnerConfiguration) -> RunnerSigner:
    return RunnerSigner(config.key_path, "runner-key-1").load()


def _identity_init(arguments) -> int:
    key_path = arguments.key
    identifier = arguments.identifier or "runner-key-1"
    try:
        initialize_identity(key_path, identifier)
    except RunnerIdentityError as error:
        return _fail("identity_error", str(error))
    return _ok({"key_path": key_path, "key_identifier": identifier,
                "note": "runner identity initialized; private key is 0600 and was never printed"})


def _identity_show(arguments) -> int:
    config = _load_config(arguments)
    try:
        signer = _load_signer(config)
    except RunnerIdentityError as error:
        return _fail("identity_error", str(error))
    return _ok({"runner_id": config.runner_id, "key_identifier": signer.key_identifier(),
                "public_key": signer.public_key(),
                "machine_fingerprint": signer.machine_fingerprint()})


def _config_validate(arguments) -> int:
    try:
        config = _load_config(arguments)
        config.effective_summary()
    except RunnerConfigurationError as error:
        return _fail("config_invalid", str(error))
    return _ok({"valid": True})


def _config_effective(arguments) -> int:
    try:
        config = _load_config(arguments)
    except RunnerConfigurationError as error:
        return _fail("config_invalid", str(error))
    return _ok(config.effective_summary())


def _self_test(arguments) -> int:
    try:
        config = _load_config(arguments)
        signer = _load_signer(config)
        fingerprint = signer.machine_fingerprint()
    except (RunnerConfigurationError, RunnerIdentityError) as error:
        return _fail("self_test_failed", str(error))
    return _ok({"self_test": "ok", "machine_fingerprint": fingerprint,
                "executors": sorted(REGISTERED_EXECUTORS.keys())})


def _executors_list(arguments) -> int:
    return _ok({"executors": sorted(REGISTERED_EXECUTORS.keys())})


def _executors_inspect(arguments) -> int:
    executor = REGISTERED_EXECUTORS.get(arguments.executor)
    if executor is None:
        return _fail("executor_unknown", "unknown executor: %s" % arguments.executor)
    return _ok({"executor": arguments.executor, "key": getattr(executor, "key", arguments.executor)})


def _journal_inspect(arguments) -> int:
    config = _load_config(arguments)
    journal = ExecutionJournal(config.journal_path, config.runner_id)
    try:
        entries = journal.entries()
    except JournalError as error:
        return _fail("journal_corrupt", str(error))
    return _ok({"entries": len(entries),
                "active_jobs": journal.active_jobs()})


def _journal_verify(arguments) -> int:
    config = _load_config(arguments)
    journal = ExecutionJournal(config.journal_path, config.runner_id)
    ok = journal.verify()
    return _ok({"integrity_ok": ok})


def _emergency_local_stop(arguments) -> int:
    print(json.dumps({"ok": True, "code": "local_stop",
                      "message": "Local emergency stop requested. Stop the service, terminate child process groups, and revoke local temporary secrets manually per the runbook."}))
    return 0


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="joeos-runner",
                                      description="JoeOS private runner CLI.")
    command.add_argument("--config", default=CONFIG_DEFAULT)
    command.add_argument("--key", default=KEY_DEFAULT)
    sub = command.add_subparsers(dest="command", required=True)

    init = sub.add_parser("identity-init")
    init.add_argument("--identifier", default="runner-key-1")
    sub.add_parser("identity-show")
    sub.add_parser("config-validate")
    sub.add_parser("config-effective")
    sub.add_parser("self-test")
    sub.add_parser("executors-list")
    inspect = sub.add_parser("executors-inspect")
    inspect.add_argument("executor")
    sub.add_parser("journal-inspect")
    sub.add_parser("journal-verify")
    sub.add_parser("emergency-local-stop")
    return command


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parser().parse_args(argv)
    handler = {
        "identity-init": _identity_init,
        "identity-show": _identity_show,
        "config-validate": _config_validate,
        "config-effective": _config_effective,
        "self-test": _self_test,
        "executors-list": _executors_list,
        "executors-inspect": _executors_inspect,
        "journal-inspect": _journal_inspect,
        "journal-verify": _journal_verify,
        "emergency-local-stop": _emergency_local_stop,
    }
    return handler[arguments.command](arguments)


if __name__ == "__main__":
    raise SystemExit(main())
