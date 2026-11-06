import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
import type { Articles } from '@lib/types';
import { pageNumber } from '@lib/security';
export const load: PageServerLoad = async ({ url }) => {
  const category = (url.searchParams.get('category') || '').slice(0, 100);
  const page = pageNumber(url.searchParams.get('page'));
  return {
    category,
    articles: await api<Articles>(
      '/api/public/articles?per_page=20&page=' +
        page +
        (category ? '&category=' + encodeURIComponent(category) : ''),
    ),
  };
};
