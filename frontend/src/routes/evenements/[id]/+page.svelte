<script lang="ts">
  import { messages, dateLabel } from '@lib/i18n';
  import { safeLink } from '@lib/security';
  import Illustration from '@lib/components/Illustration.svelte';
  import Notice from '@lib/components/Notice.svelte';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
  let event = $derived(data.event.data?.event);
</script>

<svelte:head><title>{event?.title || t.events} — Nova Atlas</title></svelte:head><Notice
  state={data.event.state}
  locale={data.locale}
/>{#if event}<article class="event-detail">
    <p class="eyebrow">{event.category} / {t.events}</p>
    <h1>{event.title}</h1>
    <p>
      {event.article_count}
      {t.articles.toLowerCase()} · {event.domain_count}
      {t.sources.toLowerCase()} · {t.updated} : {dateLabel(event.last_updated_at, data.locale)}
    </p>
    <p class="notice">{t.noConfirmation}</p>
    {#if !event.image.fallback}<Illustration image={event.image} />{/if}
    <div class="section-heading"><h2>{t.coverage}</h2></div>
    <div class="coverages">
      {#each event.articles as article}<section class="coverage">
          <p class="eyebrow">{article.source}</p>
          <h2>{article.title}</h2>
          <p>{dateLabel(article.published_at || article.published_raw, data.locale)}</p>
          {#if safeLink(article.link)}<a
              href={safeLink(article.link)}
              rel="noopener noreferrer"
              target="_blank">{t.origin} ↗</a
            >{/if}
        </section>{/each}
    </div>
  </article>{/if}
