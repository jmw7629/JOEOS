# Mobile workflow audit — 10 September 2026

The mobile defects are reproducible. This change fixes the existing workspace rather than rebuilding it. It is a review candidate; it has not been installed on JoeVPS. Production databases, project registries, credentials, recovery copies, services and worker ownership were not changed. The incoming-project Codex automation remains paused. No additional agents were launched.

Repository: `jmw7629/JOEOS`. Review branch: `feat/project-byte-ai-connections`, with `project-byte-deploy` as the release base. The starting local commit was `61cee74b5edad9ca90c043a621d115070787d477`; its tree matched remote `cf490b6c90568145bb43321bc2d33dbfb8913147`. Existing draft PR: https://github.com/jmw7629/JOEOS/pull/59. Handoff: https://github.com/jmw7629/JOEOS/issues/49.

## Findings and changes

| Area | Verified defect | Change in this candidate |
| --- | --- | --- |
| Project Lead view | Clicking applied a filter; no lead summary opened. An unassigned lead looked like a broken action. | Opens a lead dialog with project status, open/review/blocked counts, and queue/chat actions. |
| Mobile chat | Large window headers and context controls consumed the viewport. A 390 × 430 keyboard-height emulation left only 55 px for messages. | Compact Chats menu, larger composer, viewport-aware sizing, and collapsed navigation controls while typing. The same viewport now leaves 187 px for messages. At 390 × 844, observed message space grows from 247 to 408 px. |
| Terminal / Follow output | Header plus output exceeded the containing panel. The Follow button changed state without a clear label. | One bounded scrolling output area; Follow/Following label and pressed state reflect the setting. |
| Graph | Content exceeded its fixed panel and was difficult to reach on a phone. | Scrollable graph panel and bounded canvas; selection, zoom, focus, inspector and permission review remain available. |
| Project history | Saved conversations existed, but were hard to discover; the catalog exposed only the latest 100 globally. | Project-filtered History with title search and explicit Load older conversations. Owner-authenticated HTTP pagination reaches older records, including records for projects no longer registered. Equal timestamps have a stable ID tie-breaker. |
| Chat tools | No convenient message copy or per-chat model selection. | Copy on messages; optional model/reasoning dialog backed by runtime-advertised choices. Astra/ultra remains the default. Accepted requests and their specialist sessions retain the selected model/effort. |
| Input | Composer was narrow and lacked attachment/dictation entry points. | Full-width composer, text/CSV reference-file input, and browser dictation with a phone-keyboard fallback. Capability limits are listed below. |
| Updates | Two-second whole-dashboard scans and routine event popups were distracting. Refresh requests could be lost while a read was in flight. | Event-focused notifications; whole-dashboard reads on load, navigation, return, mutation or explicit refresh. Overlapping refresh requests coalesce into one follow-up read. Active execution still streams recorded progress through bounded polling. |
| My work | Exact owner-name matching omitted work led by the owner and unassigned owner work. | Includes assigned work, project-lead work, and unassigned work for the owner. Explicit filters still apply; the card shows four priority items with a link to all work. |
| Portfolio pulse | An unconditional five-project cap hid the rest. | Includes every project in the current dashboard/filter scope, including projects with no tracked tasks. It does not invent projects or statuses absent from the data. |
| Kanban | Manual status controls dominated, with no obvious next step or central permission queue. | Suggested next action on each task; manual moves are under a disclosure. Ready for review contains the existing authentic permission inbox and explicit task-result review actions. |

## How the work queue should be used

1. Describe the outcome in a project or task chat, then send it. Codex selects its tools and specialist roles within the connected runner's actual capabilities.
2. In progress means work is underway. Blocked means a prerequisite or answer is needed; the card offers a relevant next step.
3. Ready for review is the main place for decisions. **Approve result** marks that task Done after a fresh status read and confirmation. **Request changes** opens its work chat with a draft for the requested changes.
4. **Approve once / Deny** on a real permission request applies only to the stated tool action. It retains the existing owner check, review token, request identity, expiry and decision handling. It does not stand for approval of other work or an automatic merge.
5. Open a work chat to revisit its progress and result. Closing an idle window does not delete its server conversation.

