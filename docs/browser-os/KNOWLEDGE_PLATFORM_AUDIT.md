# Knowledge Platform Audit

Audit of the real JoeOS memory, files, search, and knowledge backend so the P4-UI-E
browser knowledge layer is built against true contracts. Verified against the `ai-rebuild`
codebase at base commit `df9898a` (P4-UI-E worktree `feature/browser-memory-search-files`).

## Reality summary

- **Memory**: a real, typed memory platform exists (`server/memory/`) with records,
  provenance, evidence, entities, relationships, conflicts, review items, documents,
  notes, versions, correct/supersede/delete, search, import, backup. SQLite-backed.
- **Search**: memory search is a **bounded, deterministic token-overlap index — NOT
  dense vectors**. The envelope reports `semantic=True` but the engine is lexical
  token-overlap with authority/confidence/freshness ranking (`server/memory/service.py`
  `search()`). Embeddings are marked `embedding_model="token-overlap/1.0"`.
- **Files**: no general-purpose Files/upload API. Engineering exposes project-scoped
  file listing/read/write/search. Agents exposes artifact *metadata* registry.
- **Universal search**: no unified backend endpoint exists. Per-domain searches exist
  (memory search, engineering project search, intelligence project search).
- **Context**: memory `ContextPack` + intelligence `context-pack` endpoints exist.
- **Data plane**: no PostgreSQL/pgvector. Embeddings/vector infra is deferred; the
  current store is intentional SQLite token-overlap, not a hack.

## Capability classification

Legend: COMPLETE_BACKEND (real server support) · COMPLETE_BROWSER (this phase) ·
BACKEND_ONLY · PARTIAL · PLACEHOLDER · MISSING_BACKEND · MISSING_BROWSER ·
DEFERRED_TO_DATA_PLANE · NOT_REQUIRED.

### Memory
| Capability | Classification | Evidence |
|---|---|---|
| Memory records | COMPLETE_BACKEND | `MemoryRecord` + CRUD router |
| Memory provenance | COMPLETE_BACKEND | `Provenance` (kind, source, method, learned_at) |
| Memory scope | COMPLETE_BACKEND | `MemoryScope` enum, `primary_scope` + `related_scopes` |
| Memory relevance/ranking | COMPLETE_BACKEND | `RetrievalResult` score + `ranking_factors` |
| Memory confidence | COMPLETE_BACKEND | `ConfidenceClass` |
| Memory deletion | COMPLETE_BACKEND | `POST /memory/records/{id}/delete`, `DeletionState` |
| Memory forgetting | COMPLETE_BACKEND | deletion marks state + marks chunks deleted |
| Memory pinning | MISSING_BACKEND | no pin field in `MemoryRecord` (browser-only favorite allowed) |
| Memory editing/versioning | COMPLETE_BACKEND | `POST .../correct`, `MemoryVersion`, `.../versions` |
| Memory supersession | COMPLETE_BACKEND | `POST .../supersede`, `SupersededState` |
| Semantic search (dense) | DEFERRED_TO_DATA_PLANE | no vector store; `token-overlap/1.0` |
| Keyword search | COMPLETE_BACKEND | token-overlap engine |
| Hybrid search | MISSING_BACKEND | single lexical index only |
| Embeddings | DEFERRED_TO_DATA_PLANE | `embedding_state` tracked; model is token-overlap |
| Vector store | DEFERRED_TO_DATA_PLANE | no pgvector/FAISS |
| Indexing | COMPLETE_BACKEND | `DocumentRecord.extraction/chunking/indexing_state` |
| Ingestion/chunking | COMPLETE_BACKEND | `SemanticChunk`, `embedding_version`, `chunking_version` |
| Deduplication/conflicts | COMPLETE_BACKEND | `DuplicateCandidate`, `ConflictRecord` |
| Retention | COMPLETE_BACKEND | `RetentionMode`, `expires_at` |
| Review queue | COMPLETE_BACKEND | `/memory/review`, `ReviewItem`, `ReviewState` |
| Manual memory creation | COMPLETE_BACKEND | `POST /memory/records` (propose) |
| Memory usage history | MISSING_BACKEND | no per-record retrieval/use log surfaced |

### Files
| Capability | Classification | Evidence |
|---|---|---|
| File list (engineering) | COMPLETE_BACKEND | `GET /engineering/projects/{id}/files` |
| File read (engineering) | COMPLETE_BACKEND | `GET .../files/content` (secret-masked) |
| File write (engineering) | COMPLETE_BACKEND | `PUT .../files/content` (revision-guarded) |
| File metadata (engineering) | COMPLETE_BACKEND | `FileEntry`, `DocumentRevision` (sha256, size) |
| Project search | COMPLETE_BACKEND | `GET .../projects/{id}/search` → `SearchEnvelope` |
| General Files app (all projects) | MISSING_BACKEND | files are project-scoped; no global file registry |
| File upload | MISSING_BACKEND | no `UploadFile`/upload endpoint |
| File download (authenticated) | MISSING_BACKEND | engineering read returns content over API |
| File preview | COMPLETE_BACKEND (content API) | read returns masked content; browser renders safely |
| File deletion (general) | MISSING_BACKEND | project remove deletes registry record |
| Artifact metadata | COMPLETE_BACKEND | `/agents/artifacts` + `ArtifactRecord` |
| Artifact content/download | MISSING_BACKEND | artifacts are metadata; no content API |
| Indexing state | COMPLETE_BACKEND | `DocumentRecord` states |

