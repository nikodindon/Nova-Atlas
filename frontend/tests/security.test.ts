import { describe, it, expect } from 'vitest';
import { safeLink, localImage, streamPath, pageNumber } from '../src/lib/security';
import { messages, dateLabel } from '../src/lib/i18n';
describe('untrusted content', () => {
  it.each([
    'javascript:alert(1)',
    'data:text/html,x',
    'https://u:secret@x.test/a',
    'https://x.test/\nno',
    '//evil.test',
    'https://x.test\\evil',
  ])('rejects unsafe link %s', (value) => expect(safeLink(value)).toBeNull());
  it('preserves Unicode HTTPS links', () =>
    expect(safeLink('https://example.test/日本語')).toContain('https://example.test/'));
  it.each([
    'https://external.test/x.png',
    'data:image/png,x',
    '/media/images/../../secret',
    '//evil.test',
  ])('uses local fallback for %s', (value) =>
    expect(localImage(value)).toBe('/assets/news-fallback.svg'),
  );
  it('allows only approved local image identifiers', () =>
    expect(localImage('/media/images/' + 'a'.repeat(64) + '/320.jpg')).toContain('a'.repeat(64)));
  it('restricts audio origins', () => {
    expect(streamPath('https://evil.test/stream/nova')).toBeNull();
    expect(streamPath('/stream/nova')).toBe('/stream/nova');
  });
  it('bounds pagination', () => {
    expect(pageNumber('-2')).toBe(1);
    expect(pageNumber('10001')).toBe(1);
    expect(pageNumber('2')).toBe(2);
  });
  it('has complete English UI keys and preserves ambiguous dates', () => {
    expect(Object.keys(messages('en'))).toEqual(Object.keys(messages('fr')));
    expect(dateLabel('2026-11-06 12:00', 'fr')).toBe('2026-11-06 12:00');
  });
});
