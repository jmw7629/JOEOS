# Files Workspace

The browser Files workspace browses **registered engineering projects** through
the authoritative engineering API. The browser never reads the VPS filesystem
directly and never holds file content beyond a single bounded read.

## Data sources

- Projects: `GET /api/v1/engineering/projects` → `ProjectEnvelope`
- Directory listing: `GET /api/v1/engineering/projects/{id}/files?path=`
  → `DirectoryListing` (bounded, truncated flag)
- File content: `GET /api/v1/engineering/projects/{id}/files/content?path=`
  → `DocumentState` with `masked_secrets` and a `revision`
- Artifacts: `GET /api/v1/agents/artifacts?limit=` → `ArtifactRecord` metadata

## Surfaces

- **Projects**: registered project cards with name and path.
- **Browse**: directory entries with kind, size, language, secret flag.
- **Preview**: safe text preview. When `masked_secrets > 0`, a visible note
  explains that the engineering API masked secret values. Content is rendered as
  text inside a bounded `<pre>`, never via `innerHTML`.
- **Artifacts**: agent-produced artifact metadata (type, producer, version, review
  state). Artifact bodies are not fetched by the browser.

## Boundaries

- No raw VPS paths.
- No generic file download from the browser.
- No secret exposure: the engineering API masks secret values server-side.
- Directory/content requests are lazy and bounded (one request per path).
