<script lang="ts">
  import type { Article, Locale } from '@lib/types';
  import { messages, dateLabel } from '@lib/i18n';
  import { safeLink } from '@lib/security';
  import Illustration from './Illustration.svelte';
  let {
    article,
    locale,
    lead = false,
  }: { article: Article; locale: Locale; lead?: boolean } = $props();
  let t = $derived(messages(locale));
</script>

<article class:lead class="article-card" data-category={article.category}>
  {#if lead || !article.image.fallback}<Illustration image={article.image} eager={lead} />{/if}
  <div class="card-body">
    <p class="eyebrow">{article.category} <span>· {article.source}</span></p>
    <h2>{article.title}</h2>
    <p class="summary">{article.summary}</p>
    <div class="article-meta">
      <time
        datetime={article.published_at || undefined}
        title={article.published_at ? t.publication : t.collected}
        >{dateLabel(
          article.published_at ||
            article.observed_at ||
            article.published_raw ||
            article.observed_raw,
          locale,
        )}</time
      >{#if safeLink(article.link)}<a
          href={safeLink(article.link)}
          rel="noopener noreferrer"
          target="_blank">{t.origin} ↗</a
        >{/if}
    </div>
  </div>
</article>
