<script lang="ts">
  import Feed from '@lib/components/Feed.svelte';
  import { messages, dateLabel } from '@lib/i18n';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
</script>

<svelte:head><title>{dateLabel(data.day, data.locale)} — Nova Atlas</title></svelte:head><Feed
  resource={data.articles}
  locale={data.locale}
  categories={[]}
  path={'/archives/' + data.day}
  title={t.archiveDay + ' ' + dateLabel(data.day, data.locale)}
/>
