export function safeLink(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 4096 || /[\s\\\x00-\x1f\x7f]/u.test(value))
    return null;
  try {
    const url = new URL(value);
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export function localImage(value: unknown): string {
  return typeof value === 'string' &&
    /^(\/assets\/news-fallback\.svg|\/media\/images\/[a-f0-9]{64}\/(320|768)\.jpg)$/.test(value)
    ? value
    : '/assets/news-fallback.svg';
}
export function streamPath(value: unknown): string | null {
  return typeof value === 'string' && /^\/stream\/[\w-]{1,64}$/.test(value) ? value : null;
}
export function pageNumber(value: string | null): number {
  const n = Number(value || '1');
  return Number.isInteger(n) && n > 0 && n <= 10000 ? n : 1;
}
