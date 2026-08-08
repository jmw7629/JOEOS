# Agent Fabric Integration Audit (Phase P3G)

Date: 2026-08-07. Branch: `ai-rebuild`. HEAD: `df9898a1d45796034dd790d62fe9db19e8b5a439`.

This document is the honest evidence matrix for Phase P3G. Every row states whether the
reference capability is already delivered in the JoeOS codebase, partially delivered,
reference-only, or missing, with a local path/symbol citation. No capability is claimed
to exist unless it was verified in the working tree.

## Inputs audited

| Input | Status | Evidence |
| --- | --- | --- |
| `OpenHands` reference repo | Present, read-only | `/home/joewillisny/joeos-agent-architecture-references/OpenHands` @ `fe74c54` (MIT) |
| `open-multi-agent` reference repo | Present, read-only | `/home/joewillisny/joeos-agent-architecture-references/open-multi-agent` @ `04ade0f` (MIT) |
| `langgraph` reference repo | Present, read-only | `/home/joewillisny/joeos-agent-architecture-references/langgraph` @ `b2926a0` (MIT) |
| `autogen` reference repo | Present, read-only | `/home/joewillisny/joeos-agent-architecture-references/autogen` @ `027ecf0` (CC-BY-4.0) |
| `joeos-agent-fabric-0.1.0.zip` starter | **Missing** | `/home/joewillisny/Downloads/` does not exist; full-filesystem search found no agent-fabric archive. Contents/hash cannot be verified. Classified unverifiable. |
| Integration of reference code in JoeOS | None | grep for `OpenHands|open-multi-agent|LangGraph|AutoGen|agent-fabric` in `server/`, `runner/`, `apps/` (excluding `.venv`, `node_modules`, `.git`) returned zero matches. The four repos are cloned only; no code was copied. |
| Ollama runtime | Present | `/usr/local/bin/ollama` 0.31.2; `ollama list` shows qwen2.5-coder 1.5b/7b/14b + deepseek-r1 14b (agentic and safe variants); `ollama.service` active. JoeOS provider registry supports `provider_type="ollama"` (`server/actions/storage.py:21`) but no `OllamaProvider` adapter is registered (`server/ai/providers.py` ships only `LocalLemonadeProvider`). |
| OpenCode integration surface | Present | `opencode` 1.18.13 at `~/.opencode/bin/opencode`; documented noninteractive `opencode run --format json` and headless `opencode serve` interfaces exist. No JoeOS executor currently invokes it. |

## Evidence matrix

Legend: `delivered` = verified in working tree; `partial` = some subset exists; `missing` = verified absent; `rejected` = intentionally not implemented.

