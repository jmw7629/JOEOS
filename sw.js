const CACHE_NAME = "joeos-shell-v4";
const APP_SHELL = ["/", "/manifest.webmanifest", "/joeos-icon.svg", "/sdk/index.js"];
const NAVIGATION_CACHE_KEY = "/";

// Only responses that are genuinely part of the JoeOS application experience
// may enter the shell cache. A download/error/empty response for a module
// route (e.g. a stale build artifact at /os/build) must NEVER be cached as a
// navigation or shell asset, or Safari will keep offering it as a file
// download long after the server is repaired.
function isCacheableHtml(response, url) {
  if (!response || !response.ok) return false;
  const type = (response.headers.get("content-type") || "").toLowerCase();
  const disposition = (response.headers.get("content-disposition") || "").toLowerCase();
  if (disposition.includes("attachment")) return false;
  if (!type.includes("text/html") && !type.includes("application/manifest")) return false;
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz") return false;
  return true;
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname === "/healthz") return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // Never cache a download or error response as the shell.
          if (isCacheableHtml(response, url)) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(NAVIGATION_CACHE_KEY, copy));
          }
          return response;
        })
        .catch(() => caches.match(NAVIGATION_CACHE_KEY))
    );
    return;
  }

  // Non-navigation assets: only cache genuinely app-owned HTML/manifest
  // responses (never downloads, never API, never error bodies).
  event.respondWith(
    caches.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (isCacheableHtml(response, url) && url.pathname !== "/sw.js") {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
