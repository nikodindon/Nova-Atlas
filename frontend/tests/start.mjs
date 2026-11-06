// Synthetic contracts and a loopback reverse proxy only. Never used by the product.
import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const day = '20261106';
const id = 'a'.repeat(64);
const eventId = 'event-fixture';
let expired = false,
  revoked = false;
const requests = [];
const fallback = {
  url: '/assets/news-fallback.svg',
  width: 320,
  height: 180,
  fallback: true,
  caption: 'Illustration indisponible',
  attribution: null,
};
const image = {
  url: '/media/images/' + id + '/320.jpg',
  large_url: '/media/images/' + id + '/768.jpg',
  width: 320,
  height: 180,
  fallback: false,
  caption: 'Illustration générique — fixture',
  attribution: {
    author: 'Fixture Nova Atlas',
    credit: 'Image synthétique de test',
    source: 'https://example.invalid/fixture',
    license: 'CC BY 4.0',
    license_url: 'https://creativecommons.org/licenses/by/4.0/',
    changes: 'Redimensionnement de test',
  },
};
const cats = [
  { id: 'tech', name: 'Tech & IA', icon: '⚡', active: true },
  { id: 'monde', name: 'Monde', icon: '🌐', active: true },
  { id: 'economie', name: 'Économie', icon: '📈', active: true },
];
const articles = [
  {
    title: 'L’observatoire ouvre une nouvelle fenêtre sur le ciel',
    summary:
      'Dans cette dépêche fictive, une équipe internationale présente un programme d’observation. Les données sont publiques et les résultats restent à examiner.',
    category: 'science',
  },
  {
    title: 'Un réseau ferroviaire repense ses liaisons régionales',
    summary:
      'Un projet fictif relie plusieurs villes. Les acteurs du territoire examinent les propositions et leurs conséquences pour les voyageurs.',
    category: 'monde',
  },
  {
    title: '<img src=x onerror="window.pwned=1"> 日本語 العربية',
    summary: '</script><script>window.pwned=2</script> Texte hostile conservé comme donnée inerte.',
    category: 'tech',
  },
  {
    title: 'Les marchés se préparent à une semaine de publications',
    summary:
      'Les chiffres de cet exercice sont synthétiques. Ils ne constituent ni cotations ni conseils financiers.',
    category: 'economie',
  },
  {
    title: 'La recherche locale explore de nouveaux outils numériques',
    summary:
      'Une initiative fictive examine les usages des logiciels libres et l’accès aux connaissances.',
    category: 'tech',
  },
  {
    title: 'La ville imagine une bibliothèque ouverte sur le quartier',
    summary:
      'Des ateliers publics réunissent habitants et associations pour une consultation de démonstration.',
    category: 'monde',
  },
  {
    title: 'Un débat sur l’énergie replace les usages au centre',
    summary:
      'Les scénarios présentés dans ce jeu de test soulignent la diversité des besoins locaux.',
    category: 'monde',
  },
].map((a, i) => ({
  ...a,
  id: 'article-' + i,
  hash: String(i).repeat(32),
  original_title: a.title,
  link: i === 2 ? 'javascript:window.pwned=3' : 'https://example.invalid/article-' + i,
  source: i % 2 ? 'Le Journal Exemple' : 'Agence Démonstration',
  published_at: '2026-11-06T10:00:00Z',
  published_raw: '',
  observed_at: null,
  observed_raw: '',
  image: i === 0 ? image : fallback,
}));
const event = {
  event_id: eventId,
  title: 'Un projet ferroviaire vu par plusieurs rédactions',
  category: 'monde',
  secondary_categories: [],
  article_count: 2,
  domain_count: 2,
  sources: ['Journal A', 'Journal B'],
  articles: [
    {
      id: 'c1',
      title: 'Le projet et ses prochaines étapes',
      source: 'Journal A',
      link: 'https://one.invalid/a',
      published_at: '2026-11-06T09:00:00Z',
      published_raw: '',
    },
    {
      id: 'c2',
      title: 'Les attentes des voyageurs',
      source: 'Journal B',
      link: 'https://two.invalid/a',
      published_at: '2026-11-06T10:00:00Z',
      published_raw: '',
    },
  ],
  last_updated_at: '2026-11-06T10:00:00Z',
  independent_confirmation: false,
  image: fallback,
};
function json(res, value, status = 200) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(value));
}
const backend = http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://127.0.0.1:18055');
  const path = url.pathname;
  requests.push({ path, method: req.method, cookie: req.headers.cookie || '' });
  if (path === '/__test/reset') {
    expired = false;
    revoked = false;
    json(res, {});
    return;
  }
  if (path === '/__test/expire') {
    expired = true;
    json(res, {});
    return;
  }
  if (path === '/__test/revoke') {
    revoked = true;
    json(res, {});
    return;
  }
  if (path === '/__test/requests') {
    json(res, requests);
    return;
  }
  const authenticated =
    (req.headers.cookie || '').includes('atlas_admin=synthetic-auth') && !expired;
  if (path === '/api/auth/login') {
    res.setHeader('Set-Cookie', 'atlas_admin=synthetic-preauth; HttpOnly; SameSite=Strict; Path=/');
    json(res, { configured: true, csrf_token: 'login-csrf' });
    return;
  }
  if (path === '/admin/login') {
    let body = '';
    for await (const part of req) body += part;
    const values = new URLSearchParams(body);
    if (values.get('csrf_token') !== 'login-csrf') {
      json(res, {}, 403);
      return;
    }
    if (
      values.get('username') !== 'admin-test' ||
      values.get('password') !== 'synthetic-test-password'
    ) {
      json(res, {}, 401);
      return;
    }
    expired = false;
    res.writeHead(303, {
      Location: '/config/yaml',
      'Set-Cookie': 'atlas_admin=synthetic-auth; HttpOnly; SameSite=Strict; Path=/',
    });
    res.end();
    return;
  }
  if (
    path.startsWith('/admin/') ||
    path.startsWith('/api/admin/') ||
    path.startsWith('/config') ||
    path === '/radio-studio'
  ) {
    if (!authenticated) {
      json(res, { status: 'error' }, 401);
      return;
    }
    if (req.method === 'POST' && req.headers['x-csrf-token'] !== 'admin-csrf') {
      json(res, {}, 403);
      return;
    }
    if (path === '/admin/logout') {
      res.writeHead(303, {
        Location: '/admin/login',
        'Set-Cookie': 'atlas_admin=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/',
      });
      res.end();
      return;
    }
    if (path === '/admin/csrf') {
      json(res, { csrf_token: 'admin-csrf' });
      return;
    }
    if (path === '/api/admin/sources') {
      json(res, {
        status: 'ok',
        total: 1,
        sources: [
          {
            id: 'source1',
            name: 'Source de démonstration',
            url: 'https://source.invalid/rss',
            category: 'tech',
            health: 'never_checked',
          },
        ],
      });
      return;
    }
    if (path === '/api/admin/radio') {
      json(res, {
        status: 'ok',
        configured: false,
        enabled: false,
        channels: {},
        metrics: { generated: 0 },
        next_slot: null,
      });
      return;
    }
    res.writeHead(200, {
      'Content-Type': 'text/html',
      'Content-Security-Policy': "default-src 'self'",
    });
    res.end(
      '<!doctype html><html lang="fr"><title>Outil Flask de test</title><h1>Outil historique Flask</h1></html>',
    );
    return;
  }
  const base = {
    status: 'ok',
    observed_at: '2026-11-06T10:00:00Z',
    page: Number(url.searchParams.get('page') || 1),
    per_page: Number(url.searchParams.get('per_page') || 20),
  };
  if (path === '/api/public/site') {
    json(res, {
      ...base,
      name: 'Nova Atlas',
      categories: cats,
      streams: { primary: '/stream/nova', android: '/stream/nova-android' },
    });
    return;
  }
  if (path === '/api/public/articles') {
    if (url.searchParams.get('category') === 'unavailable') {
      json(res, { status: 'unavailable' }, 503);
      return;
    }
    const category = url.searchParams.get('category');
    let rows = category ? articles.filter((a) => a.category === category) : articles;
    rows = rows.map((a) => ({ ...a, image: revoked ? fallback : a.image }));
    json(res, {
      ...base,
      date: day,
      total: rows.length,
      articles: rows.slice((base.page - 1) * base.per_page, base.page * base.per_page),
    });
    return;
  }
  if (path === '/api/public/events') {
    json(res, { ...base, total: 1, events: [event], index_available: true });
    return;
  }
  if (path === '/api/public/events/' + eventId) {
    json(res, { ...base, event });
    return;
  }
  if (path === '/api/public/archives') {
    json(res, {
      ...base,
      total: 1,
      days: [{ date: day, articles: true, report: true, editions: ['matin', 'midi', 'soir'] }],
    });
    return;
  }
  if (path.startsWith('/api/public/documents/')) {
    json(res, {
      ...base,
      date: day,
      kind: 'report',
      title: 'Le point sur la journée',
      nodes: [
        { tag: 'p', children: [{ text: 'Un rapport synthétique, sans données privées.' }] },
        { tag: 'strong', children: [{ text: 'Les sources restent consultables.' }] },
        { tag: 'p', children: [{ text: '<script>window.pwned=4</script>' }] },
      ],
    });
    return;
  }
  if (path.startsWith('/media/images/') && !revoked) {
    res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'no-store' });
    res.end(fs.readFileSync(root + '/tests/fixture.jpg'));
    return;
  }
  if (path === '/assets/news-fallback.svg' || path.startsWith('/media/images/')) {
    res.writeHead(200, { 'Content-Type': 'image/svg+xml', 'Cache-Control': 'no-store' });
    res.end(
      '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><rect width="320" height="180" fill="#e7e9df"/><circle cx="160" cy="90" r="40" fill="none" stroke="#176661" stroke-width="3"/></svg>',
    );
    return;
  }
  if (path.startsWith('/stream/')) {
    res.writeHead(502);
    res.end();
    return;
  }
  json(res, { status: 'error' }, 404);
});
backend.listen(18055, '127.0.0.1');
const node = spawn(process.execPath, ['server.mjs'], {
  cwd: root,
  env: {
    ...process.env,
    HOST: '127.0.0.1',
    PORT: '4174',
    FLASK_ORIGIN: 'http://127.0.0.1:18055',
    ATLAS_PUBLIC_SCHEME: 'http',
  },
  stdio: 'inherit',
});
const proxy = http.createServer((req, res) => {
  const flask =
    /^\/(api\/|admin\/|assets\/|media\/|stream\/|config(?:\/|$)|radio-studio(?:\/|$))/.test(
      req.url,
    );
  const request = http.request(
    {
      hostname: '127.0.0.1',
      port: flask ? 18055 : 4174,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, 'x-forwarded-proto': 'http' },
    },
    (response) => {
      res.writeHead(response.statusCode, response.headers);
      response.pipe(res);
    },
  );
  request.on('error', () => {
    res.writeHead(503);
    res.end('Service unavailable');
  });
  req.pipe(request);
});
proxy.listen(4173, '127.0.0.1');
function stop() {
  node.kill('SIGTERM');
  backend.close();
  proxy.close();
  setTimeout(() => process.exit(), 300).unref();
}
process.on('SIGTERM', stop);
process.on('SIGINT', stop);
