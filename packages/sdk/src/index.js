const DEFAULT_BASE_URL = "/api";
const DEFAULT_TIMEOUT_MS = 15_000;
const MAX_TIMEOUT_MS = 300_000;
const DEFAULT_RECONNECT_DELAY_MS = 500;
const DEFAULT_MAX_RECONNECT_DELAY_MS = 15_000;
const MAX_RECONNECT_DELAY_MS = 300_000;
const MAX_EVENT_MESSAGE_CHARACTERS = 1_048_576;
const SDK_VERSION = "0.1.0";

const ROUTES = Object.freeze({
  bootstrap: "/api/v1/bootstrap",
  metrics: "metrics",
  bots: "bots",
  events: "events",
  chat: "chat",
  workspace: "workspace",
  configurationGuide: "configuration/guide",
});

/**
 * @typedef {Object} JoeOSClientOptions
 * @property {string} [baseUrl="/api"] Same-origin API base path or URL.
 * @property {string} [origin] Trusted origin for non-browser runtimes. In a browser, location.origin is authoritative.
 * @property {number} [timeoutMs=15000] Default request timeout in milliseconds.
 * @property {typeof fetch} [fetch] Fetch implementation, primarily for tests and non-browser runtimes.
 * @property {(url: string) => WebSocketLike} [webSocketFactory] WebSocket constructor adapter for non-browser runtimes and tests.
 * @property {Scheduler} [scheduler] Timer adapter for deterministic reconnect tests.
 */

/**
 * @typedef {Object} WebSocketLike
 * @property {(type: string, listener: (event: any) => void) => void} addEventListener
 * @property {(type: string, listener: (event: any) => void) => void} removeEventListener
 * @property {(code?: number, reason?: string) => void} close
 */

/**
 * @typedef {Object} Scheduler
 * @property {(callback: () => void, delayMs: number) => unknown} setTimeout
 * @property {(timer: unknown) => void} clearTimeout
 */

/**
 * @typedef {Object} RequestOptions
 * @property {AbortSignal} [signal] Optional caller cancellation signal.
 * @property {number} [timeoutMs] Per-request timeout override in milliseconds.
 */

/**
 * @typedef {Object} Metric
 * @property {string} id
 * @property {string} label
 * @property {number} value
 * @property {number} [previous]
 * @property {string} [unit]
 * @property {string} [detail]
 * @property {number[]} [history]
 */

/** @typedef {{metrics: Metric[], uptime_seconds?: number, runtime?: Record<string, unknown>, nodes?: Array<Record<string, unknown>>}} MetricsResponse */
/** @typedef {{bots: Array<Record<string, unknown>>}} BotsResponse */
/** @typedef {{logs: Array<Record<string, unknown>>, summary?: Record<string, number>}} EventsResponse */

/**
 * @typedef {Object} BootstrapServerIdentity
 * @property {string} server_id Stable informational UUIDv4; never an authorization credential.
 * @property {"joeos"} product_id
 * @property {"JoeOS Local Command Center"} display_name
 * @property {string} server_version Semantic `major.minor.patch` server version.
 * @property {"v1"} api_version
 * @property {"local_first"} deployment_mode
 */

/**
 * @typedef {Object} BootstrapSecurityPosture
 * @property {"single_owner"} ownership_model
 * @property {"operator_managed_private_tailnet"} network_boundary
 * @property {"unavailable"} application_authentication
 * @property {"operator_pairing_v1"} device_enrollment
 * @property {"unavailable"} role_based_access
 * @property {"unavailable"} privileged_actions
 * @property {false} public_internet_ready
 * @property {false} secrets_returned
 * @property {string} warning
 */

/**
 * @typedef {Object} BootstrapRoute
 * @property {string} id
 * @property {string} path Relative same-origin path.
 * @property {"http"|"websocket"} protocol
 * @property {Array<"GET"|"PUT"|"POST"|"WEBSOCKET">} methods
 * @property {"read_only"|"configuration"|"stream"|"local_analysis"|"enrollment"} access
 * @property {"stable"} stability
 * @property {string} description
 */

/**
 * @typedef {Object} BootstrapCapability
 * @property {string} id
 * @property {"available"|"unavailable"} status
 * @property {"read_only"|"configuration"|"stream"|"local_analysis"|"enrollment"|"unavailable"} access
 * @property {string[]} route_ids
 * @property {string} description
 */

/**
 * Read-only metadata describing the native/local-console device-pairing protocol.
 * The browser SDK validates this profile but intentionally cannot invoke it.
 * @typedef {Object} BootstrapDeviceEnrollmentProfile
 * @property {"joeos-device-enrollment-v1"} protocol
 * @property {"local_console_only"} offer_authority
 * @property {32} pairing_secret_bytes
 * @property {300} offer_ttl_seconds
 * @property {120} challenge_ttl_seconds
 * @property {"ES256"} key_algorithm
 * @property {"spki_der_base64url"} public_key_format
 * @property {"x962_der_base64url"} signature_format
 * @property {"HKDF-SHA256+HMAC-SHA256+ECDSA-SHA256"} proof_algorithm
 * @property {["device_authentication", "approval"]} required_key_purposes
 * @property {"active_unassigned"} activation_state
 * @property {false} grants_authority
 */

/**
 * @typedef {Object} BootstrapDocument
 * @property {2} schema_version
 * @property {string} generated_at Timezone-aware UTC timestamp; not a signed time source.
 * @property {BootstrapServerIdentity} server
 * @property {BootstrapSecurityPosture} security
 * @property {BootstrapDeviceEnrollmentProfile} device_enrollment
 * @property {BootstrapCapability[]} capabilities
 * @property {BootstrapRoute[]} routes
 */

