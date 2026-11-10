/* Offline support for trains: last payload per train, plus the app shell.
   Everything online stays network-first; the cache is only a fallback. */
const SHELL = 'patri-shell-v2';
const API = 'patri-api-v2';

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL);
      // Best effort: a failed precache must not block the worker.
      await Promise.all(['/', '/index.html'].map((url) => cache.add(url).catch(() => {})));
      await self.skipWaiting();
    })()
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k !== SHELL && k !== API).map((k) => caches.delete(k)));
      await self.clients.claim();
    })()
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Live data: always ask the server first, so online users never see a cached
  // payload. The cache only answers when the network fails.
  if (url.pathname.startsWith('/api/train/')) {
    event.respondWith(networkFirst(request, API));
    return;
  }

  // Opening or reloading the app.
  if (request.mode === 'navigate') {
    event.respondWith(navigation(request));
    return;
  }

  // Build output is content-hashed, so a cache hit is byte-identical.
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirst(request));
    return;
  }

  // Fonts: serve from cache for speed, refresh in the background.
  if (url.pathname.startsWith('/fonts/')) {
    event.respondWith(staleWhileRevalidate(request, SHELL));
    return;
  }

  // Unhashed images and the manifest change on deploy, so prefer the network.
  if (/\.(svg|png|webmanifest)$/.test(url.pathname)) {
    event.respondWith(networkFirst(request, SHELL));
  }
});

async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch {
    return (await cache.match(request)) || Response.error();
  }
}

async function navigation(request) {
  const cache = await caches.open(SHELL);
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put('/index.html', response.clone());
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached =
      (await cache.match(request)) ||
      (await cache.match('/index.html')) ||
      (await cache.match('/'));
    return cached || Response.error();
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(SHELL);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) cache.put(request, response.clone());
  return response;
}

async function staleWhileRevalidate(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((response) => {
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || network;
}
