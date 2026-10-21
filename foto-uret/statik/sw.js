// Uygulama kabuğunu önbelleğe alır: önce ağ, ağ yoksa önbellek.
// API istekleri (/api/) hiç önbelleğe alınmaz.
const SURUM = "foto-studyo-v5";
const KABUK = [
  "/",
  "/static/style.css?v=5",
  "/static/app.js?v=5",
  "/manifest.webmanifest",
  "/static/icon-192.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SURUM).then((c) => c.addAll(KABUK)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== SURUM).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    fetch(e.request, { cache: "no-cache" })
      .then((r) => {
        if (r.ok) {
          const kopya = r.clone();
          caches.open(SURUM).then((c) => c.put(e.request, kopya));
        }
        return r;
      })
      .catch(() => caches.match(e.request))
  );
});
