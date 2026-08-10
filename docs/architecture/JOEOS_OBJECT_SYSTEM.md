# JoeOS Enterprise Object System

Status: **Foundational architecture**

Everything meaningful inside JoeOS is an Enterprise Object. An object may
represent an agent, an AI model, a provider, a file, a workflow, a schedule, a
task, a WorkPackage, a campaign, an approval, an execution, a machine, a
device, an organization, a workspace, a user, a conversation, a message, a
memory, a module, a widget, a notification — they all participate in the same
universal object model.

This document is the canonical reference for that abstraction. Future features
must not bypass it casually.

---

## 1. The principle

> I click anything. JoeOS knows exactly what it is. JoeOS knows where it came
> from. JoeOS knows what owns it. JoeOS knows what it owns. JoeOS knows what it
> relates to. JoeOS knows what happened to it. JoeOS knows what it can do.
> JoeOS knows what I am allowed to do with it. Joe knows how to reason about
> it. And I can continue navigating deeper through its relationships.

The object system is implemented **incrementally**: existing domains keep their
own databases and services and are surfaced through authorized adapters. There
is never a destructive "big bang" rewrite.

---

## 2. Core contracts

### 2.1 ObjectRef

A lightweight, canonical, stable reference. `object_id` + `object_type` are the
only required fields; `organization_id`/`workspace_id` scope where relevant and
`display_hint` carries a safe label.

```json
{ "object_id": "1847", "object_type": "execution" }
```

ObjectRef is how Joe context, relationships, navigation, activity, search,
approvals, executions, files, and notifications reference objects — without
copying whole objects around.

Python: `server/objects/core.py::ObjectRef`

### 2.2 ObjectTypeRegistry

A typed registry of canonical object kinds (`OBJECT_TYPES`). Documented aliases
are normalized (`workflow` → `automation`, `mission` → `agent_run`, `bot` →
`agent`, `workpackage` → `work_package`). Unknown types fail safely: resolution
returns `None` rather than fabricating meaning. Custom enterprise types can be
declared via `register_object_type()` without editing the kernel.

### 2.3 Capabilities

Every object exposes capabilities:

- base contract (always): `view`, `inspect`, `search`, `ask_joe`
- type-specific sets (e.g. `approval` adds `approve`/`reject`; `file` adds
  `edit`/`archive`/`export`/`version`; `agent` adds `execute`/`automate`)

Lifecycle state disables capabilities (an archived file is not editable or
executable; a deleted object is not even viewable). Policy denies override
capabilities.

`server/objects/core.py::effective_capabilities`

### 2.4 Action safety levels

One predictable safety language for every action:

| Level | Gate |
|-------|------|
| `safe` | executes immediately |
| `consequential` | previewed before running |
| `privileged` | requires approval |
| `destructive` | heavily protected |

Unknown or mislabeled actions default to the **most conservative** level so a
mislabeled action can never run without the right gate.

`server/objects/core.py::safety_level`, `safety_gate`

### 2.5 Lifecycle

A shared lifecycle vocabulary applies where meaningful: create → read → update →
archive → restore → delete/purge, with capability and permission gates. Soft
delete/archive is preferred over destructive deletion; auditability is
preserved.

---

## 3. Resolution — authorization model

Resolution is always **server-authorized**. The browser cannot fetch an object
merely by knowing its id.

`server/objects/resolver.py::ObjectResolver` resolves an `ObjectRef` by
delegating to existing domain services (agents, automation, engineering,
security, conversations, memory, runtime, workspace). Each adapter applies its
own resource-boundary checks; adapter failures degrade to not-found. A summary
is bounded and serializable:

```json
{
  "object": {"object_id": "a1", "object_type": "agent"},
  "type": "agent",
  "name": "Test Agent",
  "status": "busy",
  "capabilities": ["approve", "ask_joe", "edit", "execute", "inspect", "view"],
  "action_safety": {"approve": "privileged", "edit": "consequential", "execute": "privileged", "view": "safe"}
}
```

