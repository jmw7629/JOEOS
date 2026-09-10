# Desktop workflow audit — 10 September 2026

This extends the [mobile workflow candidate](MOBILE_WORKFLOW_AUDIT.md) to desktop in the same draft PR 59. Repository: `jmw7629/JOEOS`; review branch: `feat/project-byte-ai-connections`; release base: `project-byte-deploy`. The starting local commit was `0dcdc18`; its tree matched remote `a6e34ea57f526e08ca50eb0df5631004c56159bc`.

The existing desktop layout, Home design, authentication, data and settings are preserved. No production deployment, worker restart, project execution, recovery restore or ownership change was performed. Work used one agent and disposable local browser fixtures.

## Shared behavior verified on desktop

- Project Lead view opens the lead summary and its work actions.
- Project and task chats retain independent drafts, context and server conversation history across navigation and reload. History supports project filtering, search and explicit pagination.
- Copy, text/CSV reference attachments and model/reasoning controls use the same implementation on both layouts. A model selection does not choose the specialist; the connected runner handles that routing.
- Chat, Terminal and Graph retain the selected conversation. Graph tool selection and focus/escape work with desktop controls.
- Ready for review contains actual permission requests with Approve once / Deny. Result approval remains a separate task-status decision. The fixture verifies the exact review token and one submitted desktop approval.
- Important background messages and failures are retained in Updates. Routine idle desktop activity does not repeatedly fetch the task list or initiate model work.

## Desktop defects corrected

The mobile context rule also hid the selected task's context on desktop, where the mobile Context button is absent. The desktop sidebar now contains the existing context disclosure, including repository, task, dependency and Close window. On a mobile layout that same disclosure returns to the Context controls. It is not a second copy with separate state.

The workspace no longer resets desktop scroll position whenever an input receives focus. Chat and the sidebar share a viewport height calculation, and a resize observer adapts them when navigation expands. Opening task context scrolls inside the sidebar rather than taking space away from messages. Desktop Graph uses the available height instead of inheriting the phone's fixed canvas size.

Terminal Follow previously inspected the scroll distance before a wheel gesture took effect, so the first upward gesture could leave follow enabled. Upward wheel movement and keyboard Home/Page Up/Arrow Up now pause follow; scrollbar or touch scrolling away from the latest output does too. Follow returns to the newest recorded output and its label reflects its state. Switching back to Terminal while following also reaches the latest recorded output.

Dictation fallback wording now applies to desktop keyboard dictation as well as phones. Microphone capability still depends on the browser and device.

## Validation

The scoped-work browser suite exercises 1024 × 768, 1280 × 720, 1440 × 900 and 1920 × 1080. It checks context/dependencies, history search, model controls, input tools, Terminal scrolling, Graph selection/focus, navigation expansion, desktop/mobile resize, Lead view, review permissions, preserved focus scroll and idle API traffic. Existing mobile keyboard and scoped-chat checks remain in the same suite.

| Desktop viewport | Message area | Composer | Terminal output area |
| --- | ---: | ---: | ---: |
| 1024 × 768 | 221 px | 90 px | 164 px |
| 1280 × 720 | 173 px | 90 px | 116 px |
| 1440 × 900 | 353 px | 90 px | 296 px |
| 1920 × 1080 | 533 px | 90 px | 476 px |

Measurements use the synthetic fixture with navigation collapsed. At 1280 × 720 the preceding candidate provided 141 px for messages. Content scrolls within the measured areas. The expanded navigation is separately tested not to cover the composer.

Run `test_scoped_work_chat_browser.mjs`, `test_codex_conversation_browser.mjs`, `test_codex_workspace_browser.mjs`, `test_codex_trace_browser.mjs` and `verify_ai_packaging.py`. Desktop coverage is included in the existing CI workflow. Screenshots and raw measurements are kept outside the repository in `evidence/desktop-workflow-audit/`.

The limits in the mobile audit also apply to desktop: full image/document attachments, device/app/password integrations, automatic chat-to-Kanban lifecycle and complete Codex/OpenClaw/Hermes capability parity are not implemented by this UI change. Browser fixtures do not certify real microphone behavior or actual provider execution. The candidate remains unmerged and undeployed pending release review and the previously required native broker acceptance.
