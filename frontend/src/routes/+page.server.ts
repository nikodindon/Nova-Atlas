import { api } from '@lib/server/api';
import type { Articles, Events, Archives } from '@lib/types';
const editorialDay = new Intl.DateTimeFormat('sv-SE', {
  year: 'numeric', month: '2-digit', day: '2-digit', timeZone: 'Europe/Paris',
});

export const load = async () => {
  const [articles, events, archives] = await Promise.all([
    api<Articles>('/api/public/articles?per_page=13'),
    api<Events>('/api/public/events?per_page=3'),
    api<Archives>('/api/public/archives?per_page=3'),
  ]);
  const headlines = await api<Pick<Articles, 'articles'>>('/api/public/headlines');
  return {
    articles, events, archives, headlines,
    today: editorialDay.format(new Date()).replaceAll('-', ''),
  };
};
