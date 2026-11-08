<script lang="ts">
  import { messages, dateLabel } from '@lib/i18n';
  import Notice from '@lib/components/Notice.svelte';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
</script>

<svelte:head><title>{t.sources} — Nova Atlas</title></svelte:head>
<p class="eyebrow">{t.readOnly} / 50 max.</p>
<h1>{t.sourceManager}</h1>
<p><a href="/config/feeds/page" data-sveltekit-reload>{t.legacy} ↗</a></p>
<Notice state={data.sources.state} locale={data.locale} />
<div class="archive-grid">
  {#each data.sources.data?.sources || [] as source}<article class="archive-card">
      <p class="eyebrow">{source.category}</p>
      <h2>{source.name || source.url}</h2>
      <p>{source.health}</p>
      <p>{source.url}</p>
      <p>
        {t.collected} : {source.observed_at
          ? dateLabel(new Date(source.observed_at * 1000).toISOString(), data.locale)
          : '—'}{source.stale ? ' · ' + t.stale : ''}
      </p>
    </article>{/each}
</div>
