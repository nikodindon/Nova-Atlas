<script lang="ts">
  import ArticleCard from './ArticleCard.svelte';
  import Notice from './Notice.svelte';
  import Pagination from './Pagination.svelte';
  import type { Snippet } from 'svelte';
  import { messages } from '@lib/i18n';
  import { invalidate, invalidateAll, goto } from '$app/navigation';
  import type { Articles, Category, Locale, Resource } from '@lib/types';
  let {
    resource,
    locale,
    categories,
    selectedCategories = [],
    path = '/direct',
    title,
    sidebar,
  }: {
    resource: Resource<Articles>;
    locale: Locale;
    categories: Category[];
    selectedCategories?: string[];
    path?: string;
    title: string;
    sidebar?: Snippet;
  } = $props();
  let t = $derived(messages(locale));
  let pendingCategories = $state<string[] | null>(null);
  let activeCategories = $derived(pendingCategories ?? selectedCategories);
  let choices = $derived.by(() => {
    const available = new Set(resource.data?.available_categories ?? []);
    const visible = categories.filter((item) => item.active || available.has(item.id)
      || activeCategories.includes(item.id));
    for (const id of [...available, ...activeCategories]) {
      if (!visible.some((item) => item.id === id)) visible.push({ id, name: id, icon: '🌐', active: false });
    }
    return visible;
  });
  let navigation = 0;

  function filterPath(selected: string[]) {
    if (path !== '/direct' || !selected.length) return path;
    return path + '?' + selected.map((category) => 'category=' + encodeURIComponent(category)).join('&');
  }

  async function setCategories(categories: string[]) {
    pendingCategories = categories;
    const request = ++navigation;
    try {
      await goto(filterPath(categories), { reset: false });
    } finally {
      if (navigation === request) pendingCategories = null;
    }
  }

  function toggleCategory(id: string) {
    const next = activeCategories.includes(id)
      ? activeCategories.filter((category) => category !== id)
      : [...activeCategories, id];
    if (next.length <= 32) void setCategories(next);
  }

  let refreshing = $state(false);
  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      if (path === '/direct') await invalidate('atlas:live-feed');
      else await invalidateAll();
    } finally {
      refreshing = false;
    }
  }

  $effect(() => {
    if (path !== '/direct') return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void refresh().catch(() => {});
    }, 60_000);
    return () => window.clearInterval(timer);
  });
</script>

<div class="page-top">
  <div>
    <p class="eyebrow">NOVA ATLAS / {t.briefing}</p>
    <h1>{title}</h1>
  </div>
  <button onclick={refresh} disabled={refreshing}>{refreshing ? t.refreshing : t.refresh}</button>
</div>
{#if path === '/direct'}
  <div class="feed-filters" role="group" aria-label={t.filter}>
    <button type="button" aria-pressed={activeCategories.length === 0}
      onclick={() => void setCategories([])}>{t.all}</button>
    {#each choices as option (option.id)}
      <button type="button" data-category={option.id}
        aria-pressed={activeCategories.includes(option.id)} onclick={() => toggleCategory(option.id)}>
        <span aria-hidden="true">{option.icon}</span> {option.name}
      </button>
    {/each}
  </div>
{/if}
<Notice state={resource.state} empty={!resource.data?.articles.length} {locale} />
<div class="timeline" class:with-sidebar={!!sidebar}>
  {#if sidebar}
    <aside class="timeline-briefs">{@render sidebar()}</aside>
  {:else}
    <aside class="timeline-aside">
      <strong>{t.live}</strong>
      <p>{t.collected}<br />{resource.fetchedAt.slice(0, 10)}</p>
    </aside>
  {/if}
  <div class="live-list" class:direct-list={path === '/direct'}>
    {#each resource.data?.articles || [] as article (article.id)}
      {@const category = path === '/direct' ? choices.find((item) => item.id === article.category) : undefined}
      <ArticleCard {article} {locale} {category} compact={path === '/direct'} />
    {/each}
  </div>
</div>
{#if resource.data}<Pagination
    page={resource.data.page}
    total={resource.data.total}
    size={resource.data.per_page}
    path={filterPath(selectedCategories)}
    {locale}
  />{/if}
