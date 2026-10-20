// Bump this string whenever you deploy a change you want pushed to installed apps.
const CACHE = 'r2r-client-v3';
const SHELL = ['./', './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-512-maskable.png', './apple-touch-icon.png',
  './expenses.html', './expenses.webmanifest', './expenses-icon-192.png', './expenses-icon-512.png', './expenses-icon-512-maskable.png', './expenses-touch-icon.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;

  // Only handle same-origin GETs. Google Sheets, CDNs etc. always go straight to network
  // so the dashboard never shows stale financial data.
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return;

  // Network first, fall back to cache when offline.
  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      })
      // offline: the same page without its ?c=&k= part, else the right app's start page
      .catch(() => caches.match(req, { ignoreSearch: true })
        .then((hit) => hit || caches.match(/expenses/.test(new URL(req.url).pathname) ? './expenses.html' : './index.html')))
  );
});
