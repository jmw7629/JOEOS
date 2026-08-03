import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const html = await readFile(new URL("../index.html", import.meta.url), "utf8");

test("uses only same-origin JoeOS API routes", () => {
  for (const endpoint of ["/api/metrics", "/api/bots", "/api/events", "/api/chat"]) {
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
