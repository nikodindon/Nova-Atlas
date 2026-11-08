import { redirect } from '@sveltejs/kit';
import type { Handle, HandleServerError } from '@sveltejs/kit/hooks';
import { api, ApiError } from '@lib/server/api';

export const handle: Handle = async ({ event, resolve }) => {
  event.locals.locale = event.cookies.get('atlas_language') === 'en' ? 'en' : 'fr';
  event.locals.theme = event.cookies.get('atlas_theme') === 'dark' ? 'dark' : 'light';
  if (event.url.pathname.startsWith('/administration')) {
    try {
      const session = await api<{ csrf_token: string }>('/admin/csrf', {
        privateRequest: true,
        cookie: event.request.headers.get('cookie') || '',
      });
      if (!session.data)
        return new Response('Administration momentanément indisponible', {
          status: 503,
          headers: { 'Cache-Control': 'no-store' },
        });
    } catch (error) {
      if (error instanceof ApiError && error.kind === 'auth') redirect(303, '/connexion');
      throw error;
    }
  }
  const response = await resolve(event, {
    transformPageChunk: ({ html }) =>
      html.replace(
        'lang="fr" data-theme="light"',
        `lang="${event.locals.locale}" data-theme="${event.locals.theme}"`,
      ),
  });
  response.headers.set('Cache-Control', 'private, no-store'); // Dynamic CSP nonce, never shared HTML caching.
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set('Referrer-Policy', 'same-origin');
  response.headers.set(
    'Permissions-Policy',
    'camera=(), microphone=(), geolocation=(), payment=()',
  );
  response.headers.append('Vary', 'Cookie');
  return response;
};
export const handleError: HandleServerError = () => ({
  message: 'Le service est momentanément indisponible.',
});
