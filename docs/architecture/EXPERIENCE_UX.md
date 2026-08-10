# JoeOS Product-Experience Polish (UX / Interaction / Accessibility)

Phase 17 refines the production JoeOS shell — the single-file React Command
Center in `index.html` — into one coherent operating-system experience. It
extends the existing workspace theme engine and design primitives instead of
introducing a second design system. Every new surface reflects authoritative
service state; no decorative mocks, dead controls, or placeholder routes were
added.

## Experience principles (as implemented)

1. **Clarity before decoration** — status always carries text + shape; color is
   supplemental.
2. **Real state only** — banners, status badges, settings, and palettes read
   real runtime, security, and performance state.
3. **Keyboard-first** — skip link, focus traps, focus restoration, and a
   centralized shortcut registry.
4. **Responsive by design** — grids collapse intentionally; coarse-pointer
   devices get larger touch targets; page-level horizontal overflow is blocked.
5. **Honest uncertainty** — unmeasured metrics stay unknown; offline/degraded/
   low-resource conditions are shown as banners, not hidden.
6. **Calm feedback** — a single `role="status"` toast and a grouped banner
   region replace scattered feedback.

## Design Token Registry

One authoritative token set now lives on `:root` in `index.html`, with
semantic aliases over the existing theme variables:

- Color: `--color-background-canvas/surface/elevated/overlay`,
  `--color-text-primary/secondary/muted`, `--color-border-subtle/strong`,
  `--color-focus-ring`, `--color-status-info/success/warning/error/critical/
  security`, `--color-action-primary/destructive`, `--color-selection`,
  `--color-code-added/removed`.
- Spacing: `--spacing-control-inline`, `--spacing-panel`.
- Radius: `--radius-control`, `--radius-panel`.
- Elevation: `--elevation-popover`, `--elevation-dialog`.
- Motion: `--duration-fast/standard/slow`, `--motion-ease`.
- Z-index: `--z-dock/topbar/popover/dialog/toast/skip-link`.
- Touch: `--touch-target`.
- Surface: `--surface-blur`, `--surface-blur-strong`.

These are consumed by the new primitives (status badges, banners, settings,
skip link) and remain backward-compatible with the existing component styles.

## Themes and presentations

The shell applies presentation attributes on `<html>`:

- `data-theme` — `dark` (default), `light` (contrast-checked token set), or
  `system` (resolves via `prefers-color-scheme`). Light mode reuses the
  existing token structure with an independently chosen palette; the dark-only
  workspace canvas/text hex overrides are skipped in light mode so the light
  set cannot be corrupted by dark hexes.
- `data-contrast="high"` — stronger text/border/focus contrast for both themes.
- `data-effects="reduced"` — disables `backdrop-filter`/glow on glass surfaces.
- `data-motion="reduced"` — kills animation/transition durations (mirrors the
  existing `prefers-reduced-motion` media query).
- `data-density="compact|balanced|comfortable"` — spacing scale.

The Performance Platform's low-resource/metered modes automatically force
`data-effects="reduced"` so the UI visibly flattens when the system is
constrained — never silently.

## Settings workspace

A new `settings` workspace exposes Appearance (theme, density, visual
effects), Accessibility (motion, contrast, larger interface text), the
keyboard-shortcut registry, and About. Preferences are persisted to
`localStorage` (`joeos:ui-prefs`), validated on load, and applied immediately.
Settings cannot reduce security, approval, or cancellation clarity.

## Keyboard Shortcut Registry

One authoritative `KEYBOARD_SHORTCUTS` array drives both the Settings list and
the Shortcuts reference dialog (`?` or `Ctrl+/`). Registered shortcuts:

- `Ctrl+K` command palette · `Ctrl+Shift+K` AI assistant
- `?` / `Ctrl+/` keyboard reference
- `Ctrl+,` settings · `Ctrl+Shift+N` notifications · `Ctrl+G` palette search
- `Alt+1..0` switch workspace · `Esc` close/stop

## Command Palette

