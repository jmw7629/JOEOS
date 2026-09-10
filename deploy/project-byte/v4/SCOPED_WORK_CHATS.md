# Scoped work chats

Project and task AI actions now open a named work window. Each window owns its project connection, optional task and dependency context, conversation/run identifiers, draft, waiting request and unread state. Reopening the same project/task action returns to that window; New chat creates an independent conversation. Sending explicitly submits that window's request to the existing owner Codex API.

The original defect was a mismatch between legacy `aiProject`/`aiTask` selectors and the native controller's single `projectKey`/`conversationId`. All owner AI action entry points now use one scoped controller. Task AI buttons no longer PATCH executor/model fields as a side effect of opening a chat. Existing server conversations cannot be rebound to a different project. Unmapped or ambiguous projects require an explicit connection selection.

The mobile audit update adds a compact Chats menu, a project-filtered History dialog with explicit pagination, message copy, a larger composer and per-message model settings. See MOBILE_WORKFLOW_AUDIT.md for local validation and the remaining capability gaps. The live hashes at the end of this document describe the preceding scoped-chat release, not deployment of the mobile audit update.

## Work lifecycle

1. Open a project chat, task chat, planning/help window, or independent New chat.
2. Inspect the bound project and task dependencies under Context; write a draft.
3. Send creates one immutable request with an idempotency UUID and a readable user request followed by reference context.
4. The existing single runner executes one task at a time. Other messages wait in their own windows. Current task existence, project ownership and prerequisite Done status are checked before a task request is submitted. A blocked prerequisite does not prevent unrelated ready work.
5. Navigate freely. Accepted execution remains on the server. Known background runs are observed separately, including paginated terminal events. Notification history opens the originating conversation.
6. Review tool permissions and results using the existing context/terminal/graph surfaces. Publication approvals and no-auto-merge rules are unchanged.

Drafts, window metadata and waiting requests are AES-GCM encrypted in this browser's local storage, scoped to the signed-in identity and access key. This does not protect against malicious code running in the authenticated browser. Server messages and runs remain authoritative. Up to 40 open windows are retained; closing an idle window preserves its server conversation history. New generic windows adopt the first request as their title.

Waiting-message dispatch requires this browser tab to remain open and visible. Reload restores unsent work paused for explicit resume. A submission with an uncertain result remains uncertain after reload and can only retry its original request ID. This is not a durable cross-device queue or multiple concurrent workers. An accepted run continues independently of dashboard navigation or reload. Browser drafts are not synchronized between devices or simultaneous browser tabs.

Owner-native execution is covered by this change. Collaborator roles retain the existing legacy controls and cannot access the owner runner. Existing authentication, settings, backend/database, worker ownership and protected/paused projects are unchanged.

## Process review and reference

Reviewed ClawPort's public source and documented separation of task chats, agent conversations and persistent activity: https://github.com/JohnRiceML/clawport-ui. Read `lib/conversation-store.ts` and `lib/kanban/chat-store.ts` as design references. No ClawPort code or OpenClaw runtime was installed. The user's other reference, “subtypes,” was not identifiable without clarification; no app identity was assumed.

## Verification

Synthetic provider-free browser coverage is in `test_scoped_work_chat_browser.mjs`: two projects, distinct requests/results, saved drafts, navigation/background observation, notification routing, dependency scheduling, reload/resume, ambiguous acceptance and same-ID retry, unmapped-project blocking, task/project/help entry points, encrypted storage, logout/login and 320/390/1440 layouts. No model calls or GitHub issues are created by these tests.

Existing native conversation, trace and workspace tests cover terminal output, pagination, permissions, stop, reconnect, XSS, project readiness, unavailable/observed project boundaries and late responses after logout. The full isolated runtime browser suite covers Home, mobile navigation, task editing, drag/Undo, notifications, preferences and permissions. Fixture interaction paths were updated to use New chat and the remaining Context refresh button; completed conversations are intentionally immutable in project scope.

Live verification is read-only after a backed-up, hash-checked update of `home.js` and `codex-workspace.js`. No installer or worker restart is used. Local Home includes separately pending Spotify work, so live Home receives only the three notification-routing line changes from this feature, not the entire local file.

Live static-file verification passed at 320, 390 and 1440 pixels: the real PRFKT_PROJECT project button bound to `joeos`, New chat created another independent window, navigation preserved selection and the composer stayed above the reserved dock. Codex reported configured/connected in execute mode. The verification browser made no task, message, permission or settings mutations. Served SHA-256: Home `5f56bcc1412515f3f671d655956de79fd60f5ed974c5cb2fe205a1d72e8b985c`; Codex UI `e5b9058e76dde5b108100be45575c8851a839e6506449e63cd4c455c2e59d284`. Physical-phone and real model execution were not part of this read-only live check.
