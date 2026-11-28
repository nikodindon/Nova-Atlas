import { api } from '@lib/server/api';
import type { Site } from '@lib/types';
export const prerender = false;
export const load = async ({ locals, url }: { locals: App.Locals; url: URL }) => {
  const hosts = (process.env.ATLAS_PERSONAL_MUSIC_HOSTS || '').split(',').map((host) => host.trim().toLowerCase()).filter(Boolean);
  const site = await api<Site>('/api/public/site');
  return {
    locale: locals.locale,
    theme: locals.theme,
    site,
    personalMusic: site.data?.personal_music === true && (!hosts.length || hosts.includes(url.host.toLowerCase())),
  };
};
