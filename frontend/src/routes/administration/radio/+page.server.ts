import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
interface Radio {
  configured: boolean;
  enabled: boolean;
  channels: Record<string, unknown>;
  metrics: Record<string, number | null>;
  next_slot: { slot: string } | null;
}
export const load: PageServerLoad = async ({ request }) => ({
  radio: await api<Radio>('/api/admin/radio', {
    privateRequest: true,
    cookie: request.headers.get('cookie') || '',
  }),
});
