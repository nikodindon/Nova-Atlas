import type { Resource } from '@lib/types';
export class ApiError extends Error {
  constructor(public kind: 'auth' | 'unavailable' | 'invalid') {
    super(kind);
  }
}
const cache = new Map<string, { data: unknown; at: number }>();
let active = 0;
export function backendOrigin(value = process.env.FLASK_ORIGIN): string {
  if (!value) throw new ApiError('invalid');
  const url = new URL(value);
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.pathname !== '/' ||
    url.search ||
    url.hash
  )
    throw new ApiError('invalid');
  return url.origin;
}
function allowed(path: string, privateRequest: boolean): boolean {
  const pathname = new URL(path, 'http://fixed.invalid').pathname;
  return privateRequest
    ? ['/admin/csrf', '/api/admin/sources', '/api/admin/radio'].includes(pathname)
    : /^\/api\/public\/(site|articles|archives|events(?:\/[\w-]+)?|documents\/\d{8}\/(report|matin|midi|soir))$/.test(
        pathname,
      );
}
export async function api<T>(
  path: string,
  options: { cookie?: string; privateRequest?: boolean } = {},
): Promise<Resource<T>> {
  const privateRequest = options.privateRequest === true;
  if (!allowed(path, privateRequest) || path.startsWith('//')) throw new ApiError('invalid');
  const old = !privateRequest ? cache.get(path) : undefined;
  const now = Date.now();
  if (old && now - old.at < 15000)
    return { data: old.data as T, state: 'available', fetchedAt: new Date(old.at).toISOString() };
  let acquired = false;
  try {
    if (active >= 4) throw new ApiError('unavailable');
    active++;
    acquired = true;
    const target = new URL(path, backendOrigin());
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (privateRequest) {
      const cookie = (options.cookie || '')
        .split(';')
        .find((part) => part.trim().startsWith('atlas_admin='));
      if (cookie && cookie.length <= 4096) headers.Cookie = cookie.trim();
      headers['X-Forwarded-Proto'] = process.env.ATLAS_PUBLIC_SCHEME === 'https' ? 'https' : 'http';
    }
    const response = await fetch(target, {
      headers,
      redirect: 'error',
      signal: AbortSignal.timeout(3000),
    });
    if ([401, 403].includes(response.status)) throw new ApiError('auth');
    if (!response.ok || !response.headers.get('content-type')?.includes('application/json'))
      throw new ApiError('unavailable');
    const reader = response.body?.getReader();
    if (!reader) throw new ApiError('unavailable');
    let length = 0;
    const chunks: Uint8Array[] = [];
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.length;
      if (length > 1024 * 1024) {
        await reader.cancel();
        throw new ApiError('unavailable');
      }
      chunks.push(value);
    }
    const data: unknown = JSON.parse(Buffer.concat(chunks).toString());
    if (typeof data !== 'object' || data === null || Array.isArray(data))
      throw new ApiError('unavailable');
    if (!privateRequest) {
      cache.delete(path);
      if (cache.size >= 16) cache.delete(cache.keys().next().value!);
      cache.set(path, { data, at: now });
    }
    return { data: data as T, state: 'available', fetchedAt: new Date(now).toISOString() };
  } catch (error) {
    if (privateRequest && error instanceof ApiError && error.kind === 'auth') throw error;
    if (old && now - old.at < 60000)
      return { data: old.data as T, state: 'stale', fetchedAt: new Date(old.at).toISOString() };
    return { data: null, state: 'unavailable', fetchedAt: new Date(now).toISOString() };
  } finally {
    if (acquired) active--;
  }
}
