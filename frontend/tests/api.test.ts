import { beforeEach, afterEach, expect, test, vi } from 'vitest';
beforeEach(() => {
  vi.resetModules();
  vi.stubEnv('FLASK_ORIGIN', 'http://fixed.invalid');
  vi.stubEnv('ATLAS_PUBLIC_SCHEME', 'https');
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});
function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
test('fixed server destination, no URL credentials or arbitrary proxy', async () => {
  const { api, backendOrigin } = await import('../src/lib/server/api');
  expect(() => backendOrigin('https://user:secret@internal/')).toThrow();
  expect(() => backendOrigin('http://internal/path')).toThrow();
  await expect(api('https://evil.invalid/api')).rejects.toThrow();
  await expect(api('//evil.invalid/api/public/site')).rejects.toThrow();
});
test('public reads strip cookies and reuse a bounded cache', async () => {
  const fetcher = vi.fn().mockResolvedValue(json({ articles: [] }));
  vi.stubGlobal('fetch', fetcher);
  const { api } = await import('../src/lib/server/api');
  await api('/api/public/articles', { cookie: 'atlas_admin=private' });
  await api('/api/public/articles');
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(String(fetcher.mock.calls[0][0])).toBe('http://fixed.invalid/api/public/articles');
  expect(fetcher.mock.calls[0][1].headers.Cookie).toBeUndefined();
  expect(fetcher.mock.calls[0][1].redirect).toBe('error');
});
test('private responses never cached, only Flask cookie forwarded', async () => {
  const fetcher = vi.fn().mockImplementation(() => json({ sources: [] }));
  vi.stubGlobal('fetch', fetcher);
  const { api } = await import('../src/lib/server/api');
  for (let i = 0; i < 2; i++)
    await api('/api/admin/sources', {
      privateRequest: true,
      cookie: 'other=secret; atlas_admin=session; atlas_theme=dark',
    });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(fetcher.mock.calls[0][1].headers).toEqual({
    Accept: 'application/json',
    Cookie: 'atlas_admin=session',
    'X-Forwarded-Proto': 'https',
  });
});
test.each([401, 403])('private HTTP %d remains authorization failure', async (status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({}, status)));
  const { api } = await import('../src/lib/server/api');
  await expect(api('/admin/csrf', { privateRequest: true })).rejects.toMatchObject({
    kind: 'auth',
  });
});
test.each([
  () => json([], 200),
  () => new Response('bad'),
  () => json({}, 503),
  () => {
    throw Error('private provider detail');
  },
])('errors use unavailable state without upstream details', async (response) => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation(response));
  const { api } = await import('../src/lib/server/api');
  expect(await api('/api/public/site')).toMatchObject({ data: null, state: 'unavailable' });
});
test('oversized JSON response rejected', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(() => json({ data: 'a'.repeat(1024 * 1024) })),
  );
  const { api } = await import('../src/lib/server/api');
  expect((await api('/api/public/site')).state).toBe('unavailable');
});
test('stale fallback expires after sixty seconds and never for private data', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn().mockImplementation(() => json({ name: 'Fixture' }));
  vi.stubGlobal('fetch', fetcher);
  const { api } = await import('../src/lib/server/api');
  await api('/api/public/site');
  fetcher.mockRejectedValue(Error('offline'));
  vi.advanceTimersByTime(16000);
  expect((await api('/api/public/site')).state).toBe('stale');
  vi.advanceTimersByTime(60000);
  expect((await api('/api/public/site')).state).toBe('unavailable');
});

test('pinned framework announcer has an exact CSP style hash', async () => {
  const { readFileSync } = await import('node:fs');
  const { createHash } = await import('node:crypto');
  const root = readFileSync(
    'node_modules/@sveltejs/kit/src/runtime/components/root.svelte',
    'utf8',
  );
  const style = root.match(/style="(position: absolute;[^"]+)"/)?.[1];
  expect(style).toBeTruthy();
  const hash = createHash('sha256').update(style!).digest('base64');
  expect(readFileSync('vite.config.ts', 'utf8')).toContain('sha256-' + hash);
});
