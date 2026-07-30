import assert from "node:assert/strict";
import test from "node:test";

import {
  createJoeOSClient,
  JoeOSApiError,
  JoeOSClient,
  joeosSdkVersion,
} from "../src/index.js";

function jsonResponse(body, init = {}) {
  return new Response(JSON.stringify(body), {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
  });
}

class FakeClock {
  constructor() {
    this.nextId = 1;
    this.timers = [];
  }

  setTimeout(callback, delayMs) {
    const timer = { id: this.nextId++, callback, delayMs };
    this.timers.push(timer);
    return timer.id;
  }

  clearTimeout(timerId) {
    this.timers = this.timers.filter((timer) => timer.id !== timerId);
  }

  get delays() {
    return this.timers.map((timer) => timer.delayMs);
  }

  runNext() {
    const timer = this.timers.shift();
    assert.ok(timer, "expected a pending timer");
    timer.callback();
  }
}

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    this.closeCalls = [];
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? new Set();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.get(type)?.delete(listener);
  }

  emit(type, event = {}) {
    for (const listener of [...(this.listeners.get(type) ?? [])]) listener(event);
  }

  open() {
    this.emit("open");
  }

  message(value) {
    this.emit("message", { data: typeof value === "string" ? value : JSON.stringify(value) });
  }

  serverClose(code = 1006, reason = "network lost") {
    this.emit("close", { code, reason });
  }

  close(code, reason) {
    this.closeCalls.push({ code, reason });
    this.emit("close", { code, reason });
  }
}

function eventEnvelope({ cursor, eventType = "audit.event", payload = { id: cursor }, ...overrides }) {
  return {
    schema_version: 1,
    event_id: eventType === "audit.event" ? cursor : null,
    cursor,
    event_type: eventType,
    occurred_at: "2026-07-29T12:00:00Z",
    source: "joeos-test",
    severity: "info",
    payload,
    ...overrides,
  };
}

function bootstrapDocument() {
  const unavailable = (id) => ({
    id,
    status: "unavailable",
    access: "unavailable",
    route_ids: [],
    description: `${id} is not available in the local-first bootstrap contract.`,
  });
  return {
    schema_version: 2,
    generated_at: "2026-07-29T15:30:00Z",
    server: {
      server_id: "12345678-1234-4abc-8def-1234567890ab",
      product_id: "joeos",
      display_name: "JoeOS Local Command Center",
      server_version: "2.0.0",
      api_version: "v1",
      deployment_mode: "local_first",
    },
    security: {
      ownership_model: "single_owner",
      network_boundary: "operator_managed_private_tailnet",
      application_authentication: "unavailable",
      device_enrollment: "operator_pairing_v1",
      role_based_access: "unavailable",
      privileged_actions: "unavailable",
      public_internet_ready: false,
      secrets_returned: false,
      warning: "Use JoeOS only through an operator-managed private tailnet.",
    },
    device_enrollment: {
      protocol: "joeos-device-enrollment-v1",
      offer_authority: "local_console_only",
      pairing_secret_bytes: 32,
      offer_ttl_seconds: 300,
      challenge_ttl_seconds: 120,
      key_algorithm: "ES256",
      public_key_format: "spki_der_base64url",
      signature_format: "x962_der_base64url",
      proof_algorithm: "HKDF-SHA256+HMAC-SHA256+ECDSA-SHA256",
      required_key_purposes: ["device_authentication", "approval"],
      activation_state: "active_unassigned",
      grants_authority: false,
    },
    capabilities: [
      {
        id: "discovery.bootstrap",
        status: "available",
        access: "read_only",
        route_ids: ["bootstrap.discovery"],
        description: "Discover the connected JoeOS server.",
      },
      unavailable("identity.authentication"),
      {
        id: "identity.device_enrollment",
        status: "available",
        access: "enrollment",
        route_ids: ["device-enrollment.challenge", "device-enrollment.complete"],
        description: "Pair a native device through an operator-created local-console offer.",
      },
      unavailable("authorization.roles"),
      unavailable("approvals.privileged_actions"),
      unavailable("agents.execution"),
      unavailable("secrets.management"),
    ],
    routes: [
      {
        id: "bootstrap.discovery",
        path: "/api/v1/bootstrap",
        protocol: "http",
        methods: ["GET"],
        access: "read_only",
        stability: "stable",
        description: "Read versioned, non-secret JoeOS discovery metadata.",
      },
      {
        id: "device-enrollment.challenge",
        path: "/api/v1/device-enrollment/challenges",
        protocol: "http",
        methods: ["POST"],
        access: "enrollment",
        stability: "stable",
        description: "Claim an operator-created device-pairing offer and receive its challenge.",
      },
      {
        id: "device-enrollment.complete",
        path: "/api/v1/device-enrollment/challenges/{challenge_id}/complete",
        protocol: "http",
        methods: ["POST"],
        access: "enrollment",
        stability: "stable",
        description: "Complete a claimed device-pairing challenge with a signed proof.",
      },
    ],
  };
}

