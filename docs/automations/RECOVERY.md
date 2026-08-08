# Recovery

## Durable claims / leases

An AutomationRun claim records worker identity, claimed time, and lease
expiration. If the scheduler worker dies, the next pass recovers non-terminal
runs with expired leases (state -> queued, cleared claim) and executes them.
Terminal runs (succeeded/failed/cancelled) are never re-executed merely because
a lease expired.

## Restart recovery

On backend restart, `AutonomousService.recover_after_restart()` recovers
expired leases. Definitions and next_run_at are persisted, so a restart simply
resumes. Occurrence deduplication prevents duplicate AgentRuns after restart.

## Crash recovery

Simulated in tests: a worker claims a run and dies mid-execution; after lease
expiry the run is recovered and re-executed exactly once, and an already
terminal AgentRun is never repeated.
