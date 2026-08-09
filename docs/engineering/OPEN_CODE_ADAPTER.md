# OpenCode Adapter

OpenCode is an **optional** implementation engine for the engineering campaign,
not the campaign authority. JoeOS remains the authority.

## Status

- Adapter: `runner/joeos_runner/opencode_executor.py`
  (`joeos.engineering.opencode`).
- Invokes the documented noninteractive interface:
  `opencode run --format json --dir <worktree> --model <model> "<prompt>"`.
- Safety (all fail closed):
  - model must be in `ALLOWED_MODELS`
  - prompt ≤ 8000 chars
  - directory must be absolute, inside the approved worktree root, free of shell
    metacharacters, and exist
  - binary must be present and executable
  - 64 KB transcript cap; 600 s max timeout
- OpenCode is **not** required for the campaign to operate. The native Builder
  path (`EngineeringDirector` → runner filesystem/git/test executors) works
  independently. OpenCode can be disabled entirely; the campaign still runs.

## Dependency status

OpenCode is **not a hard dependency**. The canary proves the campaign continues
with the OpenCode-independent native path.
