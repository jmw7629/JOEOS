# Customizable Mission Control

Status: implemented local-first foundation

Compatibility: root FastAPI/PWA routes preserved

## User experience

Mission Control is now the default workspace. It derives its live operational picture from the existing metrics, runtime, audit, and fleet APIs and answers:

- what requires attention now;
- what the read-only local AI can safely analyze;
- what is blocked or at risk;
- the recommended next action;
- what changed in recent local events.

Use **Add widget** to open the catalog. Live core modules can be added immediately. Connector-backed modules render an explicit integration-required state and a configuration entry point.

Use **Edit layout** to drag modules on desktop. On iPhone, keyboard, or assistive technology, each module also exposes Move Earlier, Move Later, Narrower, Wider, and Hide controls. **Done editing** saves through an optimistic workspace revision; a conflicting edit from another client is not overwritten.

Use **Customize** to set font scale, font family, accent color, primary text color, canvas color, layout density, corner radius, and glass opacity. The native color controls preview immediately and also update the installed web app's browser chrome color. **Save workspace** persists the complete versioned layout in Halo SQLite.

The configuration guide accepts requests such as:

```text
Use a purple accent, larger rounded font, text color #F4F7FF, background #020711, compact density, add calendar and move Halo health first
```

It returns a typed proposal, integration requirements, warnings, and a reminder that secrets belong in a dedicated secret workflow. A proposal is a preview only. It does not mutate the workspace until the user applies and saves it.

Press Command/Ctrl+K for the command palette. Shift+Command/Ctrl+K opens the private AI assistant.

## API contract

### `GET /api/workspace`

Returns the default workspace, revision, theme, ordered widget instances, versioned catalog, size constraints, and integration states.

### `PUT /api/workspace`

Atomically replaces the editable layout. The request includes the current revision, complete theme, and complete widget list. A stale revision returns HTTP 409 with the current server revision. Successful saves increment the revision and append an existing JoeOS audit event.

### `POST /api/configuration/guide`

Accepts `{ "message": "..." }` and returns a deterministic non-executing proposal. Credential-shaped input is ignored and not reflected or persisted.

## Backend design

`server/workspace` separates Pydantic contracts, service/repository behavior, and FastAPI transport. The service receives an injected SQLite connection factory and optional event sink, so its business behavior is testable independently of the application singleton.

Tables:

- `widget_catalog` — versioned immutable-style definitions;
- `workspaces` — name, revision, theme, timestamps;
- `workspace_widgets` — ordered instances, size, visibility, settings, catalog foreign key.

Settings are JSON-size bounded, widget versions and dimensions are catalog validated, instance IDs must be unique, and full-layout updates use `BEGIN IMMEDIATE` for deterministic revision checks.

## Client SDK

`packages/sdk` provides a dependency-free same-origin browser client with fixed supported routes, bounded timeouts, caller cancellation, normalized errors, and revision headers. It deliberately has no shell, download, deploy, generic request, or agent-control method.

## Deliberate boundary

This feature does not grant remote execution or connector permissions. Integration-required widgets are framework-ready manifests, not fake implementations. Identity, device enrollment, approvals, signed runners, and synchronized multi-user state remain security gates before privileged or remote actions.