| Reference capability | Status | JoeOS evidence |
| --- | --- | --- |
| Provider registry (inference) | delivered | `server/ai/providers.py` `InferenceProvider` protocol, `LocalLemonadeProvider`; `server/ai/registry.py` `ProviderRegistry`, `ProviderRecord`; `AIService` (`server/ai/service.py`). |
| Provider capability dictionary / model routing | partial | `server/ai/router.py` (overview/providers/build_context/interpret); `server/actions/storage.py` `PROVIDER_TYPES`; conversation availability callback `_ai_availability` (`joeos_backend.py:480+`) reports provider_id "lemonade" and a chosen model; no runtime capability/health-check dict per provider beyond availability. |
| Agent registry + immutable versions | delivered | `server/actions/service.py` agent profiles (`control_agents`, `control_agent_versions`, `control_agent_runs`); `server/agents/models.py` `AgentProfile` with `config_version` and immutable versions at storage. |
| Durable agent runs + restart recovery | delivered | `server/actions/service.py` `start_agent_run`, runs/tasks; `server/actions/storage.py` run records with `interrupted` terminal handling; `recover_after_restart` in `server/runners/service.py`; conversation `recover_after_restart` in `server/conversations/service.py`. |
| Conversation authority + persistent messages | delivered | `server/conversations/` `ConversationService` create/list/get/rename/archive, `submit_message`, `retry_last_message`, `cancel_run`, `stream_message`, `_run_inference` (timeout 180s), `_execute`; requires `conversation.invoke_ai`. |
| Task graph / dependency enforcement | delivered | `server/agents/models.py` `TaskGraph`, `MissionTask`, `TaskDependency`; Swift `TaskGraph`/`TaskGraphRunner` (`apps/mobile/Sources/JoeOSIntelligence/`). |
| Multi-agent deliberation / council | delivered | `ExecutiveCouncil.run_council` (`server/actions/council.py`); `DisagreementRecord`, `ConsensusResult`, `DebateRecord`, `ConsultationRecord` (`server/agents/models.py`). |
| Delegation / parent run tracking | delivered | `control_agent_runs` parent/delegation fields and delegation depth in `server/actions/storage.py`; `AgentRun` delegation in `apps/mobile/Sources/JoeOSIntelligence/AgentFabric.swift`. |
| Authority inheritance | delivered | `server/identity/authority_repository.py` `AuthorityService.principal(session)` roles+capabilities; `require_capability` 403 capability_denied; `CAPABILITY_RISK_BY_NAME` classification. |
| Immutable action proposals | delivered | `server/actions/service.py` `ActionProposal` immutable, `payload_digest`; replay/digest-change/expiry/self-approval rejected (`docs/architecture/AGENTS_ACTIONS.md`). |
| Policy evaluation | delivered | `PolicyEngine` deny/allow_read_only/approval_required (`server/actions/policy.py`); `ApprovalRequest` step-up + separation of duties. |
| Human approvals | delivered | `server/actions/approvals.py`; one-time approval challenge signed with enrolled approval key; `server/actions/router.py` `/api/v1/control/approvals`. |
| Tool schemas / mediation | delivered | `ToolBroker` register/list/evaluate with `required_capabilities` and risk informational/low/medium/high/critical (`server/actions/tools.py`). |
| Runner execution plane (signed, typed executors) | delivered | `server/runners/` service + `runner/joeos_runner/` daemon: `DevCommandExecutor` (templates `joeos.dev.backend_tests`, `.runner_tests`, `.frontend_contract`, `.mobile_web_typecheck`, `.mobile_web_tests`, `.mobile_web_build`, `.python_compile`), `GitExecutor` (branch/commit validation, secret-scan hook, `_require_clean_args`), `UserServiceExecutor` (systemctl), `HealthChecker`. |
| Event streaming / realtime | delivered | `server/realtime/` `RealtimeService` SSE, `AuditEventRecord`, initial_snapshot/events_after/heartbeat, `origin_allowed`. |
| Observability / health / performance | delivered | `server/agents/` health + performance snapshots; `server/selfmaintenance/` health battery; `/_internal/diagnostics`; `PERFORMANCE_PLATFORM.md`. |
| Audit log | delivered | `server/security/audit.py` hash-chained `AuditService.record`; audit_checkpoint. |
| Engineering campaign state machine | **missing** | No `campaign`, `work_package`, `engineering_campaign`, `watchdog`, or `checkpoint` domain anywhere in `server/` or `runner/` Python (grep verified zero intentional matches). |
| Roadmap queue / ingestion | **missing** | No roadmap file in repo root; `IMPLEMENTATION_BACKLOG.md` is prose; no parseable roadmap/YAML or queue. |
| Versioned autonomy policy | **missing** | No `joeos.engineering.*` policy; only `joeos.ai.*`/control policies. |
| Worktree isolation | **missing** | `GitService` (`server/engineering/git.py`) and `GitExecutor` (`runner/joeos_runner/operations.py`) have no worktree create/integrate operations. |
| Coding-agent workflow (implement/validate/review) | **missing** | No engineering campaign orchestration; engineering workspace is bounded read/write only (`server/engineering/service.py`). |
| Verifier / quality gate | partial | Review records + `QualityGate` exist (`server/agents/models.py`) but no engineering campaign verification stage. |
| Watchdog | **missing** | Self-maintenance loop exists; no campaign watchdog/heartbeat contract. |
| Blocker records | **missing** | No `EngineeringBlocker` record; `intervention` records exist in org domain but not as campaign blockers. |
| Mac/Apple build-host integration | **missing** | P3F-A used manual VPS->Mac SSH (Tailscale `100.68.105.127`, key `~/.ssh/joeos_vps2mac`); no registered `joeos.apple.build` executor. |
| Automatic commit + ff-integrate + push | partial | `GitExecutor` supports branch create/commit/push for agent branches with prefix validation; no ff-integrate to `ai-rebuild`, no pre-push gate. |
| Restart recovery of campaigns | **missing** | Runs recover; campaigns do not exist yet. |
| Human interruption rules (pause/blocker/resume) | **missing** | Approvals exist; no campaign pause/blocker/resume contract. |
| Agent profile definitions for the 8 engineering roles | **missing** | No `Engineering Director / Architect / Builder / Verification / Apple Build / Security Reviewer / Release / Watchdog` profiles in the agent registry. |

