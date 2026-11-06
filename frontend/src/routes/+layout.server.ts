import { api } from '@lib/server/api';
import type { Site } from '@lib/types';
export const prerender = false;
export const load = async ({ locals }: { locals: App.Locals }) => ({
  locale: locals.locale,
  theme: locals.theme,
  site: await api<Site>('/api/public/site'),
});
