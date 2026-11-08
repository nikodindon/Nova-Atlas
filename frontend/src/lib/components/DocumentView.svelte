<script lang="ts">
  import type { Document, Resource, Locale } from '@lib/types';
  import Notice from './Notice.svelte';
  import RichText from './RichText.svelte';
  import { dateLabel } from '@lib/i18n';
  let { document, locale }: { document: Resource<Document>; locale: Locale } = $props();
</script>

<Notice state={document.state} {locale} />{#if document.data}<article class="reading">
    <p class="eyebrow">NOVA ATLAS / {dateLabel(document.data.date, locale)}</p>
    <h1>{document.data.title}</h1>
    <div class="prose"><RichText nodes={document.data.nodes} /></div>
  </article>{/if}
