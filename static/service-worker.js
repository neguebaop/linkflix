const CACHE_NAME = "linkflix-v18";
const STATIC_ASSETS = [
  "/static/css/style.css",
  "/static/images/linkflix_splash.png",
  "/static/images/linkflix_logo.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png"
];
self.addEventListener("install", event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(STATIC_ASSETS)).catch(() => {}));
});
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/login")));
    return;
  }
  if (url.origin === self.location.origin) {
    event.respondWith(fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE_NAME).then(cache => cache.put(req, copy));
      return res;
    }).catch(() => caches.match(req)));
  }
});
