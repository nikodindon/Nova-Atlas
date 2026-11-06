<script lang="ts">
  import EventCard from '@lib/components/EventCard.svelte';
  import Notice from '@lib/components/Notice.svelte';
  import Pagination from '@lib/components/Pagination.svelte';
  import { messages } from '@lib/i18n';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
</script>

<svelte:head><title>{t.events} — Nova Atlas</title></svelte:head>
<div class="page-top">
  <div>
    <p class="eyebrow">NOVA ATLAS / {t.coverage}</p>
    <h1>{t.events}</h1>
  </div>
</div>
<p class="notice">{t.noConfirmation}</p>
<Notice state={data.events.state} empty={!data.events.data?.events.length} locale={data.locale} />
<div class="event-grid">
  {#each data.events.data?.events || [] as event}<EventCard {event} locale={data.locale} />{/each}
</div>
{#if data.events.data}<Pagination
    page={data.events.data.page}
    total={data.events.data.total}
    size={data.events.data.per_page}
    path="/evenements"
    locale={data.locale}
  />{/if}
