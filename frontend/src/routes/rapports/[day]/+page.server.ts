import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
import type { Document } from '@lib/types';
import { error } from '@sveltejs/kit';
export const load: PageServerLoad = async ({ params }) => {
  if (!/^\d{8}$/.test(params.day)) error(404);
  return { document: await api<Document>('/api/public/documents/' + params.day + '/report') };
};
