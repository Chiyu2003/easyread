const CACHE = "easyread-pwa-v5";
self.addEventListener("install", (event) => event.waitUntil((async () => {
  const cache = await caches.open(CACHE);
  await cache.addAll(["/", "/web/manifest.webmanifest", "/web/favicon.svg", "/web/icons/icon-192.png", "/web/icons/icon-512.png"]);
  await self.skipWaiting();
})()));
self.addEventListener("activate", (event) => event.waitUntil((async () => {
  for (const key of await caches.keys()) if (key.startsWith("easyread-pwa-") && key !== CACHE) await caches.delete(key);
  await self.clients.claim();
})()));

self.addEventListener("message", (event) => {
  if (!event.data || event.data.type !== "CACHE_URLS") return;
  const client = event.source;
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE), urls = event.data.urls || [], failed = [];
    let done = 0, ok = 0;
    for (const raw of urls) {
      try {
        const url = new URL(raw, self.location.origin);
        if (url.origin !== self.location.origin) throw new Error("external URL");
        const response = await fetch(url.href, { cache: "no-store" });
        if (!response.ok) throw new Error(String(response.status));
        await cache.put(url.href, await cacheable(url, response));
        ok++;
      } catch (error) { failed.push({ url: raw, error: String(error) }); }
      done++;
      if (client) client.postMessage({ type: "CACHE_PROGRESS", done, total: urls.length });
    }
    if (client) client.postMessage({ type: "CACHE_DONE", ok, failed });
  })());
});

async function cacheable(url, response) {
  if (url.pathname.endsWith("/state")) {
    const state = await response.clone().json();
    delete state.token;
    const headers = new Headers(response.headers);
    headers.delete("Content-Length");
    headers.set("Content-Type", "application/json; charset=utf-8");
    return new Response(JSON.stringify(state), { status: response.status, statusText: response.statusText, headers });
  }
  return response.clone();
}

self.addEventListener("fetch", (event) => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return; // writes always require the live server
  const cachedReaderData = url.pathname.startsWith("/api/p/") && /\/(state|part\/|versions$)/.test(url.pathname);
  if (request.mode === "navigate" || cachedReaderData) {
    event.respondWith((async () => {
      try {
        const response = await fetch(request);
        if (response.ok) {
          const cache = await caches.open(CACHE);
          await cache.put(request, await cacheable(url, response));
        }
        return response;
      } catch (error) {
        const hit = (await caches.match(request)) || (request.mode === "navigate" ? (await caches.match("/")) : null);
        if (!hit) return Response.error();
        const headers = new Headers(hit.headers);
        headers.set("X-EasyRead-Offline", "1");
        return new Response(await hit.blob(), { status: hit.status, statusText: hit.statusText, headers });
      }
    })());
    return;
  }
  if (url.pathname.startsWith("/web/") || url.pathname.startsWith("/p/")) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE), hit = await cache.match(request);
      if (hit) return hit;
      const response = await fetch(request);
      if (response.ok) await cache.put(request, response.clone());
      return response;
    })());
  }
});
