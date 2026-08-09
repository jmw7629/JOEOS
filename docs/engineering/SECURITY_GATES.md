# Security Gates

Security review is mandatory for changes that touch authentication,
authorization, browser pairing, device identity, approvals, provider secrets,
connectors, MCP, runner, ToolBroker, filesystem boundaries, network, CSP,
deployment, cryptography, native Keychain/Secure Enclave, memory privacy, or
cloud routing.

## Security agent verdicts

The `engineering.securityreviewer` returns a verdict beginning with exactly:

- `PASS` — no findings
- `PASS_WITH_FINDINGS` — non-blocking findings recorded
- `BLOCK` — prevents integration; raises a durable blocker (`security_block`)

`BLOCK` is never auto-retried by weakening policy. It requires a human.

## Built-in hardening in the Director

- **Path traversal**: Builder-supplied paths must be relative, contain no `..`,
  no absolute paths, and stay inside the worktree.
- **Change budget**: file count, per-file bytes, and total bytes are bounded.
- **Secret scan**: `api_key`/`secret`/`token`/`password`/`private_key`
  assignments and private-key blocks in Builder output fail closed.
- **No shell**: the Director applies files through the runner's bounded
  filesystem executor; it never runs raw shell from agent output.
- **No force-push / protected branches**: git operations run through the runner
  with protected-branch enforcement.
- **No capability self-grant**: the worker principal holds only
  `engineering.package.manage`.

## Non-negotiables

The campaign cannot:

- approve its own privileged ActionProposal
- raise its own autonomy level
- remove the Security agent requirement
- remove ToolBroker restrictions
- run arbitrary shell around the runner
- force-push
- overwrite a dirty human checkout
- expose credentials
- silently deploy high-risk work
- alter the safeguards governing its own authority without elevated approval