/**
 * @typedef {Object} JoeOSEventEnvelope
 * @property {1} schema_version
 * @property {number|null} event_id Durable audit-event identifier, otherwise null.
 * @property {number} cursor Monotonic, non-negative event cursor.
 * @property {"telemetry.snapshot"|"audit.event"|"stream.heartbeat"} event_type
 * @property {string} occurred_at ISO-8601 timestamp.
 * @property {string} source
 * @property {"info"|"success"|"warn"|"error"} severity
 * @property {Record<string, unknown>} payload Event data.
 */

/**
 * @typedef {Object} EventStreamStatus
 * @property {"connecting"|"open"|"invalid_event"|"event_handler_error"|"socket_error"|"reconnecting"|"closed"} state
 * @property {number} cursor Last accepted cursor.
 * @property {number} [attempt]
 * @property {number} [delayMs]
 * @property {number} [code]
 * @property {string} [reason]
 */

/**
 * @typedef {Object} EventReconnectOptions
 * @property {number} [initialDelayMs=500]
 * @property {number} [maxDelayMs=15000]
 * @property {number} [maxAttempts=Infinity] Maximum reconnect attempts without receiving a valid new event.
 */

/**
 * @typedef {Object} EventSubscriptionOptions
 * @property {number} [after=0]
 * @property {(event: JoeOSEventEnvelope) => void} onEvent
 * @property {(status: EventStreamStatus) => void} [onStatus]
 * @property {AbortSignal} [signal]
 * @property {boolean|EventReconnectOptions} [reconnect=true]
 */

/**
 * @typedef {Object} EventSubscriptionHandle
 * @property {() => void} close
 * @property {number} cursor
 * @property {boolean} closed
 */

/**
 * @typedef {Object} ChatRequest
 * @property {string} message
 * @property {Record<string, unknown>} [context]
 */

/** @typedef {{reply: string, status: string, model?: string}} ChatResponse */

/**
 * @typedef {Object} Workspace
 * @property {string} id
 * @property {string} name
 * @property {number|string} revision
 * @property {Record<string, unknown>} theme
 * @property {Array<Record<string, unknown>>} widgets
 */

/** @typedef {{workspace: Workspace, catalog: Array<Record<string, unknown>>}} WorkspaceResponse */

/**
 * @typedef {Object} ConfigurationGuideRequest
 * @property {string} message Natural-language customization request. Secrets must use a dedicated server-side flow.
 */

/**
 * @typedef {Object} ConfigurationGuideResponse
 * @property {Record<string, unknown>} proposal Typed proposal that is never applied automatically.
 */

/**
 * A normalized error returned for HTTP, network, timeout, cancellation, and response-shape failures.
 */
export class JoeOSApiError extends Error {
  /**
   * @param {string} message
   * @param {{status?: number, code?: string, details?: unknown, method?: string, url?: string, requestId?: string|null, retryAfter?: string|null, cause?: unknown}} [options]
   */
  constructor(message, options = {}) {
    super(message);
    this.name = "JoeOSApiError";
    this.status = options.status ?? 0;
    this.code = options.code ?? "joeos_error";
    this.details = options.details ?? null;
    this.method = options.method ?? null;
    this.url = options.url ?? null;
    this.requestId = options.requestId ?? null;
    this.retryAfter = options.retryAfter ?? null;
    if (options.cause !== undefined) this.cause = options.cause;
  }

  toJSON() {
    return {
      name: this.name,
      message: this.message,
      status: this.status,
      code: this.code,
      details: this.details,
      method: this.method,
      url: this.url,
      requestId: this.requestId,
      retryAfter: this.retryAfter,
    };
  }
}

function isPlainRecord(value) {
  return Object.prototype.toString.call(value) === "[object Object]";
}

function requireRecord(value, label) {
  if (!isPlainRecord(value)) throw new TypeError(`${label} must be a plain object.`);
  return value;
}

function normalizeTimeout(value, label = "timeoutMs") {
  if (!Number.isInteger(value) || value < 1 || value > MAX_TIMEOUT_MS) {
    throw new RangeError(`${label} must be an integer between 1 and ${MAX_TIMEOUT_MS}.`);
  }
  return value;
}

function normalizedOrigin(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new TypeError("origin must be an absolute HTTP(S) origin.");
  }
  if (!/^https?:$/.test(url.protocol) || url.username || url.password) {
    throw new TypeError("origin must be an HTTP(S) origin without credentials.");
  }
  if (url.pathname !== "/" || url.search || url.hash) {
    throw new TypeError("origin cannot contain a path, query, or fragment.");
  }
  return url.origin;
}

function trustedOrigin(providedOrigin) {
  const browserOrigin =
    typeof globalThis.location === "object" &&
    globalThis.location &&
    typeof globalThis.location.origin === "string" &&
    globalThis.location.origin !== "null"
      ? normalizedOrigin(globalThis.location.origin)
      : null;

  if (browserOrigin) {
    if (providedOrigin !== undefined && normalizedOrigin(providedOrigin) !== browserOrigin) {
      throw new TypeError("origin cannot override window.location.origin in a browser.");
    }
    return browserOrigin;
  }
  return normalizedOrigin(providedOrigin ?? "http://localhost");
}

