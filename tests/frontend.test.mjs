import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const html = await readFile(new URL("../index.html", import.meta.url), "utf8");

test("uses only same-origin JoeOS API routes", () => {
  for (const endpoint of ["/api/metrics", "/api/bots", "/api/events", "/api/v1/ai/chat/stream"]) {
    assert.match(html, new RegExp(`["']${endpoint.replaceAll("/", "\\/")}`));
  }
  assert.doesNotMatch(html, /API_BASE_URL\s*\+/);
  assert.doesNotMatch(html, /https?:\/\/localhost(?::\d+)?\/api\//i);
  assert.doesNotMatch(html, /https?:\/\/127\.0\.0\.1(?::\d+)?\/api\//i);
});

test("contains no Supabase integration or secret-shaped credentials", () => {
  assert.doesNotMatch(html, /supabase|\/rest\/v1\//i);
  assert.doesNotMatch(html, /SUPABASE_(?:ANON|SECRET|SERVICE_ROLE|URL)/i);
  assert.doesNotMatch(html, /eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/);
  assert.doesNotMatch(html, /github_pat_[a-zA-Z0-9_]+/i);
  assert.doesNotMatch(html, /(?:sk|rk)_(?:test|live)_[a-zA-Z0-9]+/i);
});

test("polls local telemetry every five seconds", () => {
  assert.match(html, /setInterval\([\s\S]*?5000\s*\)/);
  assert.match(html, /\/api\/metrics/);
  assert.match(html, /\/api\/events/);
});

test("uses the same-origin SDK for resumable live events with polling fallback", () => {
  assert.match(html, /import\(["']\/sdk\/index\.js["']\)/);
  assert.match(html, /client\.subscribeEvents\(/);
  assert.match(html, /after:\s*eventCursorRef\.current/);
  assert.match(html, /reconnect:\s*\{\s*initialDelayMs:\s*500,\s*maxDelayMs:\s*15000\s*\}/);
  assert.match(html, /5S POLLING FALLBACK/);
});

test("is installable as a mobile web app", () => {
  assert.match(html, /<link[^>]+rel=["']manifest["'][^>]+href=["']\/manifest\.webmanifest["']/i);
  assert.match(html, /navigator\.serviceWorker\.register\(["']\/sw\.js["']\)/);
  assert.match(html, /apple-mobile-web-app-capable/i);
  assert.match(html, /viewport-fit=cover/i);
});

test("all inline JavaScript parses without JSX or Babel", () => {
  const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)];
  assert.ok(scripts.length >= 1);
  for (const script of scripts) {
    if (script[1].trim()) new vm.Script(script[1]);
  }
  assert.match(html, /React\.createElement/);
  assert.doesNotMatch(html, /type=["']text\/babel["']/i);
  assert.doesNotMatch(html, /<App(?:\s|\/|>)/);
});

test("ships Mission Control as a persistent customizable widget workspace", () => {
  assert.match(html, /id:\s*["']mission["'][^\n]+Mission Control/);
  assert.match(html, /function MissionControlSection\(/);
  assert.match(html, /function WidgetCatalogModal\(/);
  assert.match(html, /function CustomizerPanel\(/);
  assert.match(html, /function CommandPalette\(/);
  assert.match(html, /requestJson\(["']\/api\/workspace["']/);
  assert.match(html, /method:\s*["']PUT["']/);
  assert.match(html, /If-Match/);
  assert.match(html, /\/api\/configuration\/guide/);
  assert.match(html, /Font family/);
  assert.match(html, /Text color/);
  assert.match(html, /Canvas color/);
});

test("provides non-drag layout controls and honest integration states", () => {
  assert.match(html, /Move ["'] \+ definition\.name \+ ["'] earlier/);
  assert.match(html, /Make ["'] \+ definition\.name \+ ["'] wider/);
  assert.match(html, /Hide ["'] \+ definition\.name/);
  assert.match(html, /Integration not connected/);
  assert.match(html, /INTEGRATION REQUIRED/);
  assert.match(html, /browser chat never launches a shell/i);
});

test("ships a Plugin Manager driven by real plugin state", () => {
  assert.match(html, /id: ["']plugins["'], label: ["']Plugin Manager["']/);
  assert.match(html, /function PluginsSection\(/);
  assert.match(html, /\/api\/v1\/plugins/);
  assert.match(html, /loadPluginsFromApi\(\)/);
  assert.match(html, /quarantined/);
  assert.match(html, /isolated Extension Host/i);
});

test("ships an Automation workspace driven by real workflow state", () => {
  assert.match(html, /id: ["']automation["'], label: ["']Automation["']/);
  assert.match(html, /function AutomationSection\(/);
  assert.match(html, /\/api\/v1\/automation\/workflows/);
  assert.match(html, /loadAutomationFromApi\(\)/);
  assert.match(html, /pending_approvals/);
});

test("ships a Communications workspace driven by real notification state", () => {
  assert.match(html, /id: ["']communications["'], label: ["']Communications["']/);
  assert.match(html, /function CommunicationsSection\(/);
  assert.match(html, /\/api\/v1\/communications\/notifications/);
  assert.match(html, /loadCommunicationsFromApi\(\)/);
  assert.match(html, /external sending always requires approval/i);
});

test("ships a Device Manager driven by real wearable state", () => {
  assert.match(html, /id: ["']wearables["'], label: ["']Device Manager["']/);
  assert.match(html, /function WearablesSection\(/);
  assert.match(html, /\/api\/v1\/wearables\/devices/);
  assert.match(html, /loadWearablesFromApi\(\)/);
  assert.match(html, /Camera and microphone activity always shows an indicator/i);
});

test("ships a Mobile Companion driven by real client state", () => {
  assert.match(html, /id: ["']mobile["'], label: ["']Mobile Companion["']/);
  assert.match(html, /function MobileSection\(/);
  assert.match(html, /\/api\/v1\/mobile\/clients/);
  assert.match(html, /loadMobileFromApi\(\)/);
  assert.match(html, /No fake hosts, connections, or push deliveries are shown/i);
});

test("ships a Security Center driven by real security state", () => {
  assert.match(html, /id: ["']security-center["'], label: ["']Security Center["']/);
  assert.match(html, /function SecurityCenterSection\(/);
  assert.match(html, /\/api\/v1\/security\/security-events/);
  assert.match(html, /loadSecurityFromApi\(\)/);
  assert.match(html, /No fabricated vulnerability scores or compliance claims are shown/i);
});

test("ships a Performance Center driven by real measured state", () => {
  assert.match(html, /id: ["']performance["'], label: ["']Performance Center["']/);
  assert.match(html, /function PerformanceSection\(/);
  assert.match(html, /\/api\/v1\/performance\/overview/);
  assert.match(html, /loadPerformanceFromApi\(\)/);
  assert.match(html, /Unknown metrics stay unknown/i);
  assert.match(html, /No fabricated FPS, latency, or hardware utilization values are shown/i);
});

test("ships a Settings surface for appearance and accessibility", () => {
  assert.match(html, /id: ["']settings["'], label: ["']Settings["']/);
  assert.match(html, /function SettingsSection\(/);
  assert.match(html, /function defaultUiPrefs\(/);
  assert.match(html, /joeos:ui-prefs/);
  assert.match(html, /theme:\s*"system"/);
  assert.match(html, /setAttribute\(\s*["']data-theme["']\s*,\s*effectiveTheme/);
});

test("ships one semantic design token registry", () => {
  assert.match(html, /--color-text-primary\s*:/);
  assert.match(html, /--color-status-critical\s*:/);
  assert.match(html, /--color-action-destructive\s*:/);
  assert.match(html, /--color-focus-ring\s*:/);
  assert.match(html, /--duration-standard\s*:/);
  assert.match(html, /--z-dialog\s*:/);
  assert.match(html, /--touch-target\s*:/);
});

test("supports themes, high contrast, density, reduced effects and motion", () => {
  assert.match(html, /html\[data-theme="light"\]/);
  assert.match(html, /html\[data-contrast="high"\]/);
  assert.match(html, /html\[data-density="compact"\]/);
  assert.match(html, /html\[data-effects="reduced"\]/);
  assert.match(html, /html\[data-motion="reduced"\]/);
  assert.match(html, /prefers-reduced-motion: reduce/);
  assert.match(html, /prefers-color-scheme: dark/);
});

test("ships a keyboard-first focus system with skip link and focus traps", () => {
  assert.match(html, /className: ["']skip-link["']/);
  assert.match(html, /Skip to main content/);
  assert.match(html, /function useFocusTrap\(/);
  assert.match(html, /aria-modal"\s*:\s*["']true["']/);
  assert.match(html, /document\.activeElement/);
});

test("has navigation landmarks, live system regions, and sr-only unread context", () => {
  assert.match(html, /role: "navigation"/);
  assert.match(html, /"aria-label": "Primary navigation"/);
  assert.match(html, /"aria-label": "Mobile navigation"/);
  assert.match(html, /"aria-label": "System conditions"/);
  assert.match(html, /banner-stack/);
  assert.match(html, /"aria-live": "polite"/);
  assert.match(html, /sr-only/);
  assert.match(html, /className: "sr-only"/);
  assert.match(html, /unread notifications/);
  assert.match(html, /"aria-label": "Operational notices"/);
  assert.match(html, /"aria-current": props\.active === item\.id/);
});

test("centralizes keyboard shortcuts in one registry with a reference dialog", () => {
  assert.match(html, /var KEYBOARD_SHORTCUTS\s*=/);
  assert.match(html, /Open Command Palette/);
  assert.match(html, /function ShortcutsDialog\(/);
  assert.match(html, /Open keyboard shortcuts/);
  assert.match(html, /event\.key === "\?"/);
});

test("every registered shortcut has a matching global keydown handler (no drift)", () => {
  const registry = html.match(/var KEYBOARD_SHORTCUTS\s*=\s*\[([\s\S]*?)\];/)[1];
  const handlerSource = html.slice(html.indexOf("var meta = event.metaKey || event.ctrlKey;"));
  const checks = [
    ["Ctrl+K", /meta && key === "k"/],
    ["Ctrl+Shift+K", /meta && key === "k"[\s\S]*?event\.shiftKey[\s\S]*?setAssistantOpen/],
    ["Ctrl+Shift+N", /event\.shiftKey && key === "n"/],
    ["Ctrl+,", /meta && key === ","/],
    ["Ctrl+F", /meta && key === "f"/],
    ["Ctrl+/", /meta && key === "\/"/],
    ["?", /event\.key === "\?"/],
    ["Esc", /event\.key === "Escape"/],
    ["Alt+1..0", /event\.altKey/],
  ];
  for (const [, re] of checks) assert.match(handlerSource, re);
  assert.match(registry, /Ctrl\+F/);
  assert.doesNotMatch(handlerSource, /meta && key === "g"/);
});

test("command palette ranks results, groups by category, and marks risk", () => {
  assert.match(html, /function paletteScore\(/);
  assert.match(html, /palette-group/);
  assert.match(html, /palette-caption/);
  assert.match(html, /palette-risk/);
  assert.match(html, /risk: ["']security["']/);
  assert.match(html, /role: ["']option["']/);
});

test("exposes a consistent status badge primitive with non-color labels", () => {
  assert.match(html, /function StatusBadge\(/);
  assert.match(html, /status-badge/);
  assert.match(html, /status-running/);
  assert.match(html, /aria-label": label/);
});

test("shows persistent conditions in an accessible banner region", () => {
  assert.match(html, /banner-stack/);
  assert.match(html, /system-banner/);
  assert.match(html, /Lockdown is active/);
  assert.match(html, /Lemonade Server is offline/);
  assert.match(html, /aria-label": "System conditions"/);
});

test("supports stopping the active AI operation with honest state", () => {
  assert.match(html, /function cancelAssistant\(/);
  assert.match(html, /Stop current operation/);
  assert.match(html, /Generation stopped by the operator/);
});

test("uses calm, precise microcopy without blame or hype", () => {
  assert.doesNotMatch(html, /\bOops\b|\bUh-oh\b|\bYou broke it\b/i);
  assert.doesNotMatch(html, /\bSupercharge\b|\bRevolutionary\b|\bGuaranteed\b/i);
});

test("ships a Models & AI workspace driven by real local runtime state", () => {
  assert.match(html, /id: ["']ai["'], label: ["']Models & AI["']/);
  assert.match(html, /function AiSection\(/);
  assert.match(html, /\/api\/v1\/ai\/overview/);
  assert.match(html, /loadAiFromApi\(\)/);
  assert.match(html, /never presented as parsed facts/i);
  assert.match(html, /cloud routing is never silent/i);
});

test("ships a Production & Release workspace with honest gates and targets", () => {
  assert.match(html, /id: ["']production["'], label: ["']Production & Release["']/);
  assert.match(html, /function ProductionSection\(/);
  assert.match(html, /\/api\/v1\/production\/status/);
  assert.match(html, /loadProductionFromApi\(\)/);
  assert.match(html, /not configured — never as passing/i);
  assert.match(html, /Create verified backup/);
  assert.match(html, /Enter Safe Mode/);
});

test("ships a Memory workspace driven by the real memory platform", () => {
  assert.match(html, /id: ["']memory["'], label: ["']Memory["']/);
  assert.match(html, /function KnowledgeSection\(/);
  assert.match(html, /function MemoryView\(/);
  assert.match(html, /\/api\/v1\/memory\/overview/);
  assert.match(html, /\/api\/v1\/memory\/records\?limit=500/);
  assert.match(html, /\/api\/v1\/memory\/search/);
  assert.match(html, /loadMemoryFromApi\(\)/);
  assert.match(html, /token-overlap/);
});

test("Memory workspace exposes provenance, evidence, and lifecycle without secrets", () => {
  assert.match(html, /function MemoryDetail\(/);
  assert.match(html, /Provenance/);
  assert.match(html, /Evidence/);
  assert.match(html, /Lifecycle/);
  assert.match(html, /\/memory\/records\/["'] \+ encodeURIComponent/);
  assert.match(html, /correct/);
  assert.match(html, /supersede/);
  assert.match(html, /explicit browser forget/);
  assert.match(html, /embedding: /);
});

test("Memory review queue resolves items through the authoritative API", () => {
  assert.match(html, /\/api\/v1\/memory\/review\?state=open/);
  assert.match(html, /function ReviewPanel\(/);
  assert.match(html, /resolveMemoryReview\(/);
  assert.match(html, /action: action, note: "resolved from browser workspace"/);
});

test("ships a Files workspace over registered engineering projects and artifacts", () => {
  assert.match(html, /id: ["']files["'], label: ["']Files["']/);
  assert.match(html, /\/api\/v1\/engineering\/projects/);
  assert.match(html, /\/api\/v1\/agents\/artifacts\?limit=200/);
  assert.match(html, /function FilesBrowse\(/);
  assert.match(html, /files\/content\?path=/);
  assert.match(html, /masked_secrets/);
  assert.match(html, /The browser never reads the VPS filesystem directly/);
});

test("ships a Universal Search workspace orchestrating real per-domain search", () => {
  assert.match(html, /id: ["']search["'], label: ["']Search["']/);
  assert.match(html, /function SearchView\(/);
  assert.match(html, /runUniversalSearch\(/);
  assert.match(html, /\/api\/v1\/memory\/search\?q=/);
  assert.match(html, /\/api\/v1\/engineering\/projects\/["'] \+ encodeURIComponent/);
  assert.match(html, /Search memory, project files, agents, and artifacts/);
});

test("ships a Context workspace that excludes secrets and hidden reasoning", () => {
  assert.match(html, /id: ["']context["'], label: ["']Context["']/);
  assert.match(html, /function ContextView\(/);
  assert.match(html, /Chain-of-thought excluded/);
  assert.match(html, /Hidden reasoning and secrets are never stored/);
  assert.match(html, /knowledge workspace/i);
});

test("knowledge apps register deep links and palette commands", () => {
  assert.match(html, /appId === "memory"/);
  assert.match(html, /appId === "files"/);
  assert.match(html, /appId === "search"/);
  assert.match(html, /appId === "context"/);
  assert.match(html, /memory-open/);
  assert.match(html, /search-open/);
  assert.match(html, /context-open/);
});

test("knowledge layer avoids fabricating vector search", () => {
  assert.match(html, /bounded token-overlap/);
  assert.doesNotMatch(html, /cosineSimilarity\(/);
  assert.doesNotMatch(html, /fake.*embedding|embedding.*fake/i);
});

test("function dedup: clickable object cards, no button forests, single Joe invocation", () => {
  // The Command Center module card is itself the object: role=button, keyboard
  // openable, and the redundant Open/Focus/Ask Joe button row is gone.
  assert.match(html, /function openModule\(opts\)/);
  assert.match(html, /className: "cc-module glass-card "\s*\+\s*\(opts\.tone \|\| ""\)\s*\+\s*\(pinned \? " cc-pinned" : ""\), "data-module": opts\.module, key: opts\.module, role: "button", tabIndex: 0/);
  assert.match(html, /onKeyDown: function \(event\) \{ if \(event\.key === "Enter" \|\| event\.key === " "\)/);
  // No per-card "Open"/"Focus"/"Joe" button row remains on module cards.
  assert.doesNotMatch(html, /cc-module-actions["']\s*,\s*\n?\s*h\("button",[^)]*"secondary-button",[^)]*opts\.openLabel \|\| "Open"/);
  // The dedicated "Ask Joe" module card is removed (orb is canonical).
  assert.doesNotMatch(html, /\{ module: "ask-joe"/);
  // Agent cards are clickable objects with no redundant Open workspace / Ask Joe row.
  assert.match(html, /className: "glass-card interactive-card agent-card", role: "button", tabIndex: 0/);
  assert.doesNotMatch(html, /"Open workspace"/);
  // Exactly one canonical Joe invoker surface family (topbar Joe + FAB orb), no "Ask JoeOS AI" text.
  assert.match(html, /assistant-top-orb/);
  assert.doesNotMatch(html, /"Ask JoeOS AI"/);
  // Overflow menu (ellipsis) preserved for secondary actions.
  assert.match(html, /cc-more ghost-button/);
  assert.match(html, /role: "menuitem"/);
});