### Search
| Capability | Classification | Evidence |
|---|---|---|
| Memory search | COMPLETE_BACKEND | `/memory/search` token-overlap |
| Engineering project search | COMPLETE_BACKEND | `/engineering/projects/{id}/search` |
| Intelligence project search | COMPLETE_BACKEND | `/intelligence/projects/{id}/search` |
| Conversations search | MISSING_BACKEND | no search route on conversations router |
| Agents search | PARTIAL | list + filter endpoints, no free-text search |
| Executions search | MISSING_BACKEND | no execution registry search surfaced |
| Universal search | MISSING_BACKEND | no unified endpoint; per-domain only |
| Search filters | PARTIAL | scope filter on memory search only |
| Search citations/provenance | COMPLETE_BACKEND | `RetrievalResult.evidence`, `reason`, excerpt |
| Search ranking | COMPLETE_BACKEND | backend score + `ranking_factors` |
| Search pagination | PARTIAL | limit params; no cursor pagination |

### Context / knowledge workspace
| Capability | Classification | Evidence |
|---|---|---|
| Context pack (memory) | COMPLETE_BACKEND | `ContextPack`, `ContextPackItem` |
| Context pack (intelligence) | COMPLETE_BACKEND | `/intelligence/projects/{id}/context-pack` |
| Sources used view | MISSING_BROWSER | backend supports context packs; browser app to build |
| Chain-of-thought exclusion | COMPLETE_BACKEND | service doc: "Secrets and hidden reasoning are never stored" |

## Backend wiring (verified)

- `app.include_router(memory_router)` — `joeos_backend.py:1322`
- `app.include_router(intelligence_router)` — `:1321`
- `app.include_router(engineering_router)` — `:1320`
- `app.include_router(realtime_router)` — `:1318`
- Memory service mounted with `data_dir=str(db_path.parent / "memory")`
- Engineering service with SQLite connection factory (project registry)

## Memory API surface (`/api/v1/memory/*`)

- `GET /memory/health` → `MemoryHealth` (state, diagnostics)
- `GET /memory/overview` → `MemoryOverview` (recent, awaiting_review, open_conflicts,
  stale_memories, expiring_soon, deletion_failures, documents_indexed,
  semantic_available, active_context_count, needs_attention)
- `POST /memory/records` → `MemoryRecord` (propose; status 201)
- `GET /memory/records/{id}` → `MemoryRecord`
- `GET /memory/records?scope=&limit=` → `List[MemoryRecord dict]` (active, recent first)
- `GET /memory/search?q=&scope=&limit=` → `RetrievalEnvelope`
- `GET /memory/review?state=&limit=` → `ReviewEnvelope`
- `POST /memory/review/{id}` {action,note} → `{review_id, action, resolved}`
- `GET /memory/records/{id}/versions` → `List[MemoryVersion]`
- `POST /memory/records/{id}/correct` {content,reason,changed_by} → `MemoryRecord`
- `POST /memory/records/{id}/supersede` → `dict`
- `POST /memory/records/{id}/delete` {reason} → `{deleted, memory_id}`
- `POST /memory/evidence` → `EvidenceRecord`
- `POST /memory/entities` / `GET /memory/entities` → entity registry
- `POST /memory/relationships` → relationship registry
- `POST /memory/import` → `ImportResult`
- `POST /memory/backup` → `dict`
- `GET /memory/storage` → `dict`
- `POST /memory/expire` → `dict` (202)

## Security observations

- Memory service `propose()` accepts a full `MemoryRecord`; **no session auth on the
  memory router** (mirrors the agents router). Scope is enforced by query params only.
  Browser UI must not present a security authority — backend is source of truth.
- Engineering file reads run through secret masking (`masked_secrets`, `SecretPolicy`).
- Browser must never browse VPS directories; only the engineering project file API is
  an authoritative file surface.
- `semantic_available`/`embedding_state` are authoritative flags the UI must respect:
  when embedding is token-overlap, the UI must not claim dense semantic search.

## Design constraints for P4-UI-E

1. Build truthful UI on existing contracts: memory CRUD/search, engineering file
   browse/read, artifact metadata, context packs.
2. Universal search in the browser = orchestrate the real per-domain endpoints
   (memory search + engineering project search + bounded object lists), typed and
   grouped. Do not claim a backend semantic search that does not exist.
3. No browser-only memory DB, no fabricated embeddings, no VPS path exposure.
4. Any backend addition must be a genuine missing general-purpose contract,
   authenticated, scoped, tested (e.g., a secure authenticated file upload/download
   contract if Files needs one).
