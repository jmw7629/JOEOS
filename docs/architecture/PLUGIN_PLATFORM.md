# JoeOS Plugin and Extension Platform

Phase 10 delivers `server/plugins/`: a local-first, secure-by-default platform
for extending JoeOS without compiling every future capability into the core.

## Security model

- **Least privilege.** A plugin receives only the permissions it declares and
  the user grants. There is no "trusted plugin" master switch.
- **Explicit capabilities.** Files, tools, AI, network, memory, secrets,
  projects, and hardware are accessed only through typed, brokered
  capabilities.
- **No direct core access.** Plugins run inside an isolated Extension Host
  and interact through a validated JSON RPC surface. They never import core
  internals, never reach the privileged process, and never modify core
  registries.
- **Versioned contracts.** Manifests, API versions, and storage schemas are
  explicit and compatibility-checked.
- **Safe failure.** A crashing plugin is contained to its host subprocess;
  the core keeps running.
- **Supply-chain defense.** Packages carry integrity inventories (SHA-256),
  signature state, and publisher identity. Content never grants authority.
- **No hidden execution.** A plugin is never activated merely because its
  files exist; activation is lazy and event-driven.
- **No self-authorization.** A plugin cannot grant itself permissions or
  approve its own high-risk actions.

## Architecture

```text
server/plugins/
├── models.py          typed, strict contracts for the whole platform
├── storage.py         versioned SQLite registry (plugins.db)
├── integrity.py       package + manifest + inventory hashing
├── signature.py       ECDSA P-256 signature evaluation (no custom crypto)
├── publishers.py      Publisher Registry (first-party / verified / user-trusted / unknown / revoked / blocked)
├── permissions.py     typed permission model + CapabilityBroker
├── dependency.py      dependency resolution + circular-reference detection
├── compatibility.py   compatibility resolver (JoeOS version, API version, platform)
├── contributions.py   authoritative Contribution Registry
├── events.py          bounded, permission-checked Event Gateway
├── extension_data.py  scoped extension storage + validated settings
├── secrets.py         Extension Secret Broker (AES-256-GCM at rest)
├── resources.py       Resource Governor (active jobs, rate, log volume)
├── health.py          health, redacted logs, activity, diagnostics
├── host.py            Extension Host manager (subprocess isolation)
├── host_protocol.py   typed RPC boundary (JSON lines)
├── host_runner.py     child-side runner (loads the plugin entry)
├── lifecycle.py       install / enable / activate / disable / update / rollback / quarantine / safe mode / uninstall
├── service.py         PluginService facade
└── router.py          REST API under /api/v1/plugins/*
```

## Extension Host isolation

Every active plugin runs in a dedicated subprocess
(`python -m server.plugins.host_runner`) that:

- loads only the plugin's entry module from its managed install directory;
- never imports the privileged core;
- communicates over newline-delimited JSON RPC (request id, plugin id, API
  version, method, validated params, trace id);
- enforces a bounded request timeout and a bounded restart policy;
- escalates repeated crashes to quarantine.

Every capability the child requests is checked by the CapabilityBroker in the
parent against the plugin's granted permissions and lifecycle state before any
action is permitted.

## Permission model

Permissions are granular and scoped (denied, ask-each-time, granted once,
session, workspace, project, global, revoked, blocked-by-policy). A plugin may
only request permissions from the typed catalog, and a manifest is rejected if
it declares an unknown permission or uses a permission it did not declare.
Revoking a permission takes effect without reinstalling.

## Lifecycle

`discovered → validating → pending_permissions → installed → disabled →
enabled → activating → active → idle/deactivating → disabled`, plus
`quarantined`, `updating`, `rolling_back`, `uninstalling`, `removed`, and
`crashed`. Activation requires an enabled state, complete required
permissions, satisfiable dependencies, and a verified package integrity.

## Contributions

Commands, panels, views, tools, agent roles, providers, parsers, analyzers,
document importers, themes, automations, and hardware adapters are registered
through the Contribution Registry. Core contribution IDs can never be
shadowed; collisions between plugins are resolved deterministically.

## Development

The SDK (`packages/plugin-sdk`) provides manifest helpers, validation,
integrity calculation, packaging, and templates. The CLI
(`scripts/plugin_cli.py`) supports `create`, `validate`, `package`,
`inspect`, `list-contributions`, `check-permissions`,
`calculate-integrity`, `check-compatibility`, and `dev`. Local development
plugins are installed from directories, require `development: true` in the
manifest, are never treated as trusted production, and are visually distinct.

## Signature architecture

Signatures use ECDSA P-256 over the package inventory root hash. States are
honest: `valid_first_party`, `valid_user_trusted`, `valid`, `unsigned`,
`invalid`, `unavailable`, and `locally_modified`. This release implements
validation architecture plus a clearly labeled development-mode exception. A
public marketplace and key distribution remain future work and are not
claimed here.

## Known limitations

- No public marketplace, catalog, or remote update channel (local installs
  and packages only).
- Signature *verification* is implemented; signing key management, revocation
  distribution, and publisher verification against a public authority remain
  future work.
- Isolation is per-plugin subprocess (no OS-level sandbox, WebAssembly, or
  restricted iframe yet).
- Webhooks and remote connectors are not implemented.
