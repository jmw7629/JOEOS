# Resumable Local Realtime Stream

Status: implemented local-first foundation

Compatibility: existing `/api/metrics`, `/api/bots`, and `/api/events` polling remains supported

## Purpose

JoeOS exposes one typed WebSocket stream for a live Mission Control client:

```text
WS /ws/events?after=<cursor>
```

The stream combines a current telemetry snapshot with ordered audit events already persisted in Halo SQLite. It does not create an execution channel, accept commands, expose Lemonade directly, or replace the existing polling APIs.

## Mission Control integration

The command center loads the audited ES module at same-origin `/sdk/index.js` and subscribes after the newest cursor it has already seen. Valid audit envelopes are deduplicated into the visible security stream immediately. The transport indicator distinguishes a live socket, a reconnect in progress, and the five-second polling fallback.

The existing five-second HTTP refresh remains active for complete metric, fleet, and event-window reconciliation. If the SDK cannot load, WebSocket is unavailable, the phone sleeps, or a retained-history gap is reported, the dashboard continues through polling and catches up without turning a transport problem into a blank interface. The service worker caches the SDK as part of the private PWA shell.

## Envelope contract

Every server frame has the same versioned shape:

```json
{
  "schema_version": 1,
  "event_id": 42,
  "cursor": 42,
  "event_type": "audit.event",
  "occurred_at": "2026-07-29T12:00:00Z",
  "source": "workspace",
  "severity": "success",
  "payload": {
    "message": "Mission Control workspace revision 2 saved."
  }
}
```

Allowed event types:

- `telemetry.snapshot` — the first frame on every accepted connection;
- `audit.event` — a persisted SQLite event, with `event_id === cursor`;
- `stream.heartbeat` — an idle connection heartbeat retaining the last audit cursor.

Snapshots and heartbeats are synthetic, so their `event_id` is `null`. Audit IDs are positive SQLite integers. A client should advance its durable resume cursor only for a validated `audit.event`.

## Fresh and resumed connections

Without `after`, the initial snapshot uses the latest existing audit ID as its cursor. Only audit events created after the connection follow. This prevents a new dashboard from replaying the entire local log.

With `after=N`, the initial snapshot retains cursor `N`, then the server reads audit rows strictly satisfying `id > N`, ascending and in capped batches. Duplicate or non-increasing repository results are discarded defensively.

The snapshot includes:

```json
{
  "resume": {
    "requested_after": 12,
    "oldest_event_cursor": 20,
    "latest_event_cursor": 37,
    "history_gap": true,
    "cursor_ahead": false
  }
}
```

`history_gap` indicates that retention removed one or more events after the requested cursor. `cursor_ahead` indicates that a cursor is newer than this database, such as after restoring another device. The server never moves an explicit resume cursor backward, which avoids silently re-emitting IDs a client may already have handled.

## Snapshot contents

The current backend provider supplies bounded objects for:

- metrics and runtime state;
- Bot Fleet state;
- audit-event summary.

The service accepts this snapshot provider and the event repository through constructor injection. Tests can use in-memory repositories; production uses the existing SQLite `events` table.

## Heartbeats and lifecycle

Heartbeats bound idle silence and preserve the latest delivered audit cursor. Defaults are a 15-second heartbeat and a 500-millisecond event check. Configuration is clamped to safe minimum and maximum values.

Streaming work begins only after `/ws/events` is accepted. Unrelated HTTP routes do not create a realtime polling task. Client disconnects, server cancellation, send failures, and oversized inbound frames stop and clean up both sender and receiver coroutines.

## Security boundary

Browser connections with an `Origin` header must be same-host or match an explicit `JOEOS_ALLOWED_ORIGINS` entry. Missing origins remain accepted for native Swift and other non-browser clients. Wildcards and credential-bearing origins are not accepted.

Origin validation is a browser cross-site defense, not user authentication. Until identity and device enrollment are implemented, the stream inherits the existing private Tailscale access boundary.

The server does not process WebSocket commands. An inbound application frame closes with code `1003`; a frame over the configured limit closes with code `1009`. Outbound snapshot and event payloads are JSON checked and size-bounded before transmission.

## Configuration

Optional environment variables:

```text
JOEOS_ALLOWED_ORIGINS=https://command.example.com
JOEOS_WS_BATCH_SIZE=40
JOEOS_WS_POLL_SECONDS=0.5
JOEOS_WS_HEARTBEAT_SECONDS=15
JOEOS_WS_MAX_PAYLOAD_BYTES=262144
JOEOS_WS_MAX_INBOUND_BYTES=4096
```

Do not place credentials in any of these values.

## Polling compatibility

`GET /api/events` keeps the existing `id`, `time`, `level`, `source`, and `message` fields. Each log now also includes:

- `event_id` — numeric SQLite event ID;
- `cursor` — numeric resume cursor, currently equal to `event_id`;
- `recorded_at` — canonical stored timestamp.

The response also includes its latest top-level `cursor` and `recorded_at`. Existing polling clients can ignore these additive fields.

## Client resume algorithm

1. Load the last successfully handled audit cursor, if one exists.
2. Connect to `/ws/events?after=<cursor>`.
3. Validate `schema_version`, event type, timestamp, source, severity, and object payload.
4. Render snapshots and heartbeats without advancing durable cursor state.
5. For an audit event, require `event_id === cursor` and `cursor > lastCursor`.
6. Persist the audit cursor before invoking effects that must not run twice.
7. Reconnect with the last persisted cursor after an unexpected close.
