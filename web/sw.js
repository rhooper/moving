// Caches the app shell so the UI opens instantly and survives a dead spot in
// an empty house. Box data is NOT cached: a stale answer to "where is this
// box" is worse than an honest failure, and write queueing is deliberately out
// of scope for now (see README, "Not built").

const VERSION = "v2";
const SHELL = [
  "/",
  "/app.js",
  "/scan.js",
  "/text.js",
  "/jsQR.js",
  "/Inter.ttf",
  "/manifest.webmanifest",
  "/icon-192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION).then((cache) =>
      // addAll fails the whole install if any one asset 404s, which would
      // leave no service worker at all; tolerate individual misses.
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
