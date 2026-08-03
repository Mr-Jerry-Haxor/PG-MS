// PG-MS Service Worker
const CACHE_VERSION = 'pgms-v1.0.1';
const CACHE_NAME = `pgms-cache-${CACHE_VERSION}`;

// Assets to cache on install
const STATIC_ASSETS = [
  '/static/css/app.css',
  '/static/img/favicon.png',
  '/static/img/icon-192x192.png',
  '/static/img/icon-512x512.png',
  '/static/manifest.json',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js'
];

// Install event - cache static assets
self.addEventListener('install', (event) => {
  console.log('[Service Worker] Installing...');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('[Service Worker] Caching static assets');
        return cache.addAll(STATIC_ASSETS.map(url => new Request(url, { cache: 'reload' })));
      })
      .then(() => {
        console.log('[Service Worker] Installation complete');
        return self.skipWaiting();
      })
      .catch((error) => {
        console.error('[Service Worker] Installation failed:', error);
      })
  );
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  console.log('[Service Worker] Activating...');
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name.startsWith('pgms-cache-') && name !== CACHE_NAME)
            .map((name) => {
              console.log('[Service Worker] Deleting old cache:', name);
              return caches.delete(name);
            })
        );
      })
      .then(() => {
        console.log('[Service Worker] Activation complete');
        return self.clients.claim();
      })
  );
});

// Fetch event - network first, fallback to cache
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip cross-origin requests and non-GET requests
  if (url.origin !== location.origin || request.method !== 'GET') {
    return;
  }

  // Network first strategy for API calls and dynamic content
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/pg/') ||
    url.pathname.startsWith('/admin/') ||
    url.pathname.includes('/booking/') ||
    url.pathname.includes('/leave/')
  ) {
    event.respondWith(
      fetch(request)
        .catch(() => {
          // Authenticated/dynamic responses must not be cached under stale or
          // missing URLs.
          if ((request.headers.get('accept') || '').includes('text/html')) {
            return new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
          }
          return new Response('Network error', { status: 503 });
        })
    );
    return;
  }

  // Cache first strategy for static assets
  if (
    url.pathname.startsWith('/static/') ||
    url.hostname === 'cdn.jsdelivr.net' ||
    request.destination === 'style' ||
    request.destination === 'script' ||
    request.destination === 'image' ||
    request.destination === 'font'
  ) {
    event.respondWith(
      caches.match(request)
        .then((cached) => {
          if (cached) {
            // Update cache in background
            fetch(request).then((response) => {
              caches.open(CACHE_NAME).then((cache) => {
                cache.put(request, response);
              });
            }).catch(() => {});
            return cached;
          }
          // If not in cache, fetch and cache
          return fetch(request).then((response) => {
            const responseToCache = response.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseToCache);
            });
            return response;
          });
        })
        .catch(() => {
          return new Response('Asset not available', { status: 503 });
        })
    );
    return;
  }

  // Default: network first for everything else
  event.respondWith(
    fetch(request)
      .catch(() => caches.match(request))
  );
});

// Handle messages from clients
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  
  if (event.data && event.data.type === 'CLEAR_CACHE') {
    event.waitUntil(
      caches.keys().then((cacheNames) => {
        return Promise.all(
          cacheNames.map((name) => caches.delete(name))
        );
      }).then(() => {
        event.ports[0].postMessage({ success: true });
      })
    );
  }
});

// Push notification support
self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    payload = { body: event.data ? event.data.text() : 'New notification' };
  }

  const title = payload?.notification?.title || payload?.title || 'PG-MS';
  const body = payload?.notification?.body || payload?.body || 'You have a new update.';
  const targetUrl = payload?.data?.url || payload?.fcmOptions?.link || '/';

  const options = {
    body,
    icon: payload?.notification?.icon || '/static/img/icon-192x192.png',
    badge: payload?.notification?.badge || '/static/img/icon-72x72.png',
    vibrate: [100, 50, 100],
    data: {
      url: targetUrl,
      dateOfArrival: Date.now(),
    }
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// Handle notification clicks with deep-link support
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const url = (event.notification && event.notification.data && event.notification.data.url) || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if ('focus' in client && client.url.includes(self.location.origin)) {
          client.navigate(url);
          return client.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});
