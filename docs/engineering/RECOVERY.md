# Recovery and Continuity

The campaign is designed to survive the backend restarting, the scheduler
restarting, the browser closing, SSH disconnecting, and OpenCode exiting.

## Durable state

All campaign state lives in the main `data/joeos.db`:

- `engineering_campaigns`
- `engineering_work_packages`
- `engineering_attempts`
- `engineering_checkpoints` (append-only, digest-verified)
- `engineering_blockers`
- `engineering_heartbeats`
- `engineering_roadmap`

## Restart recovery

`CampaignService.recover_after_restart()` runs at backend startup:

- active campaigns are scanned
- packages left in mid-pipeline states (`planning`, `implementing`,
  `validating`, `reviewing`, `committing`, `integrating`, `pushed`) have their
  `running` attempts marked `abandoned` and the package reset to `eligible` /
  `eligibility` with `error_detail="recovered after restart"`

**Duplicate prevention**:

- stable `package_id` derived from `campaign_id:key`
- `UNIQUE(campaign_id, key)` on work packages
- `UNIQUE(key)` on campaigns
- terminal runs are never re-run
- completed packages are never requeued

## Browser closed / SSH disconnected / OpenCode closed

The `CampaignWorker` is an asyncio loop in the backend process (30s tick). It
does not depend on the browser, the SSH session, or OpenCode. The Engineering
Director dispatches stages through the authoritative AgentFabric (Ollama +
runner executors) inside the backend.

OpenCode is an optional coding engine adapter
(`runner/joeos_runner/opencode_executor.py`); the native Builder path uses the
runner's filesystem/git/test executors and works without OpenCode running.

## Worker

`CampaignWorker.run()` awaits `tick_async()` each interval. Control via
environment:

- `JOEOS_CAMPAIGN_WORKER` (default `true`)
- `JOEOS_CAMPAIGN_WORKER_INTERVAL` (default `30` seconds)

## Canary proof

`python scripts/canary_engineering_director.py` verifies restart recovery
(no duplicated completed work) and multi-package continuation in a throwaway
database.
