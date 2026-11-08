import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
import type { Events } from '@lib/types';
import { pageNumber } from '@lib/security';
export const load: PageServerLoad = async ({ url }) => ({
  events: await api<Events>('/api/public/events?page=' + pageNumber(url.searchParams.get('page'))),
});
