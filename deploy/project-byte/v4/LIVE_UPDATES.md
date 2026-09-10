# Workspace updates

The existing dashboard now has a shared Updates drawer and a compact notification bell on phones. New observed activity, server notifications, followed Codex messages/tool milestones, and genuine runner permission requests create right-side cards. Cards fade after eight seconds; hover or keyboard focus pauses dismissal. Clicking opens a full detail view. Only three cards display at once (one on narrow phones); all observed items enter history. No synthetic activity is emitted to make an idle system look busy.

History retains the latest 500 observed events per signed-in identity/access key in this browser, encrypted with AES-GCM using a key derived from the access key. No credential or plaintext message is written by this feature. The drawer warns when persistence is unavailable. Reload restores history; sign-out, access changes, and invalid authorization discard visible/in-memory history and abort requests. Another account does not inherit private agent messages. Server notifications and activity can be recovered from their existing API windows (120 notifications, 200 activity rows); this is not an unlimited or cross-device archive. Rotating credentials makes the old encrypted device history inaccessible. Browser storage deletion also removes this local copy.

The popup preference is saved on this device. Existing in-app notification preferences, quiet hours, agent-popup preferences, and reduced-motion settings are respected. Muting popups does not remove history. Mark all read changes the device history only; the original Settings notification center retains its server read controls. Permission details link to the authoritative current request; viewing, dismissing, or marking a notice read never approves, denies, resumes, publishes, or merges anything.

## Update cadence and limits

- While this tab is visible: tasks, projects, activity, bridge runs, notifications, and intelligence are read approximately every two seconds after the preceding request completes. Requests do not overlap. Failures back off to 30 seconds and retain the last known data. The bell/drawer displays freshness and reconnect state honestly.
- The selected active Codex run continues its existing 750 ms event polling while navigating elsewhere, including Home. The native permission inbox checks across conversations every three seconds on all views. This does not start any run or subscribe to every historical conversation.
- Models, agent configuration, settings, collaborators, and memory refresh every 30 seconds. Existing health/bridge sources retain their upstream collection intervals; a fast display cannot make stale upstream evidence current. Browser tabs pause the new main loop while hidden and reconnect on return.
- Data and history keep updating during editing, but visual redraws wait until forms, dragging, move sheets, or the history drawer are finished. Selectors retain the current chat/terminal context. Data redraws occur only when snapshots change.

This is bounded near-real-time polling, not a server-push transport. True push delivery and complete cross-device event replay require a backend event stream and durable retention protocol. No backend, database, registry, service, worker, execution authorization, or ownership changes are part of this update.

## Verification

`test_live_updates_browser.mjs` uses an isolated real backend to exercise incoming task events while a draft is open, popup detail/fade, encrypted history after reload, offline/reconnect, phone bounds, muted history, sign-out and cross-account isolation, and HTML-as-text rendering. It is included in `test_home_browser.mjs` alongside existing Home, Observatory, permissions, Spotify, and Kanban acceptance.

`test_codex_conversation_browser.mjs` uses synthetic API fixtures to check real event-route consumption, native message/tool output, shared permission popups, popup preferences, continuing the followed run on Home, replay deduplication, and no unexpected execution. Synthetic fixtures are never sent to production.