function hasTraversal(value) {
  const pathOnly = value.split(/[?#]/, 1)[0];
  return /(^|[\\/])(?:\.{1,2}|%2e(?:%2e)?)(?=[\\/]|$)/i.test(pathOnly) || /%2f|%5c/i.test(pathOnly);
}

function normalizeBaseUrl(value, origin) {
  const input = value ?? DEFAULT_BASE_URL;
  if (typeof input !== "string" || !input.trim()) {
    throw new TypeError("baseUrl must be a non-empty string.");
  }
  if (hasTraversal(input)) throw new TypeError("baseUrl cannot contain traversal segments.");

  let url;
  try {
    url = new URL(input, `${origin}/`);
  } catch {
    throw new TypeError("baseUrl must be a valid same-origin HTTP(S) URL or path.");
  }
  if (!/^https?:$/.test(url.protocol) || url.username || url.password) {
    throw new TypeError("baseUrl must use HTTP(S) and cannot contain credentials.");
  }
  if (url.origin !== origin) throw new TypeError("baseUrl must be same-origin.");
  if (url.search || url.hash) throw new TypeError("baseUrl cannot contain a query or fragment.");

  const pathname = url.pathname.replace(/\/+$/, "");
  return `${url.origin}${pathname || ""}/`;
}

function normalizeCursor(value, label = "event cursor") {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new TypeError(`${label} must be a non-negative safe integer.`);
  }
  return value;
}

function normalizeScheduler(value) {
  const scheduler = value ?? {
    setTimeout: (callback, delayMs) => globalThis.setTimeout(callback, delayMs),
    clearTimeout: (timer) => globalThis.clearTimeout(timer),
  };
  if (
    !scheduler ||
    typeof scheduler.setTimeout !== "function" ||
    typeof scheduler.clearTimeout !== "function"
  ) {
    throw new TypeError("scheduler must provide setTimeout and clearTimeout functions.");
  }
  return scheduler;
}

function reconnectPolicy(value) {
  if (value === false) {
    return { enabled: false, initialDelayMs: 0, maxDelayMs: 0, maxAttempts: 0 };
  }
  if (value !== undefined && value !== true && !isPlainRecord(value)) {
    throw new TypeError("reconnect must be a boolean or a plain object.");
  }
  const options = isPlainRecord(value) ? value : {};
  const initialDelayMs = options.initialDelayMs ?? DEFAULT_RECONNECT_DELAY_MS;
  const maxDelayMs = options.maxDelayMs ?? DEFAULT_MAX_RECONNECT_DELAY_MS;
  const maxAttempts = options.maxAttempts ?? Number.POSITIVE_INFINITY;

  if (!Number.isInteger(initialDelayMs) || initialDelayMs < 1 || initialDelayMs > MAX_RECONNECT_DELAY_MS) {
    throw new RangeError(`reconnect initialDelayMs must be an integer between 1 and ${MAX_RECONNECT_DELAY_MS}.`);
  }
  if (!Number.isInteger(maxDelayMs) || maxDelayMs < initialDelayMs || maxDelayMs > MAX_RECONNECT_DELAY_MS) {
    throw new RangeError(`reconnect maxDelayMs must be an integer between initialDelayMs and ${MAX_RECONNECT_DELAY_MS}.`);
  }
  if (
    maxAttempts !== Number.POSITIVE_INFINITY &&
    (!Number.isInteger(maxAttempts) || maxAttempts < 0)
  ) {
    throw new RangeError("reconnect maxAttempts must be a non-negative integer or Infinity.");
  }
  return { enabled: true, initialDelayMs, maxDelayMs, maxAttempts };
}

function eventSocketUrl(baseUrl, after) {
  const base = new URL(baseUrl);
  const socketUrl = new URL("/ws/events", base.origin);
  socketUrl.protocol = base.protocol === "https:" ? "wss:" : "ws:";
  socketUrl.searchParams.set("after", String(after));
  if (socketUrl.host !== base.host || !/^wss?:$/.test(socketUrl.protocol)) {
    throw new TypeError("WebSocket URL must map to the JoeOS HTTP origin.");
  }
  return socketUrl.href;
}

function boundedString(value, maxLength) {
  return typeof value === "string" && value.length > 0 && value.length <= maxLength && !/[\r\n\0]/.test(value);
}

function isIsoTimestamp(value) {
  return boundedString(value, 64) &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    !Number.isNaN(Date.parse(value));
}

function parseEventEnvelope(data) {
  if (typeof data !== "string" || data.length > MAX_EVENT_MESSAGE_CHARACTERS) return null;
  let envelope;
  try {
    envelope = JSON.parse(data);
  } catch {
    return null;
  }
  const validEventType = ["telemetry.snapshot", "audit.event", "stream.heartbeat"].includes(envelope?.event_type);
  const validEventId = envelope?.event_id === null || (
    Number.isSafeInteger(envelope?.event_id) && envelope.event_id >= 0
  );
  const validEventIdentity = envelope?.event_type === "audit.event"
    ? Number.isSafeInteger(envelope?.event_id) && envelope.event_id === envelope.cursor
    : envelope?.event_id === null;
  if (
    !isPlainRecord(envelope) ||
    envelope.schema_version !== 1 ||
    !validEventId ||
    !Number.isSafeInteger(envelope.cursor) ||
    envelope.cursor < 0 ||
    !validEventType ||
    !validEventIdentity ||
    !isIsoTimestamp(envelope.occurred_at) ||
    !boundedString(envelope.source, 128) ||
    !["info", "success", "warn", "error"].includes(envelope.severity) ||
    !isPlainRecord(envelope.payload)
  ) {
    return null;
  }
  return envelope;
}

function exactKeys(value, keys) {
  if (!isPlainRecord(value)) return false;
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function uniqueStrings(values) {
  return values.length === new Set(values).size;
}

function freezeJson(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) freezeJson(child);
  return Object.freeze(value);
}

function validateBootstrapDocument(document, url) {
  const fail = (path, reason) => {
    throw new JoeOSApiError("JoeOS returned an incompatible bootstrap document.", {
      code: "invalid_bootstrap",
      details: { path, reason },
      method: "GET",
      url,
    });
  };
  const check = (condition, path, reason) => {
    if (!condition) fail(path, reason);
  };
  const identifierPattern = /^[a-z][a-z0-9_.-]{2,80}$/;
  const routePathPattern = /^\/[A-Za-z0-9_./{}-]+$/;
  const utcTimestampPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|\+00:00)$/;
  const uuidV4Pattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const routeMethods = new Set(["GET", "PUT", "POST", "WEBSOCKET"]);
  const routeAccess = new Set(["read_only", "configuration", "stream", "local_analysis", "enrollment"]);
  const capabilityAccess = new Set([...routeAccess, "unavailable"]);

  check(
    exactKeys(document, ["schema_version", "generated_at", "server", "security", "device_enrollment", "capabilities", "routes"]),
    "$",
    "expected the exact version-2 bootstrap fields",
  );
  check(document.schema_version === 2, "$.schema_version", "unsupported bootstrap schema version");
  check(
    typeof document.generated_at === "string" &&
      utcTimestampPattern.test(document.generated_at) &&
      !Number.isNaN(Date.parse(document.generated_at)),
    "$.generated_at",
    "expected a timezone-aware UTC timestamp",
  );

  check(
    exactKeys(document.server, ["server_id", "product_id", "display_name", "server_version", "api_version", "deployment_mode"]),
    "$.server",
    "invalid server identity fields",
  );
  check(uuidV4Pattern.test(document.server.server_id), "$.server.server_id", "expected a UUIDv4 server identifier");
  check(document.server.product_id === "joeos", "$.server.product_id", "unexpected product identifier");
  check(document.server.display_name === "JoeOS Local Command Center", "$.server.display_name", "unexpected product display name");
  check(/^\d+\.\d+\.\d+$/.test(document.server.server_version), "$.server.server_version", "invalid server version");
  check(document.server.api_version === "v1", "$.server.api_version", "unsupported API version");
  check(document.server.deployment_mode === "local_first", "$.server.deployment_mode", "unsupported deployment mode");

  check(
    exactKeys(document.security, [
      "ownership_model",
      "network_boundary",
      "application_authentication",
      "device_enrollment",
      "role_based_access",
      "privileged_actions",
      "public_internet_ready",
      "secrets_returned",
      "warning",
    ]),
    "$.security",
    "invalid security-posture fields",
  );
  check(document.security.ownership_model === "single_owner", "$.security.ownership_model", "unsupported ownership model");
  check(
    document.security.network_boundary === "operator_managed_private_tailnet",
    "$.security.network_boundary",
    "server is outside the supported private-tailnet boundary",
  );
  for (const field of ["application_authentication", "role_based_access", "privileged_actions"]) {
    check(document.security[field] === "unavailable", `$.security.${field}`, `${field} must remain unavailable in this contract`);
  }
  check(
    document.security.device_enrollment === "operator_pairing_v1",
    "$.security.device_enrollment",
    "device enrollment must use the operator-pairing-v1 posture",
  );
  check(document.security.public_internet_ready === false, "$.security.public_internet_ready", "public-internet operation is not supported");
  check(document.security.secrets_returned === false, "$.security.secrets_returned", "bootstrap must never return secrets");
  check(boundedString(document.security.warning, 240), "$.security.warning", "security warning must be present and bounded");

  const enrollmentProfileKeys = [
    "protocol",
    "offer_authority",
    "pairing_secret_bytes",
    "offer_ttl_seconds",
    "challenge_ttl_seconds",
    "key_algorithm",
    "public_key_format",
    "signature_format",
    "proof_algorithm",
    "required_key_purposes",
    "activation_state",
    "grants_authority",
  ];
  check(
    exactKeys(document.device_enrollment, enrollmentProfileKeys),
    "$.device_enrollment",
    "invalid device-enrollment profile fields",
  );
  const enrollment = document.device_enrollment;
  check(enrollment.protocol === "joeos-device-enrollment-v1", "$.device_enrollment.protocol", "unsupported enrollment protocol");
  check(enrollment.offer_authority === "local_console_only", "$.device_enrollment.offer_authority", "enrollment offers must remain local-console-only");
  check(enrollment.pairing_secret_bytes === 32, "$.device_enrollment.pairing_secret_bytes", "unexpected pairing-secret size");
  check(enrollment.offer_ttl_seconds === 300, "$.device_enrollment.offer_ttl_seconds", "unexpected enrollment-offer lifetime");
  check(enrollment.challenge_ttl_seconds === 120, "$.device_enrollment.challenge_ttl_seconds", "unexpected enrollment-challenge lifetime");
  check(enrollment.key_algorithm === "ES256", "$.device_enrollment.key_algorithm", "unsupported enrollment key algorithm");
  check(enrollment.public_key_format === "spki_der_base64url", "$.device_enrollment.public_key_format", "unsupported enrollment public-key format");
  check(enrollment.signature_format === "x962_der_base64url", "$.device_enrollment.signature_format", "unsupported enrollment signature format");
  check(
    enrollment.proof_algorithm === "HKDF-SHA256+HMAC-SHA256+ECDSA-SHA256",
    "$.device_enrollment.proof_algorithm",
    "unsupported enrollment proof algorithm",
  );
  check(
    Array.isArray(enrollment.required_key_purposes) &&
      enrollment.required_key_purposes.length === 2 &&
      enrollment.required_key_purposes[0] === "device_authentication" &&
      enrollment.required_key_purposes[1] === "approval",
    "$.device_enrollment.required_key_purposes",
    "unexpected enrollment key purposes",
  );
  check(enrollment.activation_state === "active_unassigned", "$.device_enrollment.activation_state", "unsupported enrollment activation state");
  check(enrollment.grants_authority === false, "$.device_enrollment.grants_authority", "device enrollment cannot grant authority");

  check(Array.isArray(document.routes) && document.routes.length <= 128, "$.routes", "routes must be a bounded array");
  const routeIds = [];
  for (let index = 0; index < document.routes.length; index += 1) {
    const route = document.routes[index];
    const path = `$.routes[${index}]`;
    check(
      exactKeys(route, ["id", "path", "protocol", "methods", "access", "stability", "description"]),
      path,
      "invalid route fields",
    );
    check(identifierPattern.test(route.id), `${path}.id`, "invalid route identifier");
    check(
      routePathPattern.test(route.path) &&
        !route.path.startsWith("//") &&
        !route.path.includes("://") &&
        !/(^|\/)\.\.?($|\/)/.test(route.path),
      `${path}.path`,
      "route must be a safe relative path",
    );
    check(route.protocol === "http" || route.protocol === "websocket", `${path}.protocol`, "invalid route protocol");
    check(
      Array.isArray(route.methods) &&
        route.methods.length >= 1 &&
        route.methods.length <= 4 &&
        route.methods.every((method) => routeMethods.has(method)) &&
        uniqueStrings(route.methods),
      `${path}.methods`,
      "invalid route methods",
    );
    check(
      route.protocol === "websocket"
        ? route.methods.length === 1 && route.methods[0] === "WEBSOCKET"
        : !route.methods.includes("WEBSOCKET"),
      `${path}.methods`,
      "route protocol and methods disagree",
    );
    check(routeAccess.has(route.access), `${path}.access`, "invalid route access classification");
    check(route.stability === "stable", `${path}.stability`, "unsupported route stability");
    check(boundedString(route.description, 240), `${path}.description`, "route description must be present and bounded");
    routeIds.push(route.id);
  }
  check(uniqueStrings(routeIds), "$.routes", "route identifiers must be unique");
  const knownRoutes = new Set(routeIds);
  const routeById = new Map(document.routes.map((route) => [route.id, route]));

  check(
    Array.isArray(document.capabilities) && document.capabilities.length <= 128,
    "$.capabilities",
    "capabilities must be a bounded array",
  );
  const capabilityIds = [];
  const capabilityById = new Map();
  for (let index = 0; index < document.capabilities.length; index += 1) {
    const capability = document.capabilities[index];
    const path = `$.capabilities[${index}]`;
    check(
      exactKeys(capability, ["id", "status", "access", "route_ids", "description"]),
      path,
      "invalid capability fields",
    );
    check(identifierPattern.test(capability.id), `${path}.id`, "invalid capability identifier");
    check(capability.status === "available" || capability.status === "unavailable", `${path}.status`, "invalid capability status");
    check(capabilityAccess.has(capability.access), `${path}.access`, "invalid capability access classification");
    check(
      Array.isArray(capability.route_ids) &&
        capability.route_ids.length <= 12 &&
        capability.route_ids.every((routeId) => typeof routeId === "string" && identifierPattern.test(routeId)) &&
        uniqueStrings(capability.route_ids),
      `${path}.route_ids`,
      "invalid capability route references",
    );
    check(
      capability.route_ids.every((routeId) => knownRoutes.has(routeId)),
      `${path}.route_ids`,
      "capability references an unknown route",
    );
    check(
      capability.status === "unavailable"
        ? capability.access === "unavailable" && capability.route_ids.length === 0
        : capability.access !== "unavailable" && capability.route_ids.length > 0,
      path,
      "capability status, access, and routes disagree",
    );
    check(
      capability.status === "unavailable" || capability.route_ids.every((routeId) => routeById.get(routeId)?.access === capability.access),
      `${path}.route_ids`,
      "capability access does not match a referenced route",
    );
    check(boundedString(capability.description, 240), `${path}.description`, "capability description must be present and bounded");
    capabilityIds.push(capability.id);
    capabilityById.set(capability.id, capability);
  }
  check(uniqueStrings(capabilityIds), "$.capabilities", "capability identifiers must be unique");

  const discoveryRoute = document.routes.find((route) => route.id === "bootstrap.discovery");
  check(
    discoveryRoute &&
      discoveryRoute.path === "/api/v1/bootstrap" &&
      discoveryRoute.protocol === "http" &&
      discoveryRoute.access === "read_only" &&
      discoveryRoute.methods.length === 1 &&
      discoveryRoute.methods[0] === "GET",
    "$.routes",
    "bootstrap discovery route is missing or incompatible",
  );
  const discoveryCapability = capabilityById.get("discovery.bootstrap");
  check(
    discoveryCapability &&
      discoveryCapability.status === "available" &&
      discoveryCapability.access === "read_only" &&
      discoveryCapability.route_ids.length === 1 &&
      discoveryCapability.route_ids[0] === "bootstrap.discovery",
    "$.capabilities",
    "bootstrap discovery capability is missing or incompatible",
  );

  const expectedEnrollmentRoutes = [
    {
      id: "device-enrollment.challenge",
      path: "/api/v1/device-enrollment/challenges",
    },
    {
      id: "device-enrollment.complete",
      path: "/api/v1/device-enrollment/challenges/{challenge_id}/complete",
    },
  ];
  for (const expected of expectedEnrollmentRoutes) {
    const route = routeById.get(expected.id);
    check(
      route &&
        route.path === expected.path &&
        route.protocol === "http" &&
        route.access === "enrollment" &&
        route.methods.length === 1 &&
        route.methods[0] === "POST",
      "$.routes",
      `${expected.id} route is missing or incompatible`,
    );
  }
  check(
    document.routes.filter((route) => route.access === "enrollment").length === expectedEnrollmentRoutes.length,
    "$.routes",
    "version-2 bootstrap cannot advertise additional enrollment routes",
  );
  const enrollmentCapability = capabilityById.get("identity.device_enrollment");
  check(
    enrollmentCapability &&
      enrollmentCapability.status === "available" &&
      enrollmentCapability.access === "enrollment" &&
      enrollmentCapability.route_ids.length === expectedEnrollmentRoutes.length &&
      enrollmentCapability.route_ids.every((routeId, index) => routeId === expectedEnrollmentRoutes[index].id),
    "$.capabilities",
    "identity.device_enrollment capability is missing or incompatible",
  );
  check(
    document.capabilities.filter((capability) => capability.access === "enrollment").length === 1,
    "$.capabilities",
    "version-2 bootstrap cannot advertise additional enrollment capabilities",
  );
  for (const capabilityId of [
    "identity.authentication",
    "authorization.roles",
    "approvals.privileged_actions",
    "agents.execution",
    "secrets.management",
  ]) {
    const capability = capabilityById.get(capabilityId);
    check(
      capability && capability.status === "unavailable" && capability.access === "unavailable" && capability.route_ids.length === 0,
      "$.capabilities",
      `${capabilityId} must be explicitly unavailable`,
    );
  }

  return freezeJson(document);
}

