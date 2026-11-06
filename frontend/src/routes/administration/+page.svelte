<script lang="ts">
  import { messages } from '@lib/i18n';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
</script>

<svelte:head><title>{t.admin} — Nova Atlas</title></svelte:head>
<p class="eyebrow">NOVA ATLAS / {t.admin}</p>
<h1>{t.adminIntro}</h1>
<div class="admin-grid">
  <article class="admin-card">
    <h2>{t.sourceManager}</h2>
    <p>{t.sourceHealth}</p>
    <a href="/administration/sources">{t.readOnly} ↗</a>
    <p><a href="/config/feeds/page" data-sveltekit-reload>{t.legacy} ↗</a></p>
  </article>
  <article class="admin-card">
    <h2>{t.radioStudio}</h2>
    <p>{t.radioHealth}</p>
    <a href="/administration/radio">{t.readOnly} ↗</a>
    <p><a href="/radio-studio" data-sveltekit-reload>{t.legacy} ↗</a></p>
  </article>
  <article class="admin-card">
    <h2>{t.images}</h2>
    <p>{t.attribution} · {t.generic}</p>
    <a href="/config/feeds/page#media-manager" data-sveltekit-reload>{t.legacy} ↗</a>
  </article>
  <article class="admin-card">
    <h2>{t.discovery}</h2>
    <a href="/config/feeds/page#source-discovery" data-sveltekit-reload>{t.legacy} ↗</a>
  </article>
  <article class="admin-card">
    <h2>{t.configuration}</h2>
    <a href="/config/yaml" data-sveltekit-reload>{t.legacy} ↗</a>
  </article>
</div>
