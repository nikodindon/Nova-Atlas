import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
interface Sources {
  total: number;
  sources: {
    id: string;
    name: string;
    url: string;
    category: string;
    health: string;
    observed_at?: number | null;
    observation_age_seconds?: number | null;
    stale?: boolean;
  }[];
}
export const load: PageServerLoad = async ({ request }) => ({
  sources: await api<Sources>('/api/admin/sources?per_page=50', {
    privateRequest: true,
    cookie: request.headers.get('cookie') || '',
  }),
});
