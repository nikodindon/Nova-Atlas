<script lang="ts">
  import Feed from '@lib/components/Feed.svelte';
  import { messages } from '@lib/i18n';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
</script>

<svelte:head><title>{t.live} — Nova Atlas</title></svelte:head>
<Feed
  resource={data.articles}
  locale={data.locale}
  categories={data.site.data?.categories || []}
  category={data.category}
  title={t.live}
/>
