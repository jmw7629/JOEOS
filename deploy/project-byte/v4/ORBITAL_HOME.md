# PRFKT_PROJECT Orbital Home

Implements the approved space-themed command-center direction in the existing application. This is executable UI, not a screenshot overlay or a separate demo.

## Scope
Earth hero, original inline vector icons, embossed dark cards, responsive metrics, agent graph, execution evidence, organization, work, review ledger, memories, and portfolio progress. The five-primary dock is paired with an accessible all-workspaces dialog so Kanban, Models, Team, Terminal, Activity, Work Next, and Help remain reachable.

Owner/priority/status scopes compose instead of resetting one another. A selected person with no tasks produces an empty result, not a fallback to all tasks. The due-soon tile uses the same date calculation as the filters. Running and queued counts are distinguished. Personal startup-view settings remain authoritative.

The agent inspector consumes an explicit Home-render event instead of attempting to replace a private IIFE function. Modal controls stay above the dock; Escape and backdrop dismissal work. AI_BYTE chat retains an unsent draft on failure and rejects double submission while a send is pending. The duplicate Home composer and Ask AI action have been removed; conversations open in Chat.

## Evidence
`test_home_browser.mjs` reconstructs the actual deployment sources in a disposable runtime, with its own database, fake test-only key, and a GitHub stub that refuses execution. It tests 320/390/768/1440 pixel layouts, all twelve workspace routes, login, combined Joe/Mike filters, due-today matching, live-inspector refresh, persisted task creation, Escape dismissal, and saved startup-view preference. It rejects browser exceptions and external resource requests.

Local browser acceptance ran in Chromium 152 through the installed Playwright package. This is browser emulation, not a physical iPhone or native Safari certification. The existing owner-hold tests and deployment governance tests also ran. GitHub's UX gate now executes browser acceptance in addition to existing checks.

## Boundaries
No application database/schema/auth changes, no live task seeding, no bridge start/stop, no owner-hold removal, and no Funnel changes. Status and counts come from existing APIs. The design mockup's example people, completion percentages, and online statuses are not seeded into production. PRFKT_PROJECT review decisions remain non-merging.

## Earth asset
Source: NASA Earth Observatory, Blue Marble 2002, https://science.nasa.gov/resource/blue-marble-2002/ . Download: https://assets.science.nasa.gov/dynamicimage/assets/science/psd/solar/2023/09/1/1-bluemarble_west-1.jpg?crop=faces%2Cfocalpoint&fit=clip&h=800&w=800 . Re-encoded as a 640-pixel WebP (45,338 bytes), embedded in Home CSS to avoid runtime external requests. Attribution is visible on Home. No NASA endorsement is implied. Usage guidance: https://www.nasa.gov/nasa-brand-center/images-and-media/ .

## Focused workspaces and live crawl
The legacy global metrics are retained as hidden internal DOM targets, not shown on any workspace; the Home KPI cards are the executive overview. Existing filter elements now live in an on-demand native dialog so identities, saved views and filter handlers are preserved. Every non-Home view shows its own title immediately. Chat puts its composer before the collapsible optional routing controls; routine refresh preserves selected project, agent, model and terminal run.

Home replaces the static mission paragraph with a continuous, read-only activity crawl sourced from existing task/activity/run APIs and health evidence. It refreshes at 15-second intervals independently of editor refresh, has a 10-second request timeout, suppresses overlapping polls, ignores outdated session responses, pauses while the document is hidden, and offers pause/resume plus a reduced-motion readable strip. Failed reads display STALE instead of claiming the feed is current. Updates contain recorded blockers/deadlines/progress, actual execution/owner holds, reviews when observed, and model-test evidence; no invented agent progress or raw shell output is used.

The focused browser regression suite verifies all eleven non-Home workspaces have neither the metric wall nor exposed filter controls; the content and chat composer start in the first phone viewport. It exercises the real filter dialog, continuous movement, pause/resume, reduced motion, polling while a draft is focused, escaped event content, failure/recovery states, and preserved routing selections. Existing four-width, owner-scope, task persistence and inspector tests still run.

## Current targeted refinements

The six Home counters open exact matching records with existing task, board and run actions. Project Lead filtering supports named and unassigned leads while preserving the other scopes. The Home layout remains intact; the duplicate chat composer and AI CHAT status badge are removed.

The embedded Webb Carina Nebula background follows bounded phone orientation when supported and permitted. Reduced motion disables movement, and the old embossed/motion toggles are removed. See [Conversation UI](CONVERSATION_UI.md) for the source attribution, mobile remote layout, optional message popups and browser acceptance. Native execution permissions are connected separately from read-only historical traces.
