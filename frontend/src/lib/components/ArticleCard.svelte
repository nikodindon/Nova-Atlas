<script lang="ts">
  import type { Article, Locale } from '@lib/types';
  import { messages, dateLabel } from '@lib/i18n';
  import { safeLink } from '@lib/security';
  import Illustration from './Illustration.svelte';
  let {
    article,
    locale,
    lead = false,
    compact = false,
  }: { article: Article; locale: Locale; lead?: boolean; compact?: boolean } = $props();
  let t = $derived(messages(locale));
  let expanded = $state(false);
  const summaryId = $props.id();
  let preview = $derived(
    article.summary.length > 180
      ? article.summary.slice(0, 180).replace(/\s+\S*$/, '').trimEnd() + '…'
      : article.summary,
  );
</script>

<article class:lead class="article-card" data-category={article.category}>
  {#if lead || !article.image.fallback}<Illustration image={article.image} eager={lead} />{/if}
  <div class="card-body">
    <p class="eyebrow">{article.category} <span>· {article.source}</span></p>
    <h2>{article.title}</h2>
    <p class="summary" id={summaryId}>{compact && !expanded ? preview : article.summary}</p>
    {#if compact && preview !== article.summary}
      <button
        type="button"
        class="summary-toggle"
        aria-expanded={expanded}
        aria-controls={summaryId}
        onclick={() => (expanded = !expanded)}
      >{expanded ? t.collapseSummary : t.expandSummary}</button>
    {/if}
    <div class="article-meta">
      {#if article.published_at || article.published_raw}
        <span>{t.sourcePublished} <time datetime={article.published_at || undefined}>{dateLabel(article.published_at || article.published_raw, locale)}</time></span>
      {/if}
      {#if article.observed_at || article.observed_raw}
        <span>{t.atlasProcessed} <time datetime={article.observed_at || undefined}>{dateLabel(article.observed_at || article.observed_raw, locale)}</time></span>
      {/if}
      {#if safeLink(article.link)}<a
          href={safeLink(article.link)}
          rel="noopener noreferrer"
          target="_blank">{t.origin} ↗</a
        >{/if}
    </div>
  </div>
</article>

<style>
  .summary-toggle {
    padding: 0.25rem 0;
    border: 0;
    background: transparent;
    color: inherit;
    text-decoration: underline;
    text-underline-offset: 0.2em;
    cursor: pointer;
  }
</style>
