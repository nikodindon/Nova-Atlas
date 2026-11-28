<script lang="ts">
  import { messages } from '@lib/i18n';
  import { safeLink } from '@lib/security';
  import type { Article, Locale } from '@lib/types';

  let { articles, locale }: { articles: Article[]; locale: Locale } = $props();
  let t = $derived(messages(locale));
</script>

{#each articles as article (article.id)}
  {@const edition = locale === 'en' ? article.english_edition : undefined}
  <details class="headline-teaser">
    <summary>{edition?.title ?? article.title}</summary>
    <p>{edition?.summary ?? article.summary}</p>
    {#if safeLink(article.link)}
      <a href={safeLink(article.link)} target="_blank" rel="noopener noreferrer">{t.origin} ↗</a>
    {/if}
  </details>
{/each}

<style>
  .headline-teaser {
    border-bottom: 1px solid var(--line);
    padding-bottom: 0.8rem;
  }

  .headline-teaser summary {
    cursor: pointer;
    font-size: 1.1rem;
    font-weight: 650;
    line-height: 1.35;
  }

  .headline-teaser p {
    color: var(--muted);
    margin: 0.6rem 0;
  }
</style>
