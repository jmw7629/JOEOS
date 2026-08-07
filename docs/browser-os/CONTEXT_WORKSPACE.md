# Context Workspace

The browser Context workspace explains what knowledge JoeOS can surface and
shows the highest-authority memory records in scope. It is deliberately a
**read-only, explainable view** — it does not assemble a context pack client-side.

## What it shows

- **Knowledge boundaries**: secrets excluded, hidden reasoning excluded, scope
  bounded to approved workspaces — matching the memory service contract that
  "secrets and hidden reasoning are never stored."
- **Sources**: each record carries its memory id, scope, authority, confidence,
  and evidence references.
- **Recent memory context**: bounded list of user-scoped memory records as
  returned by the authoritative memory API.

## What it never does

- Never builds context by copying data into the browser.
- Never exposes chain-of-thought, prompts, or system internals.
- Never renders credentials or secret values.
- Never writes memory from the context view.

## Relation to intelligence context packs

The intelligence platform exposes `POST /intelligence/projects/{id}/context-pack`
(objective + targets) with a `ContextPack` model. The browser context workspace
shows the memory-side surface; project context packs remain backend-driven.