The candidate does not automatically create/update a Kanban task for every general chat, move every finished native run into Review, or turn result approval into a publication approval. Durable linkage between native conversations and task lifecycle is still required for that full hands-off workflow. Existing browser waiting requests still require a visible tab to dispatch; already accepted runs continue on the server.

## Capability limits that remain

- **Full attachment support:** the new input accepts UTF-8 text, Markdown, CSV, JSON and log files up to 16 KB into the current draft. It does not yet send images/camera captures, PDFs, Word or Excel workbooks to the runner. Those need authenticated attachment storage, extraction, model input support and lifecycle tests. Existing task uploads are unchanged.
- **Voice:** microphone input uses the browser's speech-recognition API when available. Phone keyboard dictation is the fallback. This is not Codex realtime voice, and physical iPhone microphone behavior has not been verified.
- **Speed:** reasoning effort is selectable when advertised; it is not a guaranteed response-time or Fast service-tier setting.
- **Apps/devices/secrets:** the existing runner has explicit isolated project tools. It does not inherit this Codex app's connections, a Mac/phone control channel, or a password manager. Those require authenticated, scoped integrations; no blanket host access or credential exposure was added.
- **OpenClaw/Hermes parity:** neither complete runtime nor its capability set is embedded by this change. Automatic specialist selection already exists, but can only use actual connected tools.
- **History and push:** server conversation history is durable. Browser drafts and the latest 500 observed notifications remain encrypted device-local state. Full cross-device draft sync, durable waiting dispatch, server push, and background phone notifications are separate work.
- **UI parity:** the chat now follows Codex's conversational layout and common controls, but it is not a literal copy with every Codex feature.

The OpenAI app-server documentation distinguishes runtime model choices and input types from connected app capabilities: https://developers.openai.com/codex/app-server. A model connection alone does not establish device or app access. Hidden reasoning is not exposed; visible progress, messages and recorded tool results remain the explanatory surfaces.

## Validation and evidence

Local, disposable fixtures were used for model/permission/task actions. No test sent a real model request, created a GitHub issue through a runner, approved a production request, or changed live task data.

- Backend: `test_codex_tasks`, `test_codex_connection`, `test_codex_task_rpc`, `test_codex_tasks_runtime`: 94 tests, 90 passed and four environment-dependent tests skipped. Includes per-message model validation/persistence/idempotency and owner-only paginated history.
- Gateway: 32 tests passed, including the new bounded history route, authentication/role checks, duplicate query rejection and existing credential/session protections.
- Guard/overlay: 33 tests passed for owner hold and stage overlays. Owner-hold UI checks also passed.
- Browser: full Home suite passed at 320/390/768/1440 widths, including navigation, filters, task persistence, drag/Undo, notifications, permissions and the existing Spotify fixture. Native conversation, trace and scoped-work tests cover history, drafts, keyboard sizing, model choices, copy, text-file scope, terminal follow, real permission decisions, result review and idle traffic.
- Read-only live comparison used an isolated browser that substituted the edited UI files in its own responses. This was a preview overlay, not a deployment. The 13:30 UTC check reported zero page errors and zero blocked write attempts. Private screenshots and raw JSON are retained locally under `evidence/mobile-workflow-audit/`, outside the repository.
- Physical iPhone/Safari, camera, microphone, actual provider execution, remote-device actions and a deployed candidate are **not verified**. Passing emulation is not device certification or independent code review.

Installer hashes must match the final candidate bytes; no hash guard is weakened. The production installer was not executed. The current live Home file differs from the repository's broader pending work, including Spotify, so a later release must compare exact live bytes and must not blindly replace the entire live directory. No recovery material should be applied as part of this UI release.