test("calls the supported same-origin read and planning APIs", async () => {
  const calls = [];
  const client = createJoeOSClient({
    origin: "https://joeos.test",
    fetch: async (url, init) => {
      calls.push({ url, init });
      return jsonResponse({ ok: true });
    },
  });

  await client.getMetrics();
  await client.getBots();
  await client.getEvents();
  await client.chat({ message: "  Summarize my morning.  ", context: { active_section: "dashboard" } });
  await client.getWorkspace();
  await client.guideConfiguration({ message: "  Make the status cards denser.  " });

  assert.equal(joeosSdkVersion, "0.1.0");
  assert.deepEqual(
    calls.map(({ url, init }) => [url, init.method]),
    [
      ["https://joeos.test/api/metrics", "GET"],
      ["https://joeos.test/api/bots", "GET"],
      ["https://joeos.test/api/events", "GET"],
      ["https://joeos.test/api/chat", "POST"],
      ["https://joeos.test/api/workspace", "GET"],
      ["https://joeos.test/api/configuration/guide", "POST"],
    ],
  );
  assert.deepEqual(JSON.parse(calls[3].init.body), {
    message: "Summarize my morning.",
    context: { active_section: "dashboard" },
  });
  assert.deepEqual(JSON.parse(calls[5].init.body), {
    message: "Make the status cards denser.",
  });
  assert.equal(calls[0].init.credentials, "same-origin");
  assert.equal(calls[0].init.redirect, "error");
  assert.equal(calls[0].init.headers.get("x-joeos-sdk-version"), "0.1.0");
  assert.equal(client.startBot, undefined);
  assert.equal(client.deployAgent, undefined);
  assert.equal(client.runCommand, undefined);
  assert.equal(client.enrollDevice, undefined);
  assert.equal(client.createEnrollmentChallenge, undefined);
  assert.equal(client.completeDeviceEnrollment, undefined);
});

test("gets and freezes bootstrap schema v2 from its fixed read-only route", async () => {
  let captured;
  const client = new JoeOSClient({
    origin: "https://joeos.test",
    baseUrl: "/tenant/custom-api",
    fetch: async (url, init) => {
      captured = { url, init };
      return jsonResponse(bootstrapDocument());
    },
  });

  const document = await client.getBootstrap();
  assert.equal(captured.url, "https://joeos.test/api/v1/bootstrap");
  assert.equal(captured.init.method, "GET");
  assert.equal(captured.init.body, undefined);
  assert.equal(captured.init.credentials, "same-origin");
  assert.equal(captured.init.redirect, "error");
  assert.equal(document.schema_version, 2);
  assert.equal(document.server.api_version, "v1");
  assert.equal(document.security.device_enrollment, "operator_pairing_v1");
  assert.equal(document.device_enrollment.protocol, "joeos-device-enrollment-v1");
  assert.equal(Object.isFrozen(document), true);
  assert.equal(Object.isFrozen(document.security), true);
  assert.equal(Object.isFrozen(document.device_enrollment), true);
  assert.equal(Object.isFrozen(document.device_enrollment.required_key_purposes), true);
  assert.equal(Object.isFrozen(document.capabilities), true);
  assert.equal(Object.isFrozen(document.routes), true);
});

