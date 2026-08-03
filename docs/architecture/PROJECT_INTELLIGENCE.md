# Project and Repository Intelligence Platform

The Project and Repository Intelligence platform gives JoeOS a trustworthy,
incremental understanding of every registered project. It answers "what is
this project?", "what does it depend on?", "what would break if I change
this?", "what is risky?", and "where is the thing I am looking for?" with
citable evidence instead of guesses.

## Principles

1. **Evidence-based.** Every record carries a `Provenance` (kind, source,
   detail, timestamp) and a `Confidence`. Nothing is fabricated.
2. **Parsed facts are not AI facts.** Parser-derived symbols, references,
   dependency edges, Git history, manifest facts, and user-entered decisions
   stay distinguishable. Semantic embedding results (future) never merge with
   parsed facts.
3. **Incremental and local-first.** The index lives in a versioned SQLite
   database under the JoeOS data directory. Content hashes let scans skip
   unchanged files. Indexing runs on a background thread and is cancellable.
4. **Secrets are excluded.** Secret-bearing files are flagged and never
   parsed, indexed, or returned by retrieval.
5. **No false certainty.** Change-impact uses likelihood buckets
   (`direct`/`likely`/`possible`/`insufficient_evidence`); contributors are
   labeled `recent_contributor`, never "owner".

## Module map (`server/intelligence/`)

| Module | Responsibility |
| --- | --- |
| `models.py` | Typed contracts for every intelligence record with provenance, confidence, freshness, and literal validation. |
| `detect.py` | Evidence-based language, framework, package manager, build system, and test system detection. |
| `identity.py` | `ProjectIdentity` and stable `RepositoryFingerprint` from Git and filesystem facts. |
| `inventory.py` | Incremental file inventory, content hashing, classification, and secret/generated flagging. |
| `parser.py` | Bounded, language-aware parser adapters (python, js, ts, go, rust, java, sql, markdown, shell, dockerfile) with per-file failure isolation. |
| `graph.py` | Dependency and architecture graph construction with bounded cycle detection. |
| `gitintel.py` | Bounded Git history: churn, hotspots, and contributor ownership. |
| `analysis.py` | Change-impact estimation and explainable risk findings. |
| `knowledge.py` | Decisions (ADR), conventions, and memory registry with authoritative provenance. |
| `storage.py` | Versioned SQLite storage with per-thread connections, WAL, and migration/corruption guards. |
| `indexer.py` | Incremental indexing engine with phases, cancellation, health, and diagnostics. |
| `retrieval.py` | Hybrid structured retrieval (exact, symbol, dependency) and context packs. |
| `service.py` | Facade composing all capabilities for the API layer. |
| `router.py` | REST API under `/api/v1/intelligence/...`. |

## Index lifecycle

1. `scanning` — inventory the tree, hash contents, classify each file.
2. `classifying` — ingest ADR decisions and lint/editorconfig conventions.
3. `parsing` — extract symbols and references per file; failures are isolated.
4. `linking` — resolve references to file targets; build the dependency graph.
5. `validating` — collect risk findings (secrets, parse failures, unresolved
   references, cycles, hotspots, stale entries, generated sources).
6. `finalizing` — record diagnostics and terminal health.

Health states: `healthy`, `degraded`, `stale`, `indexing`, `cancelled`,
`failed`, `unavailable`. Diagnostics report indexed/excluded/stale file
counts, parse failures, unresolved references, symbol and relationship
totals, parser versions, storage version, size, and recent errors.

## Change-impact model

A change to a file affects the files that import it (its reverse neighbors)
and, transitively, their importers, up to a bounded depth. Likelihood is
`direct` at depth 1 and falls to `likely`/`possible` beyond; confidence is
`reported` only for direct relationships. Recommendations (run the related
test suite, smoke the serving path) are attached per impacted file.

## Security and privacy boundary

- Secret-bearing files (`.env*`, key material, credential files) are flagged
  in the inventory and excluded from parsing, indexing, and retrieval.
- The index stores metadata and content hashes, never full file contents.
- Index storage is under the JoeOS data directory with its own SQLite file;
  version mismatches refuse to load rather than silently corrupting.
- No remote services are contacted; Git history is read with `git log
  --numstat` and bounded depth.
- Retrieval results carry `privacy_classification` and `freshness`.

## Future work (not in this slice)

- Real parser backends (tree-sitter) behind the adapter registry when
  dependencies are approved.
- Semantic embeddings behind the `RetrievalEnvelope` without changing the
  contract; embedding results remain separate from parsed facts.
- Cross-project queries and a global symbol index.
- Index encryption at rest and rollback-friendly migrations.

See the [implementation backlog](IMPLEMENTATION_BACKLOG.md) for the phase
status and acceptance record.
