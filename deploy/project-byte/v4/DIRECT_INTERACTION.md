# Direct interaction

Kanban cards support mouse grab-and-drop between existing status columns. A lifted preview follows the pointer, the destination highlights, and the board scrolls at its edges. A successful server save offers Undo. Dropping outside the board, Escape, pointer cancellation, a hidden tab, or a board re-render cancels a pending gesture. Same-column drops do not write.

On touch, the grip starts a drag; card bodies retain native scrolling. Mobile column snapping pauses during manipulation and resumes afterward. Empty board space and Home's horizontal scope chips support mouse grab-to-pan. A click on a scope chip still applies its filter; a drag does not. Card titles open the existing detail form.

Keyboard users focus a grip, press Space or Enter, choose a column with Left/Right (or Home/End), then press Enter/Space to save. Escape or Tab cancels. Status announcements and the original status menu remain available. Motion preferences are respected; the drag preview follows the user's input without decorative animation.

Only signed-in editors and owners can move cards. The existing authenticated PATCH endpoint remains authoritative. Moves change only `status`: they do not reorder a column, alter priority/ownership/progress, approve tools, queue an agent, or execute a project. During saving the board is busy and temporarily inert. A rejected save leaves the original local status; a preflight read rejects a move or Undo if the task was deleted or its status already changed. This is a preflight check, not an atomic compare-and-swap; the existing API retains last-writer behavior for strictly simultaneous edits.

The interaction module is embedded in `home.js`, so existing deployed static-route allowlists continue to work. It extends the existing card/render functions and refresh-edit guard. No database schema, runner, authentication or project-registry changes are required.

## Verification

`test_direct_interaction_browser.mjs`, called by the isolated Home browser fixture, covers real persisted mouse and keyboard moves, reload, Undo, cancellation, read-only access, rejected PATCH requests, changed status before drop/Undo, refresh cancellation, narrow-board panning, Chromium touch events with edge scrolling, four viewport widths, and absence of execution requests. Physical iPhone/Safari behavior still requires a device check; emulated Chromium touch is recorded separately from hardware evidence.
