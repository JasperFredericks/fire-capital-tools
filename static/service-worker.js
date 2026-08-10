/*
 * Fire Capital Tools — Service Worker (V1)
 *
 * Strategy: cache-first for safe static assets only.
 * All authenticated routes, API calls, tool results, and POST
 * requests are intentionally never cached.
 */

const CACHE_NAME = 'fire-capital-static-v1';

// Only safe, versioned static assets that never contain private data
const PRECACHE_ASSETS = [
  '/static/style.css',
  '/static/img/icon-192.png',
  '/static/img/icon-512.png',
  '/static/img/logo-mark.svg',
  '/static/offline.html',
];

// URL prefixes that must NEVER be served from cache
const BYPASS_PREFIXES = [
  '/tools/',
  '/auth/',
  '/login',
  '/logout',
  '/dashboard',
  '/feedback/',
  '/static/uploads/',
];

// ── Install: precache static assets ────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_ASSETS))
  );
  self.skipWaiting();
});

// ── Activate: remove old caches ─────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// ── Fetch: network-first with offline fallback ──────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Only handle same-origin GET requests
  if (request.method !== 'GET' || url.origin !== self.location.origin) {
    return;
  }

  // Always bypass private/authenticated/dynamic routes — let browser handle normally
  const shouldBypass = BYPASS_PREFIXES.some((prefix) =>
    url.pathname.startsWith(prefix)
  );
  if (shouldBypass) {
    return;
  }

  // For precached static assets: cache-first, fall back to network
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request))
    );
    return;
  }

  // For everything else (root, manifest, service-worker itself):
  // network-first, serve offline page if completely unreachable
  event.respondWith(
    fetch(request).catch(() =>
      caches.match('/static/offline.html')
    )
  );
});
