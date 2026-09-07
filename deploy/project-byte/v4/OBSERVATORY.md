# PROJECT_BYTE Agent Observatory

Open **Agents** in the existing dashboard. Home, the activity crawl and all other workspaces remain unchanged. The original agent-role cards, existing run list and memory tools are retained in an expandable section below the Observatory.

## Interaction
Choose a project and recorded run, then use Tree, Treemap, Sankey or Timeline. Selecting a graph node, lens rectangle, linked sidebar item or tool event uses one shared selection and inspector. Tree supports zoom, Fit, mouse dragging, touch scrolling and arrow/bracket-key navigation. Focus enlarges the workspace with a visible exit control; Escape exits it. Pause live freezes automatic snapshot refresh, while Refresh remains available manually.

Tools have a pill grid, timestamped swimlanes and a tool-by-recorded-status frequency matrix. Treemap area uses event count, recorded tool duration or recorded model-step token totals. Unknown metrics are excluded instead of treated as zero or estimated. Timeline shows explicit compaction markers only when recorded; it does not invent compaction history. Metadata inspection includes ancestry, measured timing, event state, token measurements, argument field names and response length. Argument values, response bodies and model text are withheld to protect secrets.

## Data and safety
`GET /api/observatory` requires Editor or Owner access. It reads only fixed BYTE/VITROS bridge state directories, never caller-supplied paths, legacy R3 repositories, environment files or arbitrary session databases. It accepts regular allowlisted issue/verification logs, refuses symlink logs/directories, caps directory scanning at 1,000 entries, reads at most six recent logs per repository and 256 KiB per log, and returns at most 320 events per log. Partial/truncated history and unavailable sources are explicit. Stable hashed identifiers link delegated-agent references; child content is not guessed when missing.

The read-only adapter uses a 15-second cache; the UI polls only while Agents is active and the document is visible, with cancellation, timeout and session-change checks. No new package runtime dependency, hook installation, shell commands, remote approval relay, model invocation, production fixture seeding, bridge restart or owner-hold change is introduced.

**Recorded history is not live-process proof.** A `running` status within a log describes that event when recorded. The existing evidence-backed Home execution health remains authoritative about current processes and owner holds. Buffered worker logs cannot expose events before the worker actually writes them.

## Deliberate non-parity
Execution permission interception is not connected. The app does not pretend existing PR review decisions can approve a tool or resume a paused agent. Genuine remote Approve/Deny with a message requires an authenticated, correlated permission-capable runner adapter, expiry/replay protection and exact-request auditing. Do not enable Synapse's unauthenticated LAN relay or clear BYTE's owner hold to simulate this capability. Raw payload disclosure and unrecorded token/compaction metrics are not implemented. These limits are visible in the product.

## Reference review
Reviewed the official usesynapse.dev feature page, Soarcer/synapse README and the linked YouTube interaction demo `j4WqJHcVUc4` in Chromium. The normal watch page played; the embed initially returned error 153. Inspected graph navigation, grouped tools, lens highlighting and the metadata inspector from the actual video frames. The installed Synapse CLI describes Claude Code hooks, whereas PROJECT_BYTE's current workers use OpenCode. This is an original native implementation over existing execution records, not an iframe, copied landing page or substituted sample dashboard. No third-party source was copied.

## Verification
`test_observatory.py` validates payload non-disclosure, stable de-duplication, unknown metrics, recorded tokens/delegation, symlinks, malformed/truncated logs, bounded history, legacy-repository exclusion and absence of shell execution. `test_observatory_browser.mjs`, imported by the required Home browser gate, uses disposable log fixtures and tests API authorization, real parsing, all lenses, cross-selection, keyboard/zoom, focus exit, all tool grouping modes, search and mobile/desktop overflow. Production acceptance must use existing logs without seeding test data and must not claim physical iPhone/Safari certification.
