// Network-only: never cache authenticated pages, files, credentials or API replies.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => { event.respondWith(fetch(event.request)); });
