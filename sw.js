// Bump this string whenever you deploy a change you want pushed to installed apps.
const CACHE = 'r2r-client-v73';
const PUSH_CACHE = 'r2r-push';            // phone-alert settings of this device (kept across updates)
const PUSH_CFG = './__r2r-push-config';
const SHELL = ['./', './index.html', './old.html', './manifest.webmanifest', './icon-192.png', './icon-512.png', './icon-512-maskable.png', './apple-touch-icon.png',
  './expenses.html', './expenses.webmanifest', './expenses-icon-192.png', './expenses-icon-512.png', './expenses-icon-512-maskable.png', './expenses-touch-icon.png',
  './monitor.html', './monitor.webmanifest', './monitor-icon-192.png', './monitor-icon-512.png', './monitor-icon-512-maskable.png', './monitor-touch-icon.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== PUSH_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;

  // Only handle same-origin GETs. Google Sheets, CDNs etc. always go straight to network
  // so the dashboard never shows stale financial data.
  if (req.method !== 'GET' || new URL(req.url).origin !== self.location.origin) return;

  // Network first, fall back to cache when offline.
  // Pages are always re-checked with GitHub (cache: 'no-cache'), so an update shows on the next open
  // instead of up to 10 minutes later from the browser's own saved copy.
  const path = new URL(req.url).pathname;
  const page = req.mode === 'navigate' || /(\/|\.html|\.webmanifest|\.json)$/.test(path);
  e.respondWith(
    (page ? fetch(req.url, { cache: 'no-cache', credentials: 'same-origin' }) : fetch(req))
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      })
      // offline: the same page without its ?c=&k= part, else the right app's start page
      .catch(() => caches.match(req, { ignoreSearch: true })
        .then((hit) => hit || caches.match(/expenses/.test(new URL(req.url).pathname) ? './expenses.html' : /monitor/.test(new URL(req.url).pathname) ? './monitor.html' : './index.html')))
  );
});

// ═══════════════ Phone alerts (web push) ═══════════════
// The push itself is empty: it only wakes this phone. The words are collected from the R2R backend with
// this device's saved key (Push.gs → act=pushpull), so they never pass through Google's or Apple's servers.
async function pushCfg() {
  const c = await caches.open(PUSH_CACHE);
  const r = await c.match(PUSH_CFG);
  return r ? r.json() : null;
}
async function pushSaveSince(cfg, since) {
  const c = await caches.open(PUSH_CACHE);
  await c.put(PUSH_CFG, new Response(JSON.stringify(Object.assign({}, cfg, { since })), { headers: { 'Content-Type': 'application/json' } }));
}
function pushShow(m, quiet) {
  return self.registration.showNotification(m.title || 'R2R', {
    body: m.body || '', tag: m.tag || 'r2r', renotify: !quiet,
    icon: './icon-192.png', badge: './icon-192.png', data: { url: './index.html' + (m.tab ? '?tab=' + encodeURIComponent(m.tab) : '') },
  });
}
async function pushHandle() {
  const fallback = { title: 'R2R', body: 'You have a new alert. Tap to open your dashboard.', tag: 'r2r' };
  let cfg = null;
  try { cfg = await pushCfg(); } catch (e) { /* fine */ }
  if (!cfg || !cfg.api) return pushShow(fallback);
  try {
    const url = cfg.api + '?act=pushpull&cid=' + encodeURIComponent(cfg.c) + '&k=' + encodeURIComponent(cfg.k) + '&since=' + (Number(cfg.since) || 0) + '&n=' + Date.now();
    const res = await fetch(url, { redirect: 'follow', cache: 'no-store' });
    const j = await res.json();
    const items = j && j.ok && Array.isArray(j.items) ? j.items : [];
    if (!items.length) return pushShow(j && j.latest ? j.latest : fallback, !!(j && j.latest));   // already collected by an earlier wake-up: show the latest again (same tag, so no duplicate)
    await pushSaveSince(cfg, items[items.length - 1].t);
    return Promise.all(items.map((m) => pushShow(m)));
  } catch (e) { return pushShow(fallback); }
}
self.addEventListener('push', (e) => { e.waitUntil(pushHandle()); });

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const target = new URL((e.notification.data && e.notification.data.url) || './index.html', self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
    const open = list.find((w) => /\/index\.html|\/$/.test(new URL(w.url).pathname) && 'focus' in w);
    if (open) return open.focus();
    return self.clients.openWindow(target);
  }));
});

// the phone renewed its delivery address by itself: tell the backend
self.addEventListener('pushsubscriptionchange', (e) => {
  e.waitUntil((async () => {
    const cfg = await pushCfg(); if (!cfg) return;
    const sub = e.newSubscription || await self.registration.pushManager.subscribe(e.oldSubscription ? e.oldSubscription.options : { userVisibleOnly: true });
    await fetch(cfg.api + '?act=pushsub&cid=' + encodeURIComponent(cfg.c) + '&k=' + encodeURIComponent(cfg.k) + '&d=' + encodeURIComponent('Phone (renewed)') + '&s=' + encodeURIComponent(JSON.stringify(sub)) + '&n=' + Date.now(), { redirect: 'follow' });
  })().catch(() => {}));
});
