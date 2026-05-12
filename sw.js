const CACHE = 'dosemed-v9';
const SHELL = ['/'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.matchAll({ type: 'window', includeUncontrolled: true }))
      .then(clients => {
        // Recarrega todas as janelas abertas para servir a versão nova
        clients.forEach(c => c.navigate(c.url));
      })
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API calls: network-first, sem cache
  if (url.pathname.startsWith('/dashboard') ||
      url.pathname.startsWith('/estoque') ||
      url.pathname.startsWith('/usuario') ||
      url.pathname.startsWith('/auth') ||
      url.pathname.startsWith('/historico') ||
      url.pathname.startsWith('/webhook') ||
      url.pathname.startsWith('/admin') ||
      url.pathname.startsWith('/bulas/enriquecer') ||
      url.pathname.startsWith('/log-busca') ||
      url.pathname.startsWith('/push') ||
      url.pathname.startsWith('/precos') ||
      url.pathname.startsWith('/alarmes') ||
      url.pathname.startsWith('/farmacia') ||
      url.pathname.startsWith('/orcamento') ||
      url.pathname.startsWith('/admin/anuncio') ||
      url.pathname.startsWith('/farmacia/anuncio')) {
    e.respondWith(fetch(e.request).catch(() => new Response('{"erro":"offline"}', { headers: { 'Content-Type': 'application/json' } })));
    return;
  }

  // Shell: network-first, com fallback para cache
  e.respondWith(
    fetch(e.request).then(res => {
      if (res.ok) {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
      }
      return res;
    }).catch(() => caches.match(e.request))
  );
});

// Permite ativação forçada via postMessage
self.addEventListener('message', e => {
  if (e.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

// --- Push Notifications ---
self.addEventListener('push', e => {
  let data = { title: '💊 DoseMed', body: 'Hora do remédio!' };
  try { data = { ...data, ...e.data.json() }; } catch {}
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/icon-192.png',
      badge: '/icon-72.png',
      vibrate: [200, 100, 200, 100, 200],
      requireInteraction: false,
      tag: 'dosemed-alarme'
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.includes(self.location.origin) && 'focus' in c) return c.focus();
      }
      return clients.openWindow('/');
    })
  );
});
