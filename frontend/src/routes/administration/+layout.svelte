<script lang="ts">
  import { onMount, type Snippet } from 'svelte';
  import { goto, afterNavigate } from '$app/navigation';
  import { sessionToken, logout } from '@lib/client';
  import { messages } from '@lib/i18n';
  import type { Locale } from '@lib/types';
  let { children, data }: { children: Snippet; data: { locale: Locale } } = $props();
  let t = $derived(messages(data.locale));
  let denied = $state(false);
  async function check() {
    try {
      await sessionToken();
      denied = false;
    } catch {
      denied = true;
      await goto('/connexion', { replaceState: true });
    }
  }
  afterNavigate(() => {
    void check();
  });
  onMount(() => {
    const timer = setInterval(() => void check(), 30000);
    return () => clearInterval(timer);
  });
  async function signout() {
    try {
      await logout();
    } finally {
      denied = true;
      await goto('/connexion', { replaceState: true });
    }
  }
</script>

<div class="toolbar">
  <a href="/administration">{t.admin}</a><a href="/administration/sources">{t.sources}</a><a
    href="/administration/radio">{t.radioStudio}</a
  ><button onclick={signout}>{t.logout}</button>
</div>
{#if !denied}{@render children()}{/if}
