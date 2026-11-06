<script lang="ts">
  import DocumentView from '@lib/components/DocumentView.svelte';
  let { data }: import('./$types').PageProps = $props();
</script>

<svelte:head><title>{data.document.data?.title || 'Nova Atlas'} — Nova Atlas</title></svelte:head
><DocumentView document={data.document} locale={data.locale} />