**Object context is information, never permission elevation.** When Joe
receives an ObjectRef, the server resolves object data through the current
authenticated permission/policy context. No authority expansion.

---

## 4. Relationships

Enterprise Objects form a typed graph. Relationships are explicit and
authorized, e.g.:

- `executed_by`, `approves`, `schedules`, `part_of`, `assigned_to`, `hosted_by`,
  `provides`, `contains`, `scheduled_by`

Relationship traversal returns ObjectRefs, so every related node is itself
inspectable. The UI renders relationship chips as one-tap navigation into the
next object.

`server/objects/resolver.py::relationships`

---

## 5. Object API

All routes are auth-gated (401 without an application session):

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/objects/types` | Object type registry |
| `GET /api/v1/objects/{type}/help` | Self-describing capabilities + safety gates |
| `GET /api/v1/objects/{type}/{id}` | Authorized object summary |
| `GET /api/v1/objects/{type}/{id}/relationships` | Typed authorized relationships |
| `GET /api/v1/objects/{type}/{id}/actions` | Safety-classified permitted actions |

---

## 6. Browser implementation

### 6.1 Object interaction grammar

JoeOS uses one universal interaction grammar:

| Gesture | Meaning |
|---------|---------|
| tap/click object | open |
| select | inspect (desktop inspector) |
| long press / ellipsis | secondary actions |
| drag | rearrange |
| Return control | go back one JoeOS context |
| Joe orb | intelligence |
| Command palette (Cmd+K) | find/do anything |

### 6.2 Object Quick Look

Selecting an ObjectRef resolves its authorized summary + relationships into the
desktop inspector: identity, state, capabilities (with safety gates),
relationships (clickable), and the primary action. Mobile uses a sheet.

### 6.3 Universal Recents / Favorites

Policy-safe ObjectRef store (`joeos:recent-objects`, `joeos:pinned-objects` in
localStorage — references only, never object data). Opening any object records
it most-recent-first with dedup; pinned objects are one-tap jumps.

### 6.4 Object-native command palette

The palette (Cmd+K) searches objects and commands together, returns grouped,
actionable results (Preview / Open / Run / Approve / Pin), and routes through
`openObjectRef()` to the correct workspace while preserving the selected
object. It is **not** a second Joe chat surface.

### 6.5 Single Joe invocation

The persistent Joe orb is the canonical Joe entry. Joe context comes from the
active module + selected ObjectRef automatically. No per-object "Ask Joe"
buttons; no duplicate prompt fields; no parallel assistant instances.

---

## 7. Attention Center

One system-level attention model answers "what needs me?": pending approvals,
failed automations, unhealthy schedules, open security events, lockdown,
secrets requiring rotation, incidents, and runtime unavailability. Everything
else stays passive activity. Each item is a clickable object card that drills
to the relevant workspace.

---

## 8. Joe object context

Joe receives a scoped `ObjectRef`, not an arbitrary blob of screen text.
`server/ai/service.py::_scoped_context_block` builds a bounded context line and
explicitly instructs the model that object scope grants no extra authority:
all actions still obey ToolBroker, policy, and approval.

---

## 9. Cross-platform contract

- **Browser**: as described above (`index.html`, `agent_fabric.html`).
- **SwiftUI**: equivalent native `EnterpriseObjectRef` / `ObjectType` /
  capability / navigation semantics; native navigation with visible Return.
- **Android Compose**: equivalent native object/navigation semantics; native
  Up/Return affordance; system Back remains supported but JoeOS has its own.

All clients agree on identity/type semantics. Unknown future properties fail
safely or are ignored per schema/version rules.

---

## 10. Migration strategy

Phase existing domains behind adapters (e.g. `Agent → AgentObjectAdapter →
EnterpriseObject`). Protect existing APIs and tests. Progressively make domains
object-native where beneficial. No destructive rewrite.

---

## 11. Object Graph Intelligence

The Object System is an intelligent enterprise graph, not just a navigation
framework.

### 11.1 Semantic status

Raw states are normalized into human meaning: `state`, `label`, `meaning`,
`impact`, `next`, and `tone`. HTTP codes (`503` → error, `200` → healthy),
availability words (`busy` → running, `unavailable` → offline), and workflow
states (`in_review` → waiting) all map to the shared vocabulary. Type-specific
vocabulary is preserved in `raw`; nothing is collapsed into one meaningless
generic enum.

`server/objects/intelligence.py::semantic_status`

### 11.2 Relationship intelligence

Relationships are ranked by importance using deterministic rules over
authoritative data: relationships from an object in a degraded/failed/blocked
state rank higher, and relationships to unhealthy objects rank higher. Typed
weights break ties. The graph is structural truth from data; Joe may explain
it but never invents it.

`server/objects/intelligence.py::rank_relationships`

### 11.3 Capability explanation

Every capability maps to an availability status with a machine + human reason:
`available` / `unavailable` / `blocked by policy` / `blocked by permission` /
`blocked by state` / `blocked by dependency` / `blocked by health` /
`requires approval`. The backend remains authoritative.

`server/objects/intelligence.py::capability_reason`

### 11.4 Object activity timeline

A persistent, object-centric timeline records who did what to which object
when, with result and traversable related ObjectRefs. Raw audit stays separate;
the timeline is human-facing semantic history. Entries are recorded from
authoritative domain events — history is never manufactured.

`server/objects/intelligence.py::ObjectActivityStore`

### 11.5 Causality ("Why?")

A server-side causal resolver gathers structured evidence: current state,
dependency health, approval coupling, and recent activity. It produces a
deterministic conclusion plus a bounded evidence list. Joe turns the evidence
into a human explanation; every evidence object/event is itself an openable
ObjectRef. Unauthorized objects deny before any data is returned.

`server/objects/causality.py::CausalResolver`

### 11.6 Impact analysis

Reverse-dependency impact: who/what depends on this object (agents using a
provider/model, work packages assigned to an agent, automations using a
provider). Every impacted object is filtered through the authorized resolver —
knowing an object is related never grants authority to read it, and impact
analysis never leaks protected identities or counts.

`server/objects/resolver.py::ObjectResolver.impact`

### 11.7 Object comparison

Type-aware comparison of compatible objects (`/api/v1/objects/compare`): models
compare health/provider; providers compare availability/privacy/streaming;
agents compare role/capabilities/availability. Different-type comparisons are
rejected. The renderer shows meaningful type-specific differences, never
generic JSON.

`server/objects/compare.py::compare_objects`

### 11.8 Object intelligence API

| Endpoint | Purpose |
|----------|---------|
| `GET /api/v1/objects/{type}/{id}/activity` | human-facing timeline |
| `GET /api/v1/objects/{type}/{id}/impact` | reverse-dependency impact |
| `GET /api/v1/objects/{type}/{id}/why` | structured causal evidence |
| `GET /api/v1/objects/compare` | type-aware comparison |

All routes are session-gated (401 without an application session).

---

## 12. Tests

`tests/objects_system_test.py` covers: ObjectRef resolution/round-trip, type
registry + unknown-type safe failure, capability calculation, lifecycle/policy
denies, authorized/unauthorized resolution, adapter-error safety, relationships,
action safety levels, and self-describing metadata.

`tests/objects_intelligence_test.py` covers: semantic status normalization,
relationship ranking, the activity timeline store, the causal resolver, type
aware comparison, and security review (impact never leaks unresolved objects,
resolvable dependents are returned).

Browser behaviors are covered by jsdom suites (`test_palette`, `test_quicklook`,
`test_recents`, `test_attention`, `test_nav`, `test_dedup`, `test_compare`,
`test_quicklook2`, `test_snapshots`, `test_density`, `test_undo`) and the
frontend regression (`tests/frontend.test.mjs`).
