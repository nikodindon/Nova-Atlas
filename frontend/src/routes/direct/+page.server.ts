import type { PageServerLoad } from './$types';
import { api } from '@lib/server/api';
import type { Archives, Articles, Document, RichNode } from '@lib/types';
import { pageNumber } from '@lib/security';
const parisClock = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Europe/Paris', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', hourCycle: 'h23',
});
const editions = ['soir', 'midi', 'matin'] as const;
const opensAt = { matin: 6, midi: 12, soir: 19 };

function text(node: RichNode): string {
  return node.text ?? (node.children ?? []).map(text).join('');
}

async function publication(day: string, kind: (typeof editions)[number] | 'report') {
  if (!/^\d{8}$/.test(day)) return null;
  const document = await api<Document>('/api/public/documents/' + day + '/' + kind);
  const paragraph = document.data?.nodes
    .filter((node) => node.tag === 'p' && !node.children?.every((child) => child.tag === 'em'))
    .map((node) => text(node).replace(/\s+/g, ' ').trim())
    .find((value) => value.length > 25 && !/g[ée]n[ée]r[ée] à|articles analys[ée]s/i.test(value));
  const excerpt = paragraph && paragraph.length > 190
    ? paragraph.slice(0, 190).replace(/\s+\S*$/, '') + '…'
    : (paragraph ?? '');
  return { day, kind, excerpt, href: kind === 'report' ? '/rapports/' + day : '/editions-v2/' + day + '/' + kind };
}

export const load: PageServerLoad = async ({ url, depends }) => {
  depends('atlas:live-feed');
  const selectedCategories = [...new Set(url.searchParams.getAll('category')
    .filter((value) => value.length > 0 && value.length <= 100))].slice(0, 32);
  const page = pageNumber(url.searchParams.get('page'));
  const params = new URLSearchParams({ per_page: '20', page: String(page) });
  for (const category of selectedCategories) params.append('category', category);
  if (!selectedCategories.length) params.set('order', 'varied');
  const [articles, archives] = await Promise.all([
    api<Articles>('/api/public/articles?' + params),
    api<Archives>('/api/public/archives?per_page=50'),
  ]);
  const clock = Object.fromEntries(parisClock.formatToParts(new Date()).map((part) => [part.type, part.value]));
  const today = clock.year + clock.month + clock.day;
  const days = (archives.data?.days ?? []).filter((day) => day.date <= today);
  const edition = days.flatMap((day) =>
    editions.filter((kind) => day.editions.includes(kind)
      && (day.date !== today || Number(clock.hour) >= opensAt[kind]))
      .map((kind) => ({ day: day.date, kind })),
  )[0];
  const report = archives.data?.latest_report ?? days.find((day) => day.report)?.date;
  const [currentEdition, dailyReport] = await Promise.all([
    edition ? publication(edition.day, edition.kind) : null,
    report ? publication(report, 'report') : null,
  ]);
  return { selectedCategories, articles, currentEdition, dailyReport, archiveState: archives.state };
};