test("rejects unsupported bootstrap version and security gates", async () => {
  const cases = [
    ["schema version", (document) => { document.schema_version = 1; }, "$.schema_version"],
    ["API version", (document) => { document.server.api_version = "v2"; }, "$.server.api_version"],
    ["product", (document) => { document.server.product_id = "other"; }, "$.server.product_id"],
    ["deployment mode", (document) => { document.server.deployment_mode = "cloud"; }, "$.server.deployment_mode"],
    ["public internet", (document) => { document.security.public_internet_ready = true; }, "$.security.public_internet_ready"],
    ["secrets", (document) => { document.security.secrets_returned = true; }, "$.security.secrets_returned"],
    ["authentication", (document) => { document.security.application_authentication = "available"; }, "$.security.application_authentication"],
    ["enrollment posture", (document) => { document.security.device_enrollment = "unavailable"; }, "$.security.device_enrollment"],
    ["authentication capability", (document) => {
      const capability = document.capabilities.find((item) => item.id === "identity.authentication");
      capability.status = "available";
      capability.access = "read_only";
      capability.route_ids = ["bootstrap.discovery"];
    }, "$.capabilities"],
    ["roles capability", (document) => {
      const capability = document.capabilities.find((item) => item.id === "authorization.roles");
      capability.status = "available";
      capability.access = "read_only";
      capability.route_ids = ["bootstrap.discovery"];
    }, "$.capabilities"],
    ["approvals capability", (document) => {
      const capability = document.capabilities.find((item) => item.id === "approvals.privileged_actions");
      capability.status = "available";
      capability.access = "read_only";
      capability.route_ids = ["bootstrap.discovery"];
    }, "$.capabilities"],
    ["privileged capability", (document) => {
      const capability = document.capabilities.find((item) => item.id === "agents.execution");
      capability.status = "available";
      capability.access = "read_only";
      capability.route_ids = ["bootstrap.discovery"];
    }, "$.capabilities"],
    ["secrets capability", (document) => {
      const capability = document.capabilities.find((item) => item.id === "secrets.management");
      capability.status = "available";
      capability.access = "read_only";
      capability.route_ids = ["bootstrap.discovery"];
    }, "$.capabilities"],
  ];

  for (const [name, mutate, expectedPath] of cases) {
    const payload = bootstrapDocument();
    mutate(payload);
    const client = new JoeOSClient({
      origin: "https://joeos.test",
      fetch: async () => jsonResponse(payload),
    });
    await assert.rejects(client.getBootstrap(), (error) => {
      assert.ok(error instanceof JoeOSApiError, name);
      assert.equal(error.code, "invalid_bootstrap", name);
      assert.equal(error.method, "GET", name);
      assert.equal(error.url, "https://joeos.test/api/v1/bootstrap", name);
      assert.equal(error.details.path, expectedPath, name);
      return true;
    });
  }
});

