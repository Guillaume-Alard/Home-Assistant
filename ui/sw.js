/* Service worker Sentinel — réseau d'abord, cache en secours (usage hors ligne
   limité à l'UI ; la conversation exige évidemment le serveur). */
const CACHE = 'sentinel-v2';
const ASSETS = [
  './', 'index.html', 'css/main.css', 'manifest.webmanifest', 'icons/icon.svg',
  'js/app.js', 'js/ws.js', 'js/chat.js', 'js/viz.js',
  'js/audio-capture.js', 'js/audio-play.js', 'js/pcm-worklet.js',
];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname === '/ws' || url.pathname === '/health') return;
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return resp;
      })
      .catch(() => caches.match(e.request, { ignoreSearch: true }))
  );
});
