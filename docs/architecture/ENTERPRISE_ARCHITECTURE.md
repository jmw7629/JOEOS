# JoeOS Enterprise Architecture

Status legend: **IMPLEMENTED** · PARTIAL · PLANNED · BLOCKED

## Configuration precedence model

JoeOS separates product defaults from deployment configuration, organization
policy, workspace defaults, user preferences, and device preferences:

1. **JoeOS system defaults** — generic product defaults shipped in code
   (e.g. built-in module catalog, design tokens, default agent bindings).
2. **Organization policy** — provider/tool/module/permission restrictions that
   always override lower levels.
3. **Workspace defaults** — per-workspace module layout and capability defaults.
4. **User preferences** — themes, module ordering, pinned modules, orb position
   (persisted via `joeos:ui-prefs`).
5. **Device-specific presentation preferences** — per-device UI presentation.

Policy restrictions override lower levels; a user may not reorder a module the
organization locked, nor enable a provider the organization disabled.

## Module system (cross-platform contract)

**IMPLEMENTED (contract + catalog + built-in seed):** `server/modules/`
- `ModuleManifest` schema (`server/modules/manifest.py`): id, type, version,
  display_name, description, icon, category, subcategory, route,
  supported_form_factors, required_permissions, required_capabilities,
  commands, actions, data_sources, joe_context, widgets, inspection,
  feature_flags, policy_requirements, min_client_version, visibility,
  ordering, pinned, user_customizable, schema_version.
- Strict validation: safe absolute routes, allowed widget component types,
  allowed `joe_context.kind`, valid visibility. Unknown fields are additive;
  unknown components are rejected (never silently rendered).
- `ModuleCatalog` (persisted, builtin/user/workspace scopes, supersede-on-remove).
- Gated `/api/v1/modules` catalog API.
- Built-in Command Center modules seeded as product defaults.

**Native mirrors (same 26-field schema):**
- Swift: `apps/mobile/Sources/JoeOSCore/ModuleManifest.swift` — verified to
  decode real server snake_case JSON on the Mac.
- Kotlin: `apps/android/app/.../ModuleManifest.kt` (serialization).

**Widget component registry:** browsers/iOS/Android render only trusted
component types (`text`, `metric`, `list`, `agent_panel`, `task_panel`, etc.);
unknown types fail safely. User customization is data (manifests/config), never
downloaded executable code.

## Joe contextual assistant

Joe is the contextual intelligence layer across every module. `JoeContextScope`
carries an authorized object reference (module/object/selection/route); scope
never grants authority. Routing is provider-neutral through `CapabilityRouter`
(`server/ai/routing.py`): capability → ProviderRegistry → eligible provider+model
with health/availability gating, deterministic errors, observable fallback
reasons, and routing metadata. Ollama is the primary usable provider; Lemonade
is routable when healthy with its already-downloaded models registered.

## Browser architecture

- Modular Command Center home with focus-mode, desktop inspector, persisted
  pin/reorder, module-scoped Joe.
- WebCapabilityRegistry (`window.joeosCapabilities()`): honest feature
  detection (push, WebAuthn, mic/camera, share, badge, wakeLock, etc.) — never
  UA-guessing.
- Versioned service worker with navigation fallback; network online/offline
  state with a reconnect banner.
- Mobile: single-column modules, vertical pipelines, touch-first, safe-area
  aware, no hover dependency.

## Native iOS (SwiftUI)

**PARTIAL/IMPLEMENTED:** Native SwiftUI shell + `ModuleRenderer` (trusted
component registry) + `ModuleManifest` contract. JoeOSCore builds for the
simulator on the Mac; renderer typechecks. Full xcodebuild is gated on an Xcode
16.6 Info.plist environment quirk (pre-existing; affects the pristine project).

## Native Android (Kotlin/Compose)

**BLOCKED (toolchain):** Source-complete native project (`apps/android`):
Gradle 8.9, Compose BOM, serialization, JoeOS theme tokens, module contract,
MainActivity shell, network security policy, R8 rules. Building requires a
JDK + Android SDK + Gradle install (no toolchain on Halo or the Mac).

## Platform agents

**IMPLEMENTED:** `engineering.appleplatform` and `engineering.androidplatform`
persistent role agents registered through the engineering agent fabric
(10 engineering roles total). They own native client work via bounded
tooling (Mac build executor / sandboxed Gradle) and never receive signing keys
or unrestricted host access.

## Security boundaries

- No browser/device reaches Ollama or Lemonade directly; only the backend is
  the model client.
- Module manifests are validated server-side; unknown component types and
  unsafe routes are rejected; no arbitrary code from user modules.
- Provider/model selection never silently routes to an unhealthy provider and
  never claims an uninstalled model is available.
- Platform clients keep HTTPS-only (cleartext disabled on Android; network
  security config explicit).
- No secrets in commits; module capability reporting is non-secret booleans.