function normalizeRevision(value) {
  if (Number.isSafeInteger(value) && value >= 1) return String(value);
  if (typeof value === "string" && value.length > 0 && value.length <= 128 && !/["\r\n\0]/.test(value)) {
    return value;
  }
  throw new TypeError("workspace revision must be a positive safe integer or a non-empty opaque string.");
}

function isAbortSignal(value) {
  return Boolean(
    value &&
    typeof value.aborted === "boolean" &&
    typeof value.addEventListener === "function" &&
    typeof value.removeEventListener === "function",
  );
}

function composedSignal(externalSignal, timeoutMs) {
  if (externalSignal !== undefined && !isAbortSignal(externalSignal)) {
    throw new TypeError("signal must be an AbortSignal.");
  }

  const controller = new AbortController();
  let timedOut = false;
  let timeoutId = null;

  const abortFromCaller = () => {
    if (!controller.signal.aborted) controller.abort(externalSignal.reason);
  };

  if (externalSignal) {
    if (externalSignal.aborted) abortFromCaller();
    else externalSignal.addEventListener("abort", abortFromCaller, { once: true });
  }

  if (!controller.signal.aborted) {
    timeoutId = setTimeout(() => {
      timedOut = true;
      controller.abort(new DOMException(`Request timed out after ${timeoutMs}ms.`, "TimeoutError"));
    }, timeoutMs);
  }

  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    cleanup() {
      if (timeoutId !== null) clearTimeout(timeoutId);
      if (externalSignal) externalSignal.removeEventListener("abort", abortFromCaller);
    },
  };
}

