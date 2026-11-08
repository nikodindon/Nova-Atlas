<script lang="ts">
  import '../app.css';
  import RadioPlayer from '@lib/components/RadioPlayer.svelte';
  import { messages } from '@lib/i18n';
  import { invalidateAll } from '$app/navigation';
  import type { Snippet } from 'svelte';
  import type { Locale, Resource, Site } from '@lib/types';
  let {
    data,
    children,
  }: {
    data: { locale: Locale; theme: 'light' | 'dark'; site: Resource<Site> };
    children: Snippet;
  } = $props();
  let t = $derived(messages(data.locale));
  function theme() {
    const value = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = value;
    document.cookie = `atlas_theme=${value}; Path=/; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
  }
  async function language(event: Event) {
    const value = (event.currentTarget as HTMLSelectElement).value;
    document.cookie = `atlas_language=${value === 'en' ? 'en' : 'fr'}; Path=/; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
    document.documentElement.lang = value;
    await invalidateAll();
  }
</script>

<a class="skip-link" href="#main">{t.skip}</a>
<header class="masthead">
  <div class="masthead-inner">
    <a href="/" class="wordmark">NOVA<span>ATLAS<span class="brand-dot">.</span></span></a>
    <div class="masthead-caption">{t.tagline}<span>INFORMATION · PERSPECTIVES · RADIO</span></div>
    <div class="utilities">
      <button onclick={theme} aria-label={t.theme}>◐</button><label class="sr-only" for="language"
        >{t.language}</label
      ><select id="language" value={data.locale} onchange={language}
        ><option value="fr">FR</option><option value="en">EN</option></select
      ><a href="/administration" class="admin-link">{t.admin} ↗</a>
    </div>
  </div>
  <nav class="main-nav" aria-label="Navigation">
    <a href="/">{t.home}</a><a href="/direct">{t.live}</a><a href="/evenements">{t.events}</a><a
      href="/archives">{t.archives}</a
    ><span class="nav-edition">{t.briefing} / NOVA ATLAS V2</span>
  </nav>
</header>
<main id="main" tabindex="-1">{@render children()}</main>
<footer class="site-footer">
  <strong>NOVA ATLAS</strong>
  <p>{t.tagline} <a href="/archives">{t.archives}</a></p>
  <a href="/index.html" data-sveltekit-reload>Interface classique ↗</a>
</footer>
<RadioPlayer
  locale={data.locale}
  streams={data.site.data?.streams || { primary: '/stream/nova', android: '/stream/nova-android' }}
/>
