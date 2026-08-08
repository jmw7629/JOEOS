# Memory Workspace

The browser Memory workspace is a client over the authoritative JoeOS memory
platform (`server/memory/`). It never stores or fabricates knowledge in the
browser; every record, search result, and review item comes from the real
`/api/v1/memory/*` contracts.

## What is authoritative

- Records (`POST /memory/records`, `GET /memory/records`, `GET /memory/records/{id}`)
- Provenance and evidence (`Provenance`, `EvidenceRecord`)
- Search (`GET /memory/search`) — a bounded, deterministic token-overlap index
- Review queue (`GET /memory/review`, `POST /memory/review/{id}`)
- Lifecycle (`correct`, `supersede`, `delete`, `versions`, `expire`, `import`)

## Browser surfaces

- **Records**: list with scope filter and title/content filter, state chips
  (accepted/proposed/conflict/superseded/deleted), detail with provenance,
  evidence refs, and lifecycle.
- **Search**: token-overlap search with score, reason, scope/authority/confidence
  and excerpt. The UI labels search honestly — the backend `semantic` flag is a
  token-overlap index, not dense vectors.
- **Review**: open review items (proposals, conflicts, corrections) resolved via
  the authoritative review API.
- **Correct / Forget**: versioned correction (`POST .../correct`) and deletion
  (`POST .../delete`) go through the backend only.

## Boundaries

- No secrets or hidden reasoning are rendered.
- No memory store is kept in the browser.
- No cross-workspace reads: scope filters are passed to the backend.
- Embedding state is displayed as reported (`not_embedded`/`pending`/`embedded`/`stale`).
