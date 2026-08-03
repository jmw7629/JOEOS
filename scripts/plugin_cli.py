#!/usr/bin/env python3
"""JoeOS Plugin CLI.

Development and packaging tooling for the Plugin and Extension Platform.
Operations are deterministic, documented, non-destructive, and never publish
or upload packages automatically.

Usage:
    python scripts/plugin_cli.py create <publisher>.<name> <target-dir> [--template command|panel|tool|agent|parser|importer|provider|theme]
    python scripts/plugin_cli.py validate <plugin-dir>
    python scripts/plugin_cli.py package <plugin-dir> -o <output.zip>
    python scripts/plugin_cli.py inspect <plugin-dir>
    python scripts/plugin_cli.py list-contributions <plugin-dir>
    python scripts/plugin_cli.py check-permissions <plugin-dir>
    python scripts/plugin_cli.py calculate-integrity <plugin-dir>
    python scripts/plugin_cli.py check-compatibility <plugin-dir> [--joeos-version X.Y.Z]
    python scripts/plugin_cli.py dev <plugin-dir> [--link]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parent.parent / "packages" / "plugin-sdk"
sys.path.insert(0, str(SDK_ROOT))
sys.path.insert(0, str(SDK_ROOT / "joesdk"))

from joesdk.packaging import (  # noqa: E402
    ValidationError,
    calculate_integrity,
    package_plugin,
    validate_manifest,
    validate_package,
)

TEMPLATES = {
    "command": "command-extension",
    "panel": "panel-extension",
    "tool": "tool-extension",
    "agent": "agent-extension",
    "parser": "parser-extension",
    "importer": "document-importer",
    "provider": "ai-provider",
    "theme": "theme-extension",
}


def _plugin_id(plugin_id: str) -> str:
    if "." not in plugin_id:
        raise SystemExit("error: plugin id must look like publisher.name")
    publisher_id, _, name = plugin_id.partition(".")
    return publisher_id, name


def cmd_create(args) -> None:
    publisher_id, name = _plugin_id(args.plugin_id)
    template = TEMPLATES.get(args.template, "command-extension")
    template_dir = SDK_ROOT / "templates" / template
    target = Path(args.target_dir)
    if target.exists() and any(target.iterdir()):
        raise SystemExit("error: target directory is not empty.")
    if not template_dir.is_dir():
        raise SystemExit("error: template %r not found." % template)
    target.mkdir(parents=True, exist_ok=True)
    for item in template_dir.iterdir():
        if item.is_dir():
            shutil.copytree(str(item), str(target / item.name))
        else:
            shutil.copy2(str(item), str(target / item.name))
    module = name.replace("-", "_")
    manifest_path = target / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        data["id"] = "%s.%s" % (publisher_id, name)
        data["name"] = name.replace("-", " ").title()
        data["publisher"]["id"] = publisher_id
        data["publisher"]["name"] = publisher_id
        data["entry_point"]["module"] = module
        manifest_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Rename the module placeholder to the real module name.
    for pattern in ("hello_plugin.py", "plugin.py"):
        placeholder = target / pattern
        if placeholder.exists():
            placeholder.rename(target / (module + ".py"))
    print("created %s at %s (template: %s)" % (args.plugin_id, target, template))


def cmd_validate(args) -> None:
    ok, reason = validate_package(args.plugin_dir)
    if not ok:
        raise SystemExit("invalid: " + reason)
    print("valid: %s" % args.plugin_dir)


def cmd_package(args) -> None:
    output = package_plugin(args.plugin_dir, args.output)
    print("packaged: %s" % output)


def cmd_inspect(args) -> None:
    manifest_path = Path(args.plugin_dir) / "manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("error: manifest.json is missing.")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        validate_manifest(data)
    except ValidationError as exc:
        print("warning: manifest is invalid: %s" % exc)
    print(json.dumps(data, indent=2, sort_keys=True))


def cmd_list_contributions(args) -> None:
    manifest_path = Path(args.plugin_dir) / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for contribution in data.get("contributions", []):
        print(
            "%s\t%s\t%s"
            % (contribution.get("type"), contribution.get("id"), contribution.get("title", ""))
        )
        for command in contribution.get("commands", []):
            print("  command\t%s" % command)
        for permission in contribution.get("requires_permissions", []):
            print("  requires\t%s" % permission)


def cmd_check_permissions(args) -> None:
    manifest_path = Path(args.plugin_dir) / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    print("required permissions:")
    for decl in data.get("required_permissions", []):
        print("  %s  # %s" % (decl["permission"], decl.get("purpose", "")))
    print("optional permissions:")
    for decl in data.get("optional_permissions", []):
        print("  %s  # %s" % (decl["permission"], decl.get("purpose", "")))


def cmd_integrity(args) -> None:
    inventory = calculate_integrity(args.plugin_dir)
    print("root_hash: %s" % inventory["root_hash"])
    print("files: %d" % len(inventory["files"]))


def cmd_compatibility(args) -> None:
    manifest_path = Path(args.plugin_dir) / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = args.joeos_version
    print("joeos_version: %s" % version)
    print("min_joeos_version: %s" % data.get("min_joeos_version", "0.0.0"))
    print("max_joeos_version: %s" % (data.get("max_joeos_version") or "none"))
    print("api_version: %s" % data.get("api_version", 1))


def cmd_dev(args) -> None:
    ok, reason = validate_package(args.plugin_dir)
    if not ok:
        raise SystemExit("invalid development plugin: " + reason)
    print("development plugin validated: %s" % args.plugin_dir)
    if args.link:
        print("link with: POST /api/v1/plugins/development/link")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="joeos-plugin", description="JoeOS Plugin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create a plugin from a template")
    create.add_argument("plugin_id")
    create.add_argument("target_dir")
    create.add_argument("--template", choices=sorted(TEMPLATES), default="command")
    create.set_defaults(func=cmd_create)

    validate = subparsers.add_parser("validate", help="validate a plugin directory")
    validate.add_argument("plugin_dir")
    validate.set_defaults(func=cmd_validate)

    package = subparsers.add_parser("package", help="build a plugin package")
    package.add_argument("plugin_dir")
    package.add_argument("-o", "--output", default=None)
    package.set_defaults(func=cmd_package)

    inspect = subparsers.add_parser("inspect", help="print the manifest")
    inspect.add_argument("plugin_dir")
    inspect.set_defaults(func=cmd_inspect)

    contributions = subparsers.add_parser("list-contributions", help="list declared contributions")
    contributions.add_argument("plugin_dir")
    contributions.set_defaults(func=cmd_list_contributions)

    permissions = subparsers.add_parser("check-permissions", help="list declared permissions")
    permissions.add_argument("plugin_dir")
    permissions.set_defaults(func=cmd_check_permissions)

    integrity = subparsers.add_parser("calculate-integrity", help="compute the package inventory hash")
    integrity.add_argument("plugin_dir")
    integrity.set_defaults(func=cmd_integrity)

    compatibility = subparsers.add_parser("check-compatibility", help="check compatibility bounds")
    compatibility.add_argument("plugin_dir")
    compatibility.add_argument("--joeos-version", default="2.0.0")
    compatibility.set_defaults(func=cmd_compatibility)

    dev = subparsers.add_parser("dev", help="validate a development plugin")
    dev.add_argument("plugin_dir")
    dev.add_argument("--link", action="store_true")
    dev.set_defaults(func=cmd_dev)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except ValidationError as exc:
        raise SystemExit("error: " + str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())