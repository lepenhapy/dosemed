const CACHE = 'dosemed-v32';
const SHELL = ['/'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL).catch(() => {}))
  );
  // skipWaiting imediato — garante que iOS receba o novo index.html sem depender do banner
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  // clients.claim() faz o novo SW assumir as abas abertas após skipWaiting
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // API calls: network-first, sem cache
  if (url.pathname === '/privacidade' ||
      url.pathname === '/termos' ||
      url.pathname.startsWith('/reconhecer-imagem') ||
      url.pathname.startsWith('/exportar/') ||
      url.pathname.startsWith('/bulas/') ||
      url.pathname.startsWith('/bulas-index') ||
      url.pathname.startsWith('/dashboard') ||
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
      url.pathname.startsWith('/farmacia/anuncio') ||
      url.pathname.startsWith('/feedback') ||
      url.pathname.startsWith('/admin/feedbacks') ||
      url.pathname.startsWith('/promocoes') ||
      url.pathname.startsWith('/categorias')) {
    e.respondWith(fetch(e.request).catch(() => new Response('{"erro":"offline"}', { headers: { 'Content-Type': 'application/json' } })));
    return;
  }

  // Shell: cache-first (abre instantâneo), atualiza em segundo plano
  e.respondWith(
    caches.open(CACHE).then(cache =>
      cache.match(e.request).then(cached => {
        const networkFetch = fetch(e.request).then(res => {
          // Só salva no cache se for o DoseMed de verdade (HTML próprio)
          if (res.ok && res.headers.get('content-type')?.includes('text/html')) {
            cache.put(e.request, res.clone());
          }
          return res;
        }).catch(() => null);
        return cached || networkFetch;
      })
    )
  );
});

// Permite ativação forçada via postMessage
self.addEventListener('message', e => {
  if (e.data?.type === 'SKIP_WAITING') self.skipWaiting();
});

// --- Push Notifications ---
self.addEventListener('push', e => {
  let data = { title: '💊 DoseMed', body: 'Hora do remédio!', tag: 'dosemed-alarme' };
  try { data = { ...data, ...e.data.json() }; } catch {}
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/icon-192.png',
      badge: '/icon-72.png',
      vibrate: [400, 100, 400, 100, 400, 200, 400, 100, 400],
      requireInteraction: true,
      renotify: true,
      tag: data.tag,
      actions: [
        { action: 'tomei', title: 'Tomei ✓' },
        { action: 'abrir', title: 'Ver app' }
      ]
    })
  );
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  if (e.action === 'tomei') return; // só fecha, sem abrir o app
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) {
        if (c.url.includes(self.location.origin) && 'focus' in c) return c.focus();
      }
      return clients.openWindow('/');
    })
  );
});
