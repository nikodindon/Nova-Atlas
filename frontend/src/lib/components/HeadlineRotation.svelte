<script lang="ts">
  import ArticleCard from './ArticleCard.svelte';
  import { messages } from '@lib/i18n';
  import type { Article, Locale } from '@lib/types';

  let { articles, locale }: { articles: Article[]; locale: Locale } = $props();
  let t = $derived(messages(locale));
  let current = $state(0);
  let paused = $state(false);
  let interacting = $state(false);
  let rotationAllowed = $state(false);
  let active = $derived(current < articles.length ? current : 0);

  $effect(() => {
    const media = window.matchMedia('(min-width: 768px) and (pointer: fine) and (prefers-reduced-motion: no-preference)');
    const update = () => (rotationAllowed = media.matches);
    update();
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  });

  $effect(() => {
    if (!rotationAllowed || paused || interacting || articles.length < 2) return;
    const timer = window.setInterval(() => {
      if (!document.hidden) current = (current + 1) % articles.length;
    }, 20000);
    return () => window.clearInterval(timer);
  });
</script>

<section
  aria-label={t.breaking}
  onfocusin={() => (interacting = true)}
  onfocusout={(event) => {
    if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) {
      interacting = false;
    }
  }}
>
  <div class="section-heading">
    <h2>{t.breaking}</h2>
    {#if rotationAllowed && articles.length > 1}
      <button type="button" onclick={() => (paused = !paused)}>{paused ? t.resumeHeadlines : t.pauseHeadlines}</button>
    {/if}
  </div>
  <div class="headlines">
    {#key articles[active].id}<ArticleCard article={articles[active]} {locale} lead />{/key}
    {#if articles.length > 1}
      <div class="headlines-side">
        {#each articles as article, index (article.id)}
          <button class="headline-option" type="button" aria-pressed={index === active}
            onclick={() => { current = index; paused = true; }}>
            {locale === 'en' ? article.english_edition?.title ?? article.title : article.title}
          </button>
        {/each}
      </div>
    {/if}
  </div>
</section>

<style>
  .headline-option {
    width: 100%;
    padding: 1rem;
    text-align: start;
    font: inherit;
    font-weight: 650;
    white-space: normal;
  }
</style>
