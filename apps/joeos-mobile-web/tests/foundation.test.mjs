import assert from "node:assert/strict";
import { readFile, access } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const appRoot = join(dirname(fileURLToPath(import.meta.url)), "..");

const read = (name) => readFile(join(appRoot, name), "utf8");
const exists = async (name) => {
  try {
    await access(join(appRoot, name));
    return true;
  } catch {
    return false;
  }
};

test("package.json declares the JoeOS mobile web client with an Expo entry", async () => {
  const pkg = JSON.parse(await read("package.json"));
  assert.equal(pkg.name, "joeos-mobile-web");
  assert.equal(pkg.private, true);
  assert.equal(pkg.main, "expo-router/entry");
  for (const script of ["start", "start:web", "web", "build", "serve", "typecheck", "test"]) {
    assert.ok(pkg.scripts[script], `missing script ${script}`);
  }
});

test("package-lock.json locks the same package", async () => {
  const lock = JSON.parse(await read("package-lock.json"));
  assert.equal(lock.name, "joeos-mobile-web");
  assert.equal(lock.packages[""].name, "joeos-mobile-web");
});

test("app.json declares a web-only JoeOS Expo app", async () => {
  const app = JSON.parse(await read("app.json"));
  assert.equal(app.expo.name, "JoeOS");
  assert.equal(app.expo.slug, "joeos-mobile-web");
  assert.deepEqual(app.expo.platforms, ["web"]);
  assert.equal(app.expo.web.bundler, "metro");
  assert.equal(app.expo.web.output, "single");
});

test("tsconfig enables strict and unsafe-index checks", async () => {
  const tsconfig = JSON.parse(await read("tsconfig.json"));
  assert.equal(tsconfig.compilerOptions.strict, true);
  assert.equal(tsconfig.compilerOptions.noUncheckedIndexedAccess, true);
  assert.ok(tsconfig.include.includes("**/*.tsx"));
});

test("expo-router entry routes exist", async () => {
  assert.ok(await exists("app/_layout.tsx"), "missing root layout");
  assert.ok(await exists("app/index.tsx"), "missing home route");
});

test("serve script resolves to a real script", async () => {
  const pkg = JSON.parse(await read("package.json"));
  const serveTarget = pkg.scripts.serve.replace(/^node /, "");
  assert.ok(await exists(serveTarget), `missing ${serveTarget}`);
});

test("app.json asset references resolve to real files", async () => {
  const app = JSON.parse(await read("app.json"));
  assert.ok(await exists(app.expo.web.favicon), `missing favicon ${app.expo.web.favicon}`);
});
