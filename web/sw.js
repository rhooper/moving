// Caches the app shell so the UI opens in a dead spot. Box data is NOT cached:
// a stale answer to "where is this box" is worse than an honest failure, and
// writes are not queued offline.

// Replaced with the deployed revision as served (the /sw.js route in app.py),
// so the shell cache rolls on every deploy. Only a dev checkout sees "dev".
const VERSION = "dev";
const SHELL = [
  "/",
  "/app.js",
  "/autosave.js",
  "/live.js",
  "/nesting.js",
  "/record.js",
  "/scan.js",
  "/segmented.js",
  "/text.js",
  "/covers.js",
  "/reload.js",
  "/wedge.js",
  "/jsQR.js",
  "/Inter.ttf",
  "/manifest.webmanifest",
  "/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION).then((cache) =>
      // Not addAll: one 404 would fail the install and leave no worker at all.
      Promise.allSettled(SHELL.map((url) => cache.add(url)))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n !== VERSION).map((n) => caches.delete(n))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api/")) return;   // always live
  // How an open page learns of a deploy: cached, the first answer would be the
  // only one, and the page would never reload.
  if (url.pathname === "/health") return;
  if (url.pathname.startsWith("/b/")) return;     // redirect must be followed

  event.respondWith(
    caches.match(request).then((hit) => {
      if (hit) return hit;
      return fetch(request).then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(VERSION).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch(() => caches.match("/"));  // offline navigation -> the shell
    })
  );
});
