<script lang="ts">
  import { messages, dateLabel } from '@lib/i18n';
  import Notice from '@lib/components/Notice.svelte';
  import Pagination from '@lib/components/Pagination.svelte';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
</script>

<svelte:head><title>{t.archives} — Nova Atlas</title></svelte:head>
<div class="page-top"><h1>{t.archives}</h1></div>
<Notice state={data.archives.state} empty={!data.archives.data?.days.length} locale={data.locale} />
<div class="archive-grid">
  {#each data.archives.data?.days || [] as day}<article class="archive-card">
      <p class="eyebrow">{t.briefing}</p>
      <h2><a href={'/archives/' + day.date}>{dateLabel(day.date, data.locale)}</a></h2>
      <div class="archive-links">
        {#if day.report}<a href={'/rapports/' + day.date}>{t.report}</a
          >{/if}{#each day.editions as kind}<a href={'/editions-v2/' + day.date + '/' + kind}
            >{t[kind as 'matin' | 'midi' | 'soir']}</a
          >{/each}
      </div>
    </article>{/each}
</div>
{#if data.archives.data}<Pagination
    page={data.archives.data.page}
    total={data.archives.data.total}
    size={data.archives.data.per_page}
    path="/archives"
    locale={data.locale}
  />{/if}
