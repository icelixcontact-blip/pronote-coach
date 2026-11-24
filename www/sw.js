const CACHE_NAME = 'pronote-coach-v2';
self.addEventListener('install', (e) => {
  self.skipWaiting();
});
self.addEventListener('activate', (e) => {
  e.waitUntil(clients.claim());
});
self.addEventListener('fetch', (e) => {
  // Always network-first for API & fresh school data
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
