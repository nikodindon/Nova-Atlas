import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
import type { Archives } from '@lib/types';
import { pageNumber } from '@lib/security';
export const load: PageServerLoad = async ({ url }) => ({
  archives: await api<Archives>(
    '/api/public/archives?page=' + pageNumber(url.searchParams.get('page')),
  ),
});
