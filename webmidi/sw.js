// Keeps what the bridge needs on the phone, so that it starts without internet once it has started
// with it: the program's own files network first (a new version arrives when there is one), Python
// and mido from elsewhere cache first (their addresses name their version).
const CACHE = "ms-plus-bridge";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.pathname.startsWith("/sim/") || url.pathname.startsWith("/api/")) return;
  const own = url.origin === location.origin;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const kept = await cache.match(event.request);
    if (kept && !own) return kept;
    try {
      const response = await fetch(event.request);
      if (response.ok) await cache.put(event.request, response.clone());
      return response;
    } catch (error) {
      if (kept) return kept;
      throw error;
    }
  })());
});
