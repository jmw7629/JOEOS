# Getting started with JoeOS Automations

JoeOS can run background AI agents after you close the browser.

1. **Open** `/os/automations` (pair/authenticate once like `/os/agents`).
2. **Select New Automation**.
3. **Enter the objective** in normal language (e.g. "Report the health of the
   local agent system in three bullets. Do not modify anything.").
4. **Select the agent** (Auto/Joe, Architect, Builder, Researcher, Verifier,
   Security).
5. **Choose the schedule** — Once, Recurring, or Condition Watch.
6. **Confirm the timezone** (IANA, e.g. America/New_York). The resolved
   structured schedule is shown; review it before creating.
7. **Review** the human-readable summary.
8. **Create** — the automation is persisted with a deterministic schedule.
9. **Close the browser** — the backend scheduler continues.
10. **Return later** and open `/os/automations` or the Notification Center.
11. **Open the notification** — it deep-links to the exact AutomationRun.
12. **View the AgentRun/TaskGraph/result** — the run detail shows agent,
    provider, model, result, retries, and approvals.

Controls: Run Now, Pause, Resume, Archive. Run Now uses the same execution path
as a scheduled occurrence (no special browser execution).