test("rejects malformed or weakened schema-v2 device-enrollment profiles", async () => {
  const cases = [
    ["profile is not an object", (document) => { document.device_enrollment = null; }, "$.device_enrollment"],
    ["unknown profile field", (document) => { document.device_enrollment.secret = "must-not-exist"; }, "$.device_enrollment"],
    ["missing profile field", (document) => { delete document.device_enrollment.protocol; }, "$.device_enrollment"],
    ["protocol", (document) => { document.device_enrollment.protocol = "joeos-device-enrollment-v2"; }, "$.device_enrollment.protocol"],
    ["offer authority", (document) => { document.device_enrollment.offer_authority = "browser"; }, "$.device_enrollment.offer_authority"],
    ["pairing secret size", (document) => { document.device_enrollment.pairing_secret_bytes = 16; }, "$.device_enrollment.pairing_secret_bytes"],
    ["offer lifetime", (document) => { document.device_enrollment.offer_ttl_seconds = 301; }, "$.device_enrollment.offer_ttl_seconds"],
    ["challenge lifetime", (document) => { document.device_enrollment.challenge_ttl_seconds = 121; }, "$.device_enrollment.challenge_ttl_seconds"],
    ["key algorithm", (document) => { document.device_enrollment.key_algorithm = "EdDSA"; }, "$.device_enrollment.key_algorithm"],
    ["public key format", (document) => { document.device_enrollment.public_key_format = "jwk"; }, "$.device_enrollment.public_key_format"],
    ["signature format", (document) => { document.device_enrollment.signature_format = "raw_base64url"; }, "$.device_enrollment.signature_format"],
    ["proof algorithm", (document) => { document.device_enrollment.proof_algorithm = "ECDSA-SHA256"; }, "$.device_enrollment.proof_algorithm"],
    ["key purposes missing", (document) => { document.device_enrollment.required_key_purposes = ["device_authentication"]; }, "$.device_enrollment.required_key_purposes"],
    ["key purposes reordered", (document) => { document.device_enrollment.required_key_purposes.reverse(); }, "$.device_enrollment.required_key_purposes"],
    ["activation state", (document) => { document.device_enrollment.activation_state = "owner"; }, "$.device_enrollment.activation_state"],
    ["authority grant", (document) => { document.device_enrollment.grants_authority = true; }, "$.device_enrollment.grants_authority"],
  ];

  for (const [name, mutate, expectedPath] of cases) {
    const payload = bootstrapDocument();
    mutate(payload);
    const client = new JoeOSClient({
      origin: "https://joeos.test",
      fetch: async () => jsonResponse(payload),
    });
    await assert.rejects(client.getBootstrap(), (error) => {
      assert.ok(error instanceof JoeOSApiError, name);
      assert.equal(error.code, "invalid_bootstrap", name);
      assert.equal(error.details.path, expectedPath, name);
      return true;
    });
  }
});

test("rejects missing, altered, or expanded device-enrollment route contracts", async () => {
  const cases = [
    ["missing challenge route", (document) => {
      document.routes = document.routes.filter((route) => route.id !== "device-enrollment.challenge");
    }],
    ["missing completion route", (document) => {
      document.routes = document.routes.filter((route) => route.id !== "device-enrollment.complete");
    }],
    ["challenge path", (document) => {
      document.routes.find((route) => route.id === "device-enrollment.challenge").path = "/api/v1/device-enrollment/offers";
    }],
    ["completion method", (document) => {
      document.routes.find((route) => route.id === "device-enrollment.complete").methods = ["GET"];
    }],
    ["completion access", (document) => {
      document.routes.find((route) => route.id === "device-enrollment.complete").access = "read_only";
    }],
    ["extra enrollment route", (document) => {
      document.routes.push({
        id: "device-enrollment.cancel",
        path: "/api/v1/device-enrollment/challenges/{challenge_id}/cancel",
        protocol: "http",
        methods: ["POST"],
        access: "enrollment",
        stability: "stable",
        description: "An unrecognized enrollment mutation.",
      });
    }],
    ["enrollment capability disabled", (document) => {
      const capability = document.capabilities.find((item) => item.id === "identity.device_enrollment");
      capability.status = "unavailable";
      capability.access = "unavailable";
      capability.route_ids = [];
    }],
    ["enrollment capability route missing", (document) => {
      const capability = document.capabilities.find((item) => item.id === "identity.device_enrollment");
      capability.route_ids = ["device-enrollment.challenge"];
    }],
    ["enrollment capability route order", (document) => {
      const capability = document.capabilities.find((item) => item.id === "identity.device_enrollment");
      capability.route_ids.reverse();
    }],
  ];

  for (const [name, mutate] of cases) {
    const payload = bootstrapDocument();
    mutate(payload);
    const client = new JoeOSClient({
      origin: "https://joeos.test",
      fetch: async () => jsonResponse(payload),
    });
    await assert.rejects(
      client.getBootstrap(),
      (error) => error instanceof JoeOSApiError && error.code === "invalid_bootstrap",
      name,
    );
  }
});

