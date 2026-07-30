# JoeOS Browser SDK

Dependency-free ES module client for JoeOS browser applications. It uses same-origin cookies, fixed API routes, bounded timeouts, caller cancellation, normalized JSON errors, and optimistic workspace revisions. It contains no credentials and intentionally provides no shell, deployment, agent-control, device-enrollment, download, or other privileged command methods.

```js
import { createJoeOSClient, JoeOSApiError } from "@joeos/sdk";

const joeos = createJoeOSClient(); // current origin, /api
const { workspace, catalog } = await joeos.getWorkspace();

const updated = await joeos.updateWorkspace({
  ...workspace,
  theme: { ...workspace.theme, density: "compact" },
});

const proposal = await joeos.guideConfiguration({
  message: "Put attention and approvals first on my iPhone layout",
});
```

Available methods are `getBootstrap`, `getMetrics`, `getBots`, `getEvents`, `chat`, `getWorkspace`, `updateWorkspace`, and `guideConfiguration`. All HTTP methods accept an optional `{ signal, timeoutMs }` request-options object. `updateWorkspace` uses `PUT /api/workspace` and sends the current revision in both the JSON body and a quoted `If-Match` header.

`getBootstrap()` always reads the fixed same-origin route `GET /api/v1/bootstrap`, independent of a customized legacy API base path. It validates the complete schema-version-2 document before returning a deeply frozen value, including UUID/version fields, relative route metadata, capability references, and explicit local-first security limitations. Unsupported versions, public-internet readiness, secret-return claims, enabled authentication/authorization/privileged-action claims, malformed routes, and available execution capabilities are rejected with `JoeOSApiError` code `invalid_bootstrap`.

Schema version 2 advertises native device pairing as read-only discovery metadata. The SDK requires the exact `joeos-device-enrollment-v1` profile: offers originate only from the local console; pairing secrets are 32 bytes; offer and challenge lifetimes are 300 and 120 seconds; keys use `ES256`; public keys and signatures use `spki_der_base64url` and `x962_der_base64url`; proofs use `HKDF-SHA256+HMAC-SHA256+ECDSA-SHA256`; keys are required for both `device_authentication` and `approval`; activation is `active_unassigned`; and enrollment grants no authority. The only compatible enrollment routes are:

```text
POST /api/v1/device-enrollment/challenges
POST /api/v1/device-enrollment/challenges/{challenge_id}/complete
```

The browser client validates those routes and the `identity.device_enrollment` capability but cannot call them. Pairing is implemented by a trusted native client using an operator-created offer. Discovery metadata never creates a generic `request`, enrollment mutation, or privileged SDK method.

Subscribe to the read-only event stream with a resumable cursor:

```js
const subscription = joeos.subscribeEvents({
  after: 0,
  onEvent(event) {
    // Canonical envelope: schema_version, event_id, cursor, event_type,
    // occurred_at, source, severity, and payload.
    console.log(event.event_type, event.payload);
  },
  onStatus(status) {
    console.log(status.state, status.cursor);
  },
  signal: pageAbortController.signal,
  reconnect: { initialDelayMs: 500, maxDelayMs: 15_000 },
});

subscription.close();
```

The SDK derives `ws://` or `wss://` from the verified HTTP origin and connects only to `/ws/events?after=<cursor>`. No credential, bearer token, or arbitrary query value is accepted. Durable `audit.event` frames advance the numeric cursor; telemetry and heartbeat frames retain it. Duplicate audit cursors and malformed envelopes are not delivered. Reconnects use capped exponential backoff and resume from `subscription.cursor`; explicit close or abort is terminal.

HTTP, timeout, cancellation, malformed-response, and network failures reject with `JoeOSApiError`. Inspect `status`, `code`, `details`, `requestId`, and `retryAfter` rather than parsing message text.

The default base is `/api`. A different path may be supplied with `createJoeOSClient({ baseUrl: "/joeos/api" })`, but browser requests remain locked to `window.location.origin`. Tests and server-side runtimes can provide an explicit trusted `origin` and mocked `fetch`.

Run the package tests with:

```bash
npm test --prefix packages/sdk
```
