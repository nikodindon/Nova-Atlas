<script lang="ts">
  import type { EditorialEvent, Locale } from '@lib/types';
  import { messages, dateLabel } from '@lib/i18n';
  let { event, locale }: { event: EditorialEvent; locale: Locale } = $props();
  let t = $derived(messages(locale));
</script>

<article class="event-card">
  <p class="eyebrow">{event.category} · {event.domain_count} {t.sources.toLowerCase()}</p>
  <h2><a href={'/evenements/' + encodeURIComponent(event.event_id)}>{event.title}</a></h2>
  <p>
    {event.article_count}
    {t.articles.toLowerCase()} · {dateLabel(event.last_updated_at, locale)}
  </p>
  <span class="event-arrow" aria-hidden="true">↗</span>
</article>