test("rejects malformed bootstrap structure and route references", async () => {
  const cases = [
    ["not an object", null],
    ["unknown field", { ...bootstrapDocument(), credential: "must-not-exist" }],
    ["non-UTC time", { ...bootstrapDocument(), generated_at: "2026-07-29T11:30:00-04:00" }],
    ["invalid server UUID", (() => {
      const document = bootstrapDocument();
      document.server.server_id = "not-a-uuid";
      return document;
    })()],
    ["absolute route", (() => {
      const document = bootstrapDocument();
      document.routes[0].path = "https://evil.test/bootstrap";
      return document;
    })()],
    ["network-path route", (() => {
      const document = bootstrapDocument();
      document.routes[0].path = "//evil.test/bootstrap";
      return document;
    })()],
    ["duplicate route", (() => {
      const document = bootstrapDocument();
      document.routes.push({ ...document.routes[0] });
      return document;
    })()],
    ["unknown route reference", (() => {
      const document = bootstrapDocument();
      document.capabilities[0].route_ids = ["unknown.route"];
      return document;
    })()],
    ["route access mismatch", (() => {
      const document = bootstrapDocument();
      document.capabilities[0].access = "configuration";
      return document;
    })()],
  ];

  for (const [name, payload] of cases) {
    const client = new JoeOSClient({
      origin: "https://joeos.test",
      fetch: async () => jsonResponse(payload),
    });
    await assert.rejects(
      client.getBootstrap(),
      (error) => error instanceof JoeOSApiError && error.code === "invalid_bootstrap",
      name,
    );
  }
});

test("bootstrap discovery does not create generic, privileged, or enrollment SDK access", async () => {
  const client = new JoeOSClient({
    origin: "https://joeos.test",
    fetch: async () => jsonResponse(bootstrapDocument()),
  });
  await client.getBootstrap();

  for (const method of [
    "request",
    "fetch",
    "get",
    "post",
    "put",
    "callRoute",
    "execute",
    "runCommand",
    "startBot",
    "createPairingOffer",
    "createEnrollmentChallenge",
    "completeDeviceEnrollment",
    "enrollDevice",
    "pairDevice",
  ]) {
    assert.equal(client[method], undefined, `${method} must not be exposed`);
  }
});

test("allows a configurable path but rejects unsafe or cross-origin base URLs", async () => {
  let requestedUrl = "";
  const client = new JoeOSClient({
    origin: "https://joeos.test",
    baseUrl: "/tenant/jmw/api/",
    fetch: async (url) => {
      requestedUrl = url;
      return jsonResponse({ metrics: [] });
    },
  });
  await client.getMetrics();
  assert.equal(client.baseUrl, "https://joeos.test/tenant/jmw/api/");
  assert.equal(requestedUrl, "https://joeos.test/tenant/jmw/api/metrics");

  const invalid = [
    "https://evil.test/api",
    "//evil.test/api",
    "javascript:alert(1)",
    "https://user:password@joeos.test/api",
    "/api?admin=true",
    "/api/../admin",
    "/api/%2e%2e/admin",
  ];
  for (const baseUrl of invalid) {
    assert.throws(
      () => new JoeOSClient({ origin: "https://joeos.test", baseUrl, fetch: async () => jsonResponse({}) }),
      TypeError,
      baseUrl,
    );
  }
});

test("normalizes structured HTTP errors and request metadata", async () => {
  const client = new JoeOSClient({
    origin: "https://joeos.test",
    fetch: async () => jsonResponse(
      {
        detail: {
          code: "workspace_stale",
          message: "Workspace revision is stale.",
          details: { current_revision: 8 },
        },
      },
      { status: 409, headers: { "x-request-id": "req-123", "retry-after": "1" } },
    ),
  });

  await assert.rejects(client.getWorkspace(), (error) => {
    assert.ok(error instanceof JoeOSApiError);
    assert.equal(error.status, 409);
    assert.equal(error.code, "workspace_stale");
    assert.equal(error.message, "Workspace revision is stale.");
    assert.deepEqual(error.details, { current_revision: 8 });
    assert.equal(error.requestId, "req-123");
    assert.equal(error.retryAfter, "1");
    assert.equal(error.method, "GET");
    assert.equal(error.url, "https://joeos.test/api/workspace");
    return true;
  });
});

