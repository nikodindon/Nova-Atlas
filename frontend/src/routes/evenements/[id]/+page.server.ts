import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
import type { EditorialEvent } from '@lib/types';
export const load: PageServerLoad = async ({ params }) => ({
  event: await api<{ event: EditorialEvent }>(
    '/api/public/events/' + encodeURIComponent(params.id),
  ),
});
