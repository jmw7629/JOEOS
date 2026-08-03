# JoeOS Communications, Inbox, and Notification Hub

Phase 12 delivers `server/communications/`: a local-first, provider-neutral
communications platform that unifies JoeOS system notifications, agent,
mission, task, and workflow messages, approvals, security alerts, drafts,
outbox, and external-provider messages — without fabricating accounts,
contacts, unread counts, or delivery receipts.

## Principles

- **User authority.** The user controls external sending, identities,
  accounts, recipients, and retention.
- **Clear origin.** Every message and notification carries a true
  `Origin` (user, JoeOS core, agent, workflow, plugin, project, external
  provider/person/organization).
- **Draft before send.** Agent- and AI-generated external communication
  defaults to draft/proposal state and requires approval.
- **No silent external send.** External delivery requires a granted approval
  bound to content hash, recipient hash, and attachment hashes; changing any
  material field invalidates the approval.
- **Content is untrusted.** Messages are sanitized (scripts, forms, dangerous
  protocols, and remote content removed), links are safety-checked, phishing
  signals are computed, and prompt-injection attempts are marked — content can
  never grant authority.
- **No impersonation.** Agents, workflows, and plugins can never send as the
  user (`can_send_as` enforces identity separation).
- **Bounded attention.** Quiet hours and DND suppress interruption, never
  delete items; security-critical alerts always remain visible.
- **Idempotent, recoverable delivery.** The outbox is authoritative; retries
  are bounded and never duplicate side effects.

## Architecture

```text
server/communications/
├── models.py          typed contracts (messages, notifications, identities, providers, accounts, contacts, drafts, outbox)
├── storage.py         versioned SQLite registry (communications.db)
├── registries.py      Provider/Account/Identity/Contact registries + RecipientResolver
├── messages.py        Message Store, Draft Store, Outbox Service
├── delivery.py        ExternalSendApprovalCoordinator + DeliveryService + AttachmentService
├── notifications.py   Notification Center, rules, quiet hours, DND, snooze, digests
├── safety.py          content sanitizer, link safety, phishing signals, prompt-injection detection
├── service.py         CommunicationsService facade
└── router.py          REST API under /api/v1/communications/*
```

## Security model

- **Provider credentials** are never stored in account records; they belong
  in the Secret Broker (integration point).
- **Recipient resolution** never invents addresses; ambiguity and
  unverified destinations block sending.
- **Approval binding** ties approval to the exact message content, recipient
  list, and attachment hashes. Changing content or recipients invalidates it.
- **Sanitization** strips scripts, forms, event handlers, and dangerous
  protocols. Remote images/fonts/tracking pixels are blocked by default.
- **Phishing signals** are explainable indicators, never authoritative proof.
- **Prompt injection** text is marked and never becomes instruction: it
  cannot approve, send, open links, or trigger tools.
- **Identity separation** blocks agent/workflow/plugin impersonation of the
  user.

## Attention management

Severity (informational → security_critical), priority (low → urgent), and
urgency (immediate → digest_only) are distinct concepts. Quiet hours and DND
suppress interruption channels (toast/banner) while preserving inbox
persistence; security-critical and approval categories are never silently
suppressed. Digests summarize the window while preserving failures and
approvals.

## Integrations

- **Automation**: workflows can create internal notifications, reminders,
  digests, drafts, and request send approval through the Communications
  platform — never by calling providers directly.
- **Multi-Agent**: agents produce communication proposals and internal
  messages with scoped origins; they cannot send externally as the user.
- **Plugin Platform**: providers and accounts are contributed through the
  Provider/Account registries; plugin providers are permission-controlled and
  never receive direct credential access.

## Known limitations

- No real external provider adapters ship yet; the only concrete providers
  are `joeos.internal` and an isolated test provider (`test.isolated`) that
  records delivery without network. Real email/chat adapters are future
  plugin work.
- Mobile push, smart-glasses delivery, read receipts, and webhooks are
  architecture only and not claimed as implemented.
- Semantic search integration points are defined but rely on the future Local
  AI Runtime; current search is exact/filtered.