test("normalizes timeouts, caller cancellation, and network failures", async () => {
  const abortAwareFetch = (_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener(
      "abort",
      () => reject(init.signal.reason ?? new DOMException("Aborted", "AbortError")),
      { once: true },
    );
  });
  const timeoutClient = new JoeOSClient({
    origin: "https://joeos.test",
    timeoutMs: 10,
    fetch: abortAwareFetch,
  });
  await assert.rejects(timeoutClient.getMetrics(), (error) => {
    assert.ok(error instanceof JoeOSApiError);
    assert.equal(error.code, "request_timeout");
    assert.equal(error.status, 0);
    return true;
  });

  const controller = new AbortController();
  const cancelled = timeoutClient.getEvents({ signal: controller.signal, timeoutMs: 1_000 });
  controller.abort("navigation");
  await assert.rejects(cancelled, (error) => error instanceof JoeOSApiError && error.code === "request_aborted");

  const networkClient = new JoeOSClient({
    origin: "https://joeos.test",
    fetch: async () => { throw new TypeError("socket closed"); },
  });
  await assert.rejects(networkClient.getBots(), (error) => {
    assert.ok(error instanceof JoeOSApiError);
    assert.equal(error.code, "network_error");
    assert.equal(error.message, "JoeOS is unreachable.");
    assert.equal(error.cause.message, "socket closed");
    return true;
  });
});

test("sends workspace revision in the PUT document and If-Match header", async () => {
  let captured;
  const client = new JoeOSClient({
    origin: "https://joeos.test",
    fetch: async (url, init) => {
      captured = { url, init };
      const workspace = { ...JSON.parse(init.body), revision: 13 };
      return jsonResponse({ workspace, catalog: [] });
    },
  });
  const workspace = {
    id: "executive",
    name: "Executive Mission Control",
    revision: 12,
    theme: { accent: "cyan", density: "compact" },
    widgets: [{ id: "attention", kind: "mission.attention" }],
  };

  const response = await client.updateWorkspace(workspace);
  assert.equal(captured.url, "https://joeos.test/api/workspace");
  assert.equal(captured.init.method, "PUT");
  assert.equal(captured.init.headers.get("if-match"), '"12"');
  assert.deepEqual(JSON.parse(captured.init.body), workspace);
  assert.equal(response.workspace.revision, 13);
  assert.equal(workspace.revision, 12, "the caller document is not mutated");

  assert.throws(
    () => client.updateWorkspace({ ...workspace, revision: 'bad"revision' }),
    /workspace revision/,
  );
});

test("subscribes to the canonical same-origin event stream and validates envelopes", () => {
  const clock = new FakeClock();
  const sockets = [];
  const events = [];
  const statuses = [];
  const client = new JoeOSClient({
    origin: "https://joeos.test",
    fetch: async () => jsonResponse({}),
    scheduler: clock,
    webSocketFactory: (url) => {
      const socket = new FakeWebSocket(url);
      sockets.push(socket);
      return socket;
    },
  });

  const handle = client.subscribeEvents({
    after: 5,
    onEvent: (event) => events.push(event),
    onStatus: (status) => statuses.push(status),
  });

  assert.equal(sockets.length, 1);
  assert.equal(sockets[0].url, "wss://joeos.test/ws/events?after=5");
  const socketUrl = new URL(sockets[0].url);
  assert.deepEqual([...socketUrl.searchParams.keys()], ["after"], "the URL never carries credentials or auth tokens");
  assert.equal(statuses[0].state, "connecting");
  sockets[0].open();
  assert.equal(statuses.at(-1).state, "open");

  sockets[0].message(eventEnvelope({ cursor: 5, eventType: "telemetry.snapshot" }));
  sockets[0].message(eventEnvelope({ cursor: 6 }));
  sockets[0].message(eventEnvelope({ cursor: 6, payload: { duplicate: true } }));
  sockets[0].message(eventEnvelope({ cursor: 6, eventType: "stream.heartbeat" }));
  assert.deepEqual(events.map((event) => event.event_type), [
    "telemetry.snapshot",
    "audit.event",
    "stream.heartbeat",
  ]);
  assert.equal(handle.cursor, 6);

  sockets[0].message("not-json");
  sockets[0].message({ ...eventEnvelope({ cursor: 7 }), schema_version: 2 });
  sockets[0].message({ ...eventEnvelope({ cursor: 7 }), event_type: "privileged.command" });
  sockets[0].message({ ...eventEnvelope({ cursor: 7 }), event_id: null });
  sockets[0].message(eventEnvelope({ cursor: 7, eventType: "stream.heartbeat" }));
  assert.equal(events.length, 3, "invalid or mismatched events are not delivered");
  assert.equal(statuses.filter((status) => status.state === "invalid_event").length, 5);

  handle.close();
  handle.close();
  assert.equal(handle.closed, true);
  assert.deepEqual(sockets[0].closeCalls, [{ code: 1000, reason: "client closed" }]);
  assert.equal(clock.timers.length, 0);
  assert.equal(statuses.at(-1).state, "closed");
});

