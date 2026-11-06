import { api } from '@lib/server/api';
import type { Articles, Events, Archives } from '@lib/types';
export const load = async () => {
  const [articles, events, archives] = await Promise.all([
    api<Articles>('/api/public/articles?per_page=7'),
    api<Events>('/api/public/events?per_page=3'),
    api<Archives>('/api/public/archives?per_page=3'),
  ]);
  return { articles, events, archives };
};
