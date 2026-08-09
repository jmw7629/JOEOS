# Work Packages

A WorkPackage is the bounded unit of autonomous engineering. It must be small
enough to understand, implement, test, verify, and roll back.

## Anatomy

- `key` — stable id, e.g. `JOE-ENG-042`
- `title`, `description`
- `acceptance_criteria` — explicit, testable
- `owner_agent_key` / `verifier_agent_key` / `review_agent_key`
- `dependencies` — other package keys that must be `completed` first
- `stage_order` — the canonical pipeline
- `risk` — informational/low/medium/high/critical
- `roadmap_order`, `priority`

## Examples

```
JOE-ENG-042: Add semantic source drawer to Deep Dive results.
Objective: when a Deep Dive answer is shown, expose "VIEW SOURCES".
Scope: conversation UI + result model. Out of scope: new retrieval.
Acceptance: sources render from persisted provenance; no chain-of-thought exposed.
Risk: low. Dependencies: none.
```

Bad packages (rejected): "finish JoeOS", "improve the frontend".

## Change budget

- small — a few files
- medium — one subsystem
- large — must be decomposed before it is a single package

The Engineering Director enforces a file count and byte budget when applying
Builder output, and refuses traversal or secret-shaped content.