test("reconnects with capped exponential backoff and resumes the last audit cursor", () => {
  const clock = new FakeClock();
  const sockets = [];
  const reconnectDelays = [];
  const client = new JoeOSClient({
    origin: "http://joeos.test",
    fetch: async () => jsonResponse({}),
    scheduler: clock,
    webSocketFactory: (url) => {
      const socket = new FakeWebSocket(url);
      sockets.push(socket);
      return socket;
    },
  });
  const handle = client.subscribeEvents({
    after: 2,
    onEvent() {},
    onStatus(status) {
      if (status.state === "reconnecting") reconnectDelays.push(status.delayMs);
    },
    reconnect: { initialDelayMs: 100, maxDelayMs: 250, maxAttempts: 5 },
  });

  assert.equal(sockets[0].url, "ws://joeos.test/ws/events?after=2");
  sockets[0].serverClose();
  assert.deepEqual(clock.delays, [100]);
  clock.runNext();
  assert.equal(sockets[1].url, "ws://joeos.test/ws/events?after=2");
  sockets[1].serverClose();
  assert.deepEqual(clock.delays, [200]);
  clock.runNext();
  sockets[2].serverClose();
  assert.deepEqual(clock.delays, [250]);
  clock.runNext();

  sockets[3].message(eventEnvelope({ cursor: 2, eventType: "telemetry.snapshot" }));
  sockets[3].message(eventEnvelope({ cursor: 3 }));
  assert.equal(handle.cursor, 3);
  sockets[3].serverClose();
  assert.deepEqual(clock.delays, [100], "a valid envelope resets consecutive-failure backoff");
  clock.runNext();
  assert.equal(sockets[4].url, "ws://joeos.test/ws/events?after=3");
  assert.deepEqual(reconnectDelays, [100, 200, 250, 100]);
  handle.close();
});

test("caller abort clears reconnect work and makes the subscription terminal", () => {
  const clock = new FakeClock();
  const sockets = [];
  const statuses = [];
  const controller = new AbortController();
  const client = new JoeOSClient({
    origin: "https://joeos.test",
    fetch: async () => jsonResponse({}),
    scheduler: clock,
    webSocketFactory: (url) => {
      const socket = new FakeWebSocket(url);
      sockets.push(socket);
      return socket;
    },
  });
  const handle = client.subscribeEvents({
    onEvent() {},
    onStatus: (status) => statuses.push(status),
    signal: controller.signal,
    reconnect: { initialDelayMs: 50, maxDelayMs: 100 },
  });

  sockets[0].serverClose();
  assert.deepEqual(clock.delays, [50]);
  controller.abort();
  assert.equal(handle.closed, true);
  assert.equal(clock.timers.length, 0);
  assert.equal(statuses.at(-1).state, "closed");
  assert.equal(statuses.at(-1).reason, "caller aborted");
  assert.equal(sockets.length, 1);

  const alreadyAborted = new AbortController();
  alreadyAborted.abort();
  const closedHandle = client.subscribeEvents({
    onEvent() {},
    signal: alreadyAborted.signal,
  });
  assert.equal(closedHandle.closed, true);
  assert.equal(sockets.length, 1, "an already-aborted subscription never constructs a socket");
});
