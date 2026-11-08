import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
import type { Articles } from '@lib/types';
import { pageNumber } from '@lib/security';
export const load: PageServerLoad = async ({ params, url }) => ({
  category: params.id.slice(0, 100),
  articles: await api<Articles>(
    '/api/public/articles?category=' +
      encodeURIComponent(params.id.slice(0, 100)) +
      '&page=' +
      pageNumber(url.searchParams.get('page')),
  ),
});
