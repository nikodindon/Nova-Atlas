<script lang="ts">
  import ArticleCard from './ArticleCard.svelte';
  import Notice from './Notice.svelte';
  import Pagination from './Pagination.svelte';
  import { messages } from '@lib/i18n';
  import { invalidateAll, goto } from '$app/navigation';
  import type { Articles, Category, Locale, Resource } from '@lib/types';
  let {
    resource,
    locale,
    categories,
    category = '',
    path = '/direct',
    title,
  }: {
    resource: Resource<Articles>;
    locale: Locale;
    categories: Category[];
    category?: string;
    path?: string;
    title: string;
  } = $props();
  let t = $derived(messages(locale));
  let refreshing = $state(false);
  async function refresh() {
    refreshing = true;
    try {
      await invalidateAll();
    } finally {
      refreshing = false;
    }
  }
</script>

<div class="page-top">
  <div>
    <p class="eyebrow">NOVA ATLAS / {t.briefing}</p>
    <h1>{title}</h1>
  </div>
  <button onclick={refresh} disabled={refreshing}>{refreshing ? t.refreshing : t.refresh}</button>
</div>
{#if path === '/direct'}<div class="toolbar">
    <label
      >{t.filter}<select
        value={category}
        onchange={(e) =>
          goto(
            '/direct' +
              (e.currentTarget.value
                ? '?category=' + encodeURIComponent(e.currentTarget.value)
                : ''),
          )}
        ><option value="">{t.all}</option>{#each categories as cat}<option value={cat.id}
            >{cat.name}</option
          >{/each}</select
      ></label
    >
  </div>{/if}
<Notice state={resource.state} empty={!resource.data?.articles.length} {locale} />
<div class="timeline">
  <aside class="timeline-aside">
    <strong>{t.live}</strong>
    <p>{t.collected}<br />{resource.fetchedAt.slice(0, 10)}</p>
  </aside>
  <div class="live-list">
    {#each resource.data?.articles || [] as article}<ArticleCard {article} {locale} />{/each}
  </div>
</div>
{#if resource.data}<Pagination
    page={resource.data.page}
    total={resource.data.total}
    size={resource.data.per_page}
    path={path +
      (category && path === '/direct' ? '?category=' + encodeURIComponent(category) : '')}
    {locale}
  />{/if}
