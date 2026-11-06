import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
import type { Articles } from '@lib/types';
import { error } from '@sveltejs/kit';
import { pageNumber } from '@lib/security';
export const load: PageServerLoad = async ({ params, url }) => {
  if (!/^\d{8}$/.test(params.day)) error(404);
  return {
    day: params.day,
    articles: await api<Articles>(
      '/api/public/articles?date=' +
        params.day +
        '&page=' +
        pageNumber(url.searchParams.get('page')),
    ),
  };
};
