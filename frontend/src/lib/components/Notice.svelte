<script lang="ts">
  import { messages } from '@lib/i18n';
  import type { Locale } from '@lib/types';
  let {
    state,
    empty = false,
    locale,
  }: { state: string; empty?: boolean; locale: Locale } = $props();
  let t = $derived(messages(locale));
</script>

{#if state === 'unavailable'}<p class="notice error" role="alert">
    {t.unavailable}
  </p>{:else if state === 'stale'}<p class="notice" role="status">{t.stale}</p>{/if}
{#if empty && state !== 'unavailable'}<p class="empty-state">{t.noData}</p>{/if}
