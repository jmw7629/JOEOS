# Universal Search

The browser Universal Search workspace orchestrates the **real per-domain search
endpoints** and groups results by type. There is no unified backend search
endpoint in the current architecture (see `KNOWLEDGE_PLATFORM_AUDIT.md`), so the
browser composes authoritative calls rather than inventing a fake global index.

## Real endpoints used

- Memory: `GET /api/v1/memory/search?q=&scope=&limit=` → `RetrievalEnvelope`
  (token-overlap relevance with score, reason, authority, evidence, excerpt)
- Project files: `GET /api/v1/engineering/projects/{id}/search?q=` → `SearchEnvelope`
  (searched across the first registered projects, bounded)

## Behavior

- Results are grouped by domain (Memory, Project files) with counts.
- Each result carries its provenance (scope/authority) and a bounded snippet.
- Permission model is backend-side: the browser only renders what the APIs return.
- Query cancellation is handled via the shared `requestJson` AbortController.

## Honest limits

- No dense-vector similarity — the memory index is token-overlap.
- No conversations search exists yet (no backend route).
- No connector/federated search yet (no connector backend).
- Cross-workspace results are denied by the backend, not filtered client-side.