function messageFromErrorBody(body, statusText) {
  const candidates = [body?.error?.message, body?.detail?.message, body?.detail, body?.message];
  const message = candidates.find((candidate) => typeof candidate === "string" && candidate.trim());
  return message?.trim() || statusText || "JoeOS request failed.";
}

function codeFromErrorBody(body, status) {
  const candidates = [body?.error?.code, body?.detail?.code, body?.code];
  const code = candidates.find((candidate) => typeof candidate === "string" && candidate.trim());
  if (code) return code.trim();
  if (status === 409 || status === 412) return "revision_conflict";
  return `http_${status}`;
}

async function normalizeResponse(response, method, url) {
  if (!response || typeof response.text !== "function" || typeof response.ok !== "boolean") {
    throw new JoeOSApiError("JoeOS returned an invalid fetch response.", {
      code: "invalid_response",
      method,
      url,
    });
  }

  const raw = response.status === 204 ? "" : await response.text();
  let body = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      if (response.ok) {
        throw new JoeOSApiError("JoeOS returned malformed JSON.", {
          status: response.status,
          code: "invalid_json",
          details: { preview: raw.slice(0, 256) },
          method,
          url,
          requestId: response.headers?.get?.("x-request-id") ?? null,
        });
      }
      body = { detail: raw.slice(0, 2_048) };
    }
  }

  if (!response.ok) {
    throw new JoeOSApiError(messageFromErrorBody(body, response.statusText), {
      status: response.status,
      code: codeFromErrorBody(body, response.status),
      details: body?.error?.details ?? body?.detail?.details ?? body?.detail ?? body,
      method,
      url,
      requestId: response.headers?.get?.("x-request-id") ?? response.headers?.get?.("request-id") ?? null,
      retryAfter: response.headers?.get?.("retry-after") ?? null,
    });
  }

  return body;
}

