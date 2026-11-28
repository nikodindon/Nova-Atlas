import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
import type { Articles } from '@lib/types';
import { pageNumber } from '@lib/security';
export const load: PageServerLoad = async ({ url, depends }) => {
  depends('atlas:live-feed');
  const selectedCategories = [...new Set(url.searchParams.getAll('category')
    .filter((value) => value.length > 0 && value.length <= 100))].slice(0, 32);
  const page = pageNumber(url.searchParams.get('page'));
  const params = new URLSearchParams({ per_page: '20', page: String(page) });
  for (const category of selectedCategories) params.append('category', category);
  if (!selectedCategories.length) params.set('order', 'varied');
  return { selectedCategories, articles: await api<Articles>('/api/public/articles?' + params) };
};
