<script lang="ts">
  import Feed from '@lib/components/Feed.svelte';
  let { data }: import('./$types').PageProps = $props();
  let title = $derived(
    data.site.data?.categories.find((c) => c.id === data.category)?.name || data.category,
  );
</script>

<svelte:head><title>{title} — Nova Atlas</title></svelte:head><Feed
  resource={data.articles}
  locale={data.locale}
  categories={data.site.data?.categories || []}
  category={data.category}
  path={'/categories/' + encodeURIComponent(data.category)}
  {title}
/>