export class JoeOSClient {
  #baseUrl;
  #fetch;
  #scheduler;
  #timeoutMs;
  #webSocketFactory;

  /** @param {JoeOSClientOptions} [options] */
  constructor(options = {}) {
    requireRecord(options, "options");
    const origin = trustedOrigin(options.origin);
    this.#baseUrl = normalizeBaseUrl(options.baseUrl, origin);
    this.#timeoutMs = normalizeTimeout(options.timeoutMs ?? DEFAULT_TIMEOUT_MS);
    this.#fetch = options.fetch ?? globalThis.fetch;
    if (typeof this.#fetch !== "function") {
      throw new TypeError("A fetch implementation is required in this runtime.");
    }
    this.#scheduler = normalizeScheduler(options.scheduler);
    if (options.webSocketFactory !== undefined && typeof options.webSocketFactory !== "function") {
      throw new TypeError("webSocketFactory must be a function.");
    }
    this.#webSocketFactory = options.webSocketFactory ?? (
      typeof globalThis.WebSocket === "function"
        ? (url) => new globalThis.WebSocket(url)
        : null
    );
  }

  get baseUrl() {
    return this.#baseUrl;
  }

  async #request(route, method, body, options = {}, requestHeaders = {}) {
    requireRecord(options, "request options");
    const timeoutMs = normalizeTimeout(options.timeoutMs ?? this.#timeoutMs, "request timeoutMs");
    const composed = composedSignal(options.signal, timeoutMs);
    const url = new URL(route, this.#baseUrl).href;
    const headers = new Headers({
      Accept: "application/json",
      "X-JoeOS-SDK-Version": SDK_VERSION,
      ...requestHeaders,
    });
    const init = {
      method,
      headers,
      signal: composed.signal,
      credentials: "same-origin",
      mode: "same-origin",
      redirect: "error",
      cache: "no-store",
    };
    if (body !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(body);
    }

    try {
      if (composed.signal.aborted) throw composed.signal.reason ?? new DOMException("Request aborted.", "AbortError");
      const response = await this.#fetch(url, init);
      return await normalizeResponse(response, method, url);
    } catch (error) {
      if (error instanceof JoeOSApiError) throw error;
      if (composed.didTimeout()) {
        throw new JoeOSApiError(`JoeOS request timed out after ${timeoutMs}ms.`, {
          code: "request_timeout",
          method,
          url,
          cause: error,
        });
      }
      if (options.signal?.aborted || composed.signal.aborted) {
        throw new JoeOSApiError("JoeOS request was cancelled.", {
          code: "request_aborted",
          method,
          url,
          cause: error,
        });
      }
      throw new JoeOSApiError("JoeOS is unreachable.", {
        code: "network_error",
        method,
        url,
        cause: error,
      });
    } finally {
      composed.cleanup();
    }
  }

  /**
   * Discovers the connected local JoeOS server through the fixed read-only bootstrap route.
   * The response is rejected unless its version-2 profile, references, and explicit security gates are compatible.
   * @param {RequestOptions} [options]
   * @returns {Promise<BootstrapDocument>}
   */
  async getBootstrap(options) {
    const document = await this.#request(ROUTES.bootstrap, "GET", undefined, options);
    const url = new URL(ROUTES.bootstrap, this.#baseUrl).href;
    return validateBootstrapDocument(document, url);
  }

  /** @param {RequestOptions} [options] @returns {Promise<MetricsResponse>} */
  getMetrics(options) {
    return this.#request(ROUTES.metrics, "GET", undefined, options);
  }

  /** Read-only fleet snapshot. This SDK intentionally exposes no agent execution controls. @param {RequestOptions} [options] @returns {Promise<BotsResponse>} */
  getBots(options) {
    return this.#request(ROUTES.bots, "GET", undefined, options);
  }

  /** @param {RequestOptions} [options] @returns {Promise<EventsResponse>} */
  getEvents(options) {
    return this.#request(ROUTES.events, "GET", undefined, options);
  }

  /** @param {ChatRequest} request @param {RequestOptions} [options] @returns {Promise<ChatResponse>} */
  chat(request, options) {
    requireRecord(request, "chat request");
    const message = typeof request.message === "string" ? request.message.trim() : "";
    if (!message) throw new TypeError("chat message must be a non-empty string.");
    if (message.length > 16_000) throw new RangeError("chat message cannot exceed 16000 characters.");
    if (request.context !== undefined) requireRecord(request.context, "chat context");
    return this.#request(
      ROUTES.chat,
      "POST",
      { message, context: request.context ?? {} },
      options,
    );
  }

  /** @param {RequestOptions} [options] @returns {Promise<WorkspaceResponse>} */
  getWorkspace(options) {
    return this.#request(ROUTES.workspace, "GET", undefined, options);
  }

  /**
   * Replaces the editable workspace document using optimistic concurrency.
   * The revision is sent in both the JSON document and a quoted If-Match header.
   * @param {Workspace} workspace
   * @param {RequestOptions} [options]
   * @returns {Promise<WorkspaceResponse>}
   */
  updateWorkspace(workspace, options) {
    requireRecord(workspace, "workspace");
    const revision = normalizeRevision(workspace.revision);
    const body = { ...workspace, revision: workspace.revision };
    return this.#request(
      ROUTES.workspace,
      "PUT",
      body,
      options,
      { "If-Match": `"${revision}"` },
    );
  }

  /**
   * Requests a non-executing, typed configuration proposal and visual preview.
   * @param {ConfigurationGuideRequest} request
   * @param {RequestOptions} [options]
   * @returns {Promise<ConfigurationGuideResponse>}
   */
  guideConfiguration(request, options) {
    requireRecord(request, "configuration guide request");
    const message = typeof request.message === "string" ? request.message.trim() : "";
    if (!message) throw new TypeError("configuration message must be a non-empty string.");
    if (message.length > 2_000) throw new RangeError("configuration message cannot exceed 2000 characters.");
    return this.#request(
      ROUTES.configurationGuide,
      "POST",
      { message },
      options,
    );
  }

  /**
   * Opens the read-only, resumable JoeOS domain-event stream.
   * @param {EventSubscriptionOptions} options
   * @returns {EventSubscriptionHandle}
   */
  subscribeEvents(options) {
    requireRecord(options, "event subscription options");
    if (typeof options.onEvent !== "function") {
      throw new TypeError("event subscription onEvent must be a function.");
    }
    if (options.onStatus !== undefined && typeof options.onStatus !== "function") {
      throw new TypeError("event subscription onStatus must be a function.");
    }
    if (options.signal !== undefined && !isAbortSignal(options.signal)) {
      throw new TypeError("event subscription signal must be an AbortSignal.");
    }
    if (!this.#webSocketFactory) {
      throw new TypeError("A WebSocket implementation is required to subscribe to events.");
    }

    const initialCursor = normalizeCursor(options.after ?? 0, "event subscription after");
    const policy = reconnectPolicy(options.reconnect);
    const scheduler = this.#scheduler;
    const socketFactory = this.#webSocketFactory;
    const onEvent = options.onEvent;
    const onStatus = options.onStatus;
    const externalSignal = options.signal;

    let activeSocket = null;
    let activeListeners = null;
    let reconnectTimer = null;
    let lastCursor = initialCursor;
    let failureCount = 0;
    let stopped = false;
    let closedStatusSent = false;

    const emitStatus = (status) => {
      if (!onStatus) return;
      try {
        onStatus(Object.freeze({ cursor: lastCursor, ...status }));
      } catch {
        // Observer failures never destabilize or reconnect the transport.
      }
    };

    const detachSocket = (socket) => {
      if (!socket || !activeListeners) return;
      for (const [type, listener] of Object.entries(activeListeners)) {
        socket.removeEventListener(type, listener);
      }
      activeListeners = null;
    };

    const clearReconnect = () => {
      if (reconnectTimer === null) return;
      scheduler.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    };

    const emitClosed = (details = {}) => {
      if (closedStatusSent) return;
      closedStatusSent = true;
      emitStatus({ state: "closed", ...details });
    };

    const removeAbortListener = () => {
      if (externalSignal) externalSignal.removeEventListener("abort", abortSubscription);
    };

    const finalize = (details = {}) => {
      if (stopped) return;
      stopped = true;
      clearReconnect();
      removeAbortListener();
      emitClosed(details);
    };

    const close = () => {
      if (stopped) return;
      stopped = true;
      clearReconnect();
      removeAbortListener();
      const socket = activeSocket;
      activeSocket = null;
      detachSocket(socket);
      if (socket) {
        try {
          socket.close(1000, "client closed");
        } catch {
          // Closing is idempotent even if the implementation is already closed.
        }
      }
      emitClosed({ code: 1000, reason: "client closed" });
    };

    function abortSubscription() {
      if (stopped) return;
      stopped = true;
      clearReconnect();
      removeAbortListener();
      const socket = activeSocket;
      activeSocket = null;
      detachSocket(socket);
      if (socket) {
        try {
          socket.close(1000, "caller aborted");
        } catch {
          // The subscription remains terminal even if close itself fails.
        }
      }
      emitClosed({ code: 1000, reason: "caller aborted" });
    }

    const scheduleReconnect = (details = {}) => {
      if (stopped) return;
      failureCount += 1;
      if (!policy.enabled || failureCount > policy.maxAttempts) {
        finalize(details);
        return;
      }
      const delayMs = Math.min(
        policy.maxDelayMs,
        policy.initialDelayMs * (2 ** Math.min(failureCount - 1, 30)),
      );
      emitStatus({
        state: "reconnecting",
        attempt: failureCount,
        delayMs,
        ...details,
      });
      reconnectTimer = scheduler.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, delayMs);
    };

    const acceptMessage = (event) => {
      if (stopped) return;
      const envelope = parseEventEnvelope(event?.data);
      if (!envelope) {
        emitStatus({ state: "invalid_event", reason: "invalid event envelope" });
        return;
      }

      if (envelope.event_type === "audit.event") {
        if (envelope.cursor <= lastCursor) return;
        lastCursor = envelope.cursor;
      } else if (envelope.cursor !== lastCursor) {
        emitStatus({ state: "invalid_event", reason: "transient event cursor mismatch" });
        return;
      }

      failureCount = 0;
      try {
        onEvent(Object.freeze(envelope));
      } catch {
        emitStatus({ state: "event_handler_error", reason: "event handler threw" });
      }
    };

    const connect = () => {
      if (stopped) return;
      const url = eventSocketUrl(this.#baseUrl, lastCursor);
      emitStatus({ state: "connecting", attempt: failureCount });

      let socket;
      try {
        socket = socketFactory(url);
      } catch {
        emitStatus({ state: "socket_error", reason: "WebSocket construction failed" });
        scheduleReconnect({ reason: "WebSocket construction failed" });
        return;
      }
      if (
        !socket ||
        typeof socket.addEventListener !== "function" ||
        typeof socket.removeEventListener !== "function" ||
        typeof socket.close !== "function"
      ) {
        emitStatus({ state: "socket_error", reason: "Invalid WebSocket implementation" });
        try {
          socket?.close?.(1002, "invalid WebSocket implementation");
        } catch {
          // The invalid adapter is discarded regardless of close behavior.
        }
        scheduleReconnect({ reason: "Invalid WebSocket implementation" });
        return;
      }

      activeSocket = socket;
      const listeners = {
        open: () => {
          if (!stopped && socket === activeSocket) {
            emitStatus({ state: "open", attempt: failureCount });
          }
        },
        message: acceptMessage,
        error: () => {
          if (!stopped && socket === activeSocket) {
            emitStatus({ state: "socket_error", reason: "WebSocket transport error" });
          }
        },
        close: (event) => {
          if (socket !== activeSocket) return;
          detachSocket(socket);
          activeSocket = null;
          if (stopped) return;
          scheduleReconnect({
            code: Number.isInteger(event?.code) ? event.code : 1006,
            reason: typeof event?.reason === "string" && event.reason ? event.reason.slice(0, 123) : "connection closed",
          });
        },
      };
      activeListeners = listeners;
      for (const [type, listener] of Object.entries(listeners)) {
        socket.addEventListener(type, listener);
      }
    };

    const handle = {};
    Object.defineProperties(handle, {
      close: { value: close, enumerable: true },
      cursor: { get: () => lastCursor, enumerable: true },
      closed: { get: () => stopped, enumerable: true },
    });
    Object.freeze(handle);

    if (externalSignal?.aborted) {
      stopped = true;
      emitClosed({ code: 1000, reason: "caller aborted" });
      return handle;
    }
    if (externalSignal) externalSignal.addEventListener("abort", abortSubscription, { once: true });
    connect();
    return handle;
  }
}

/** @param {JoeOSClientOptions} [options] */
export function createJoeOSClient(options) {
  return new JoeOSClient(options);
}

export const joeosSdkVersion = SDK_VERSION;