## Gap list (result of this audit)

1. **EngineeringCampaign / WorkPackage / EngineeringAttempt / EngineeringCheckpoint** domain and state machine (persisted, authority-backed). This is the missing orchestration glue.
2. **Roadmap** parseable definition + ingestion queue (single source of truth for campaign backlog).
3. **Versioned autonomy policy** `joeos.engineering.ai-rebuild.v1` binding the campaign to provider/model/limits and gating capability grants.
4. **Worktree isolation** + typed repository tools (inspect/search/read/write/patch/diff/status/test/secret-scan/security-scan) as runner executors.
5. **Mac build-host executor** reusing the proven P3F-A rsync + Xcode setup, plus `AppleBuildAgent` profile.
6. **Watchdog + heartbeat + blocker** records and enforcement.
7. **Campaign status API + REST control** (start/pause/resume/cancel/checkpoint) in `server/engineering/` (extending the existing directory) and a frontend control panel.
8. **Multi-agent engineering graph** wiring eligibility -> plan -> worktree -> implement -> validate -> review -> commit -> integrate -> push -> checkpoint using the existing `TaskGraph` and `ActionService`.
9. **Integration gate** (uncommitted changes = 0, HEAD == ai-rebuild, tests green before push) and durable retry/backoff.
10. **Eight agent role profiles** created through the existing immutable agent registry.
11. **New capabilities** registered in `server/identity/authority_repository.py` `CAPABILITY_RISK_BY_NAME`: `engineering.campaign.read` (standard), `engineering.campaign.manage` (privileged), `engineering.campaign.start` (privileged), `engineering.campaign.pause` (privileged), `engineering.campaign.cancel` (privileged), `engineering.package.read` (standard), `engineering.package.manage` (privileged), `engineering.blocker.resolve` (critical). Owner role grants `engineering.campaign.manage` + `engineering.package.manage`; the campaign service never self-grants.

## Deliberate non-goals (rejected or out of scope)

- Second agent framework: none of the four reference repos is adopted wholesale; they are read-only inputs. AutoGen is CC-BY-4.0 and its code is not copied.
- Generic shell/SSH tools: no new unrestricted executor; the existing bound `GitExecutor`/`DevCommandExecutor`/`UserServiceExecutor` pattern is extended only.
- OpenCode TUI automation: no `tmux`/`expect` driving of the interactive TUI. If OpenCode is used it is invoked through the documented noninteractive `run --format json` interface inside a bounded worktree, with the runner as the only execution plane.
- Hermes agent: no intentional dependency (verified: only pip `AUTHORS.txt` and React Native package-lock "hermes" matches). Not integrated.
- Executing privileged operations through browser chat: unchanged.
