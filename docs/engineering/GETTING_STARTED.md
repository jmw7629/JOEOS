# Getting Started — Autonomous Build

The intended future workflow:

1. Open JoeOS.
2. Ask Joe (Agents home) or open Build (`/os/build`).
3. Say: **"Continue building JoeOS."**
4. Confirm **Safe Autonomous Development** (Level 2).
5. JoeOS works — selects roadmap work, plans, implements, verifies,
   security-reviews, commits, pushes feature branches, and continues.
6. Return later to **Build / Campaign** to see progress.
7. Respond only when **Needs You** appears (decisions, credentials, device
   actions, approvals, security blocks).

No OpenCode prompt copying. No giant manual prompts.

## Commands Joe can use (natural language steering)

- "Continue building JoeOS." → resume/start the campaign (`/engineering/director/continue`)
- "Focus on the mobile app next." → roadmap steering (future; configure priorities)
- "Pause frontend work." → pause
- "Don't deploy automatically." → autonomy/level policy
- "Continue overnight." → campaign continues in the background worker
- "Stop after the current task." → `pause-after-current`

## Where to see current work

- **Build Command Center**: `/os/build` (mobile-usable)
- **Ask Joe / Agents**: "Continue building JoeOS" button
- Notifications: `PACKAGE_COMPLETED`, `BUILD_BLOCKED`, `CAMPAIGN_COMPLETED`,
  and the handoff categories.

## Conditions that stop the campaign and ask Joe

- product decision required
- credential required
- device/GUI action required
- privileged approval required
- Security block
- repeated package failure beyond the attempt budget
- roadmap has no READY work
- user pauses or chooses stop-after-current

## Starting the campaign manually (operator CLI)

```bash
.venv/bin/python scripts/activate_campaign.py --start
```

`--start` starts the campaign if it is not active. The script is idempotent.
