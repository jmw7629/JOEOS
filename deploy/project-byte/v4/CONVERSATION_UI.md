# Conversation and Observatory presentation

The existing desktop layout now opens on readable messages. Chat, Terminal and Graph share the selected native conversation. The Terminal view displays recorded tool commands, output and results; it is not an interactive shell. Commentary and final messages remain in chronological order, with separate records for delegated agents.

Desktop and mobile use the same project history, copy, attachment, model/reasoning and review controls. Desktop keeps task context and dependencies in the side column; mobile exposes them through Context. The composer adapts when navigation expands, and desktop focus preserves scroll position. Scrolling upward in Terminal pauses Follow; enabling it returns to the newest recorded output. See [DESKTOP_WORKFLOW_AUDIT.md](DESKTOP_WORKFLOW_AUDIT.md) for screen-size verification and shared capability limits.

Settings → Chat & agents provides automatic, desktop and mobile layouts plus optional agent-message popups. Mobile prioritizes agent text and real permission requests. Historical activity remains available through Show recorded activity. Dismissing a popup does not answer or deny a request.

Observatory owner sessions can inspect recorded visible prompts, tool arguments, responses, errors and agent messages. Credentials are redacted and content is bounded. Hidden reasoning is excluded. Missing historical tokens and timings remain null and display Not recorded; no estimates are presented as measurements. See OBSERVATORY.md for the audience and evidence contract.

Home keeps its layout. Each of the six counters opens the exact matching records with existing record actions. The duplicate Home composer, Ask AI action and AI CHAT status badge are removed. Detailed health remains available in the existing health views. Projects Lead view opens a summary of projects for that lead, including unassigned projects, with direct work-queue and chat actions. Opening a presentation control does not queue, resume, approve or publish work. Explicit result and permission decisions are described in MOBILE_WORKFLOW_AUDIT.md.

## Design references and image credit

Reviewed Soarcer/synapse public-launch commit f7020f404829b8902650c72418029aae5abfdc8a and the static contents of @synapse-ai/cli 0.1.3 (registry SHA-1 4ab1acd109a92e9529473fdbe2b727c1838c6648). Its public repository contains documentation and screenshots, while the package contains the shipped implementation. No package code was executed or copied. Shared selection, compact tools, readable inspectors and mobile response sheets informed an original adaptation to this dashboard's existing authenticated runner.

The background uses original generated PRFKT nebula artwork embedded in the application. Earth and external astronomy-photo credits are removed. Phone-orientation parallax, permission handling and reduced-motion behavior are preserved. See assets/NEBULA_PROVENANCE.md.

The backdrop follows bounded changes in device orientation when supported, signed in and visible. Reduced-motion preferences disable movement. Browsers requiring a sensor permission expose an explicit Allow orientation access action in Settings. Physical iPhone sensor behavior requires device acceptance; synthetic browser tests cover permission, bounds and reduced-motion behavior.

## Verification

Run test_codex_conversation_browser.mjs, test_codex_trace_browser.mjs, test_codex_workspace_browser.mjs, test_home_drilldown_browser.mjs and test_home_browser.mjs using the pinned isolated Playwright toolchain. Run test_observatory.py for owner/metadata audience separation, credential redaction, missing metrics and hidden-content exclusion. The deployment uses a three-file overlay against pinned live baselines, preserving unrelated runtime integrations and installed state.
