import { api } from '@lib/server/api';
import type { Site } from '@lib/types';
import type { LayoutServerLoad } from './$types';

export const prerender = false;

function hosts(value: string | undefined) {
  return (value || '').split(',').map((host) => host.trim().toLowerCase()).filter(Boolean);
}

export const load: LayoutServerLoad = async ({ locals, url, cookies, request }) => {
  const local = hosts(process.env.ATLAS_PERSONAL_MUSIC_HOSTS);
  const restricted = hosts(process.env.ATLAS_PERSONAL_MUSIC_ADMIN_HOSTS);
  const host = url.host.toLowerCase();
  const site = await api<Site>('/api/public/site');
  let personalMusic = site.data?.personal_music === true &&
    (local.length === 0 || local.includes(host) || restricted.includes(host));
  if (personalMusic && restricted.includes(host)) {
    personalMusic = false;
    if (cookies.get('atlas_admin')) {
      try {
        const session = await api<{ csrf_token: string }>('/admin/csrf', {
          privateRequest: true, cookie: request.headers.get('cookie') || '',
        });
        personalMusic = !!session.data;
      } catch {
        // Public pages and the shared stream remain available after session expiry.
      }
    }
  }
  return { locale: locals.locale, theme: locals.theme, site, personalMusic };
};
