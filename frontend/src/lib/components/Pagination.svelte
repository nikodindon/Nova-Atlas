<script lang="ts">
  import type { Locale } from '@lib/types';
  import { messages } from '@lib/i18n';
  let {
    page,
    total,
    size,
    path,
    locale,
  }: { page: number; total: number; size: number; path: string; locale: Locale } = $props();
  let t = $derived(messages(locale));
</script>

<nav class="pagination" aria-label="Pagination">
  {#if page > 1}<a href={path + (path.includes('?') ? '&' : '?') + 'page=' + (page - 1)}
      >{t.previous} ←</a
    >{/if}<span>{page} / {Math.max(1, Math.ceil(total / size))}</span>{#if page * size < total}<a
      href={path + (path.includes('?') ? '&' : '?') + 'page=' + (page + 1)}>{t.next} →</a
    >{/if}
</nav>
