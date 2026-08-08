# Scheduling

## Recurrence

Uses the automation platform's structured recurrence (RFC 5545-style RRULE
semantics): daily / weekly / monthly / interval with an `at_time`, weekdays, or
month_days, plus an explicit IANA timezone. The VPS system timezone is never
assumed; the user's chosen timezone is authoritative.

## DST safety

The schedule service handles DST gaps (nonexistent local times run at the next
valid time) and repeats (ambiguous local times run once). `_fold_safe` resolves
to a concrete UTC instant.

## Timezone

Each automation stores an IANA timezone (e.g. `America/New_York`). `next_run_at`
is always persisted in UTC. The browser wizard requires an explicit timezone
selection; natural-language interpretation must be confirmed against the
resolved structured schedule before creation.

## Occurrence identity

`occurrence_key = sha256(automation_id | scheduled_for | definition_revision)`.
A unique index on `(automation_id, occurrence_key)` enforces that repeated
scheduler passes never create duplicate AutomationRuns or AgentRuns.
