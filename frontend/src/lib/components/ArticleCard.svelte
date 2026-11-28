<script lang="ts">
  import type { Article, Category, Locale } from '@lib/types';
  import { messages, dateLabel } from '@lib/i18n';
  import { safeLink } from '@lib/security';
  import { qualificationImages } from '@lib/client';
  import type { Illustration as Image } from '@lib/types';
  import Illustration from './Illustration.svelte';
  import genericCover from '@lib/assets/editorial-globe.webp';
  let {
    article,
    locale,
    lead = false,
    compact = false,
    category,
  }: { article: Article; locale: Locale; lead?: boolean; compact?: boolean; category?: Category } = $props();
  let t = $derived(messages(locale));
  let edition = $derived(locale === 'en' ? article.english_edition : undefined);
  let summary = $derived(edition?.summary ?? article.summary);
  let qualification = $state<Image>();
  let image = $derived(qualification ?? article.image);
  $effect(() => {
    const { id, image } = article;
    qualification = undefined;
    if (!image.fallback) return;
    let active = true;
    void qualificationImages().then((images) => {
      if (active) qualification = images[id];
    });
    return () => { active = false; };
  });
  let expanded = $state(false);
  const summaryId = $props.id();
  let preview = $derived(
    summary.length > 180
      ? summary.slice(0, 180).replace(/\s+\S*$/, '').trimEnd() + '…'
      : summary,
  );
</script>

<article class:lead class="article-card" data-category={article.category}>
  {#if !image.fallback}
    <Illustration {image} eager={lead} />
  {:else if lead}
    <figure class="editorial-cover">
      <img src={genericCover} alt="" width="960" height="540" fetchpriority="high" decoding="async" />
      <figcaption>{t.genericEditorialCover}</figcaption>
    </figure>
  {/if}
  <div class="card-body">
    <p class="eyebrow">
      {#if category?.icon}<span aria-hidden="true">{category.icon}</span> {/if}
      {category?.name ?? article.category} <span>· {article.source}</span>
    </p>
    <h2>{edition?.title ?? article.title}</h2>
    {#if locale === 'en' && !edition}<p class="article-meta">{t.articleTranslationUnavailable}</p>{/if}
    <p class="summary" id={summaryId}>{compact && !expanded ? preview : summary}</p>
    {#if compact && preview !== summary}
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
  .editorial-cover img {
    display: block;
    max-height: 240px;
    object-fit: cover;
  }

  .editorial-cover figcaption {
    color: var(--muted);
    font-size: 0.75rem;
    margin-top: 0.25rem;
  }


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