The palette now ranks results (prefix > substring > fuzzy subsequence),
groups by category, shows shortcuts, supports arrow-key navigation with Enter
activation and `role="option"`/`aria-selected`, traps focus, and marks
security-sensitive commands with a distinct `risk` label. New commands include
Open Settings, Open keyboard shortcuts, Stop current operation, Focus main
content, and Open Security Center.

## Focus management

- A `skip-link` is the first focusable element and moves focus to
  `#main-content` (which carries `tabIndex={-1}`).
- A reusable `useFocusTrap` utility is applied to the palette, shortcut
  dialog, widget catalog, customization panel, deploy/profile dialog, node
  detail dialog, and the mobile drawer. It saves the previously focused
  element on open, traps Tab, and restores focus on close.
- Dialog Escape handling is centralized in one global handler with the correct
  precedence.

## Status semantics

A `StatusBadge` primitive renders state as text + shape (never color alone)
and maps a normalized vocabulary (running, active, healthy, connected,
queued, waiting, idle, starting, paused, blocked, degraded, stale, failed,
cancelled, critical, offline, unavailable, unsupported, unknown, restricted,
quarantined, revoked) to consistent tone classes. It is applied to production
surfaces (for example, Automation workflow enablement/health).

## Feedback

- Toasts remain a single `role="status"` region for brief, noncritical
  feedback (they are never used for approvals, destructive confirmation, or
  security incidents).
- A persistent `banner-stack` region (`aria-label="System conditions"`)
  surfaces Lockdown active, Lemonade offline, low-resource mode, and critical
  disk pressure — each derived from real runtime, security, and performance
  state, with an action to the relevant workspace where one exists.

## Interaction and safety

- The AI assistant is cancellable: an abort controller stops in-flight
  generation and reports "Generation stopped by the operator." honestly.
- Coarse-pointer media query raises touch targets to ≥44px.
- Reduced motion, reduced effects, high contrast, and large text are all
  reversible, validated settings.

## Honest limitations

- Light theme and high-contrast values were chosen for contrast by review;
  no automated contrast-measurement tool is available on this host, so the
  values are documented as reviewed-by-design, not tool-verified.
- Screen-reader and browser-zoom E2E scenarios require a browser automation
  runtime that is not installed here; they are documented as manual-review
  requirements rather than claimed as automated passes.
- Localization is not shipped; user-facing strings remain centralized enough
  (in `index.html`) for a future pass.
- No visual-regression screenshot infrastructure exists in the repository;
  structural frontend tests (`tests/frontend.test.mjs`) cover the new
  primitives instead.

## Universal interaction grammar (D24)

JoeOS uses six nearly-absolute behaviors across browser, SwiftUI, and Android.
Once learned, the whole OS becomes predictable:

| Gesture | Meaning | Notes |
|---------|---------|-------|
| tap/click object | **open** | the object card body is the object; no separate "View/Open" buttons |
| select | **inspect** | desktop inspector shows Object Quick Look (identity, state, relationships, primary action) |
| long press / ellipsis | **secondary actions** | overflow menu (pin, focus, move, reset) |
| drag | **rearrange** | workspace/module layout with persisted order + undo |
| Return control | **back one JoeOS context** | named internal return ("← Agents"); never requires the browser Back button |
| Joe orb | **intelligence** | the single canonical Joe invocation; context is automatic from the active ObjectRef |
| Command palette (Cmd+K) | **find/do anything** | object-native search + actions (Open/Run/Approve/Pin/Preview) |

### Rules that follow from the grammar

- Object cards are `role="button"` + keyboard-openable (Enter/Space).
- No per-card "Open / View / Ask Joe / Check Status" button forests.
- Status is live and automatic; refresh is a diagnostic action, not a workflow.
- Every drill-down surface exposes a visible named Return control.
- The persistent Joe orb is the only normal Joe invocation; per-object "Ask
  Joe" buttons and duplicate prompt fields are prohibited.
- Actions carry a uniform safety language: safe / consequential / privileged /
  destructive (see `docs/architecture/JOEOS_OBJECT_SYSTEM.md` §2.4).
- Reversible actions (layout, pinning, metadata) are undoable.
- Zero-clipping is absolute: text is never accidentally cropped; intentional
  truncation uses ellipsis + full value accessible via tooltip/detail.
