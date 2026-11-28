<script lang="ts">
  import Feed from '@lib/components/Feed.svelte';
  import { messages, dateLabel } from '@lib/i18n';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
  let publications = $derived([
    { heading: t.liveEdition, item: data.currentEdition, empty: t.editionPending },
    { heading: t.liveDaily, item: data.dailyReport, empty: t.dailyPending },
  ]);
</script>

<svelte:head><title>{t.live} — Nova Atlas</title></svelte:head>
<Feed
  resource={data.articles}
  locale={data.locale}
  categories={data.site.data?.categories || []}
  selectedCategories={data.selectedCategories}
  title={t.live}
>
  {#snippet sidebar()}
    {#each publications as publication}
      <section class="live-brief">
        <h2>{publication.heading}</h2>
        {#if publication.item}
          {@const item = publication.item}
          <div class="live-brief-heading">
            <h3><a href={item.href}>{item.kind === 'report' ? t.liveReportLink : t[item.kind]}</a></h3>
            <time datetime={item.day.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')}>
              {dateLabel(item.day, data.locale)}
            </time>
          </div>
          {#if item.excerpt}<p>{item.excerpt}</p>{/if}
        {:else}
          <p>{data.archiveState === 'unavailable' ? t.unavailable : publication.empty}</p>
        {/if}
      </section>
    {/each}
  {/snippet}
</Feed>

<style>
  .live-brief {
    border-top: 2px solid var(--teal);
    border-bottom: 1px solid var(--line);
    padding: 0.9rem 0;
  }
  .live-brief h2 {
    font-size: 1.3rem;
    margin: 0 0 0.55rem;
  }
  .live-brief h3 {
    font-size: 1rem;
    margin: 0;
  }
  .live-brief-heading {
    display: grid;
    gap: 0.25rem;
  }
  .live-brief time, .live-brief p {
    color: var(--muted);
    font-size: 0.78rem;
  }
  .live-brief p {
    margin: 0.5rem 0 0;
  }
</style>
