<script lang="ts">
  import { messages } from '@lib/i18n';
  import Notice from '@lib/components/Notice.svelte';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
</script>

<svelte:head><title>{t.radioStudio} — Nova Atlas</title></svelte:head>
<p class="eyebrow">{t.readOnly}</p>
<h1>{t.radioStudio}</h1>
<p><a href="/radio-studio" data-sveltekit-reload>{t.legacy} ↗</a></p>
<Notice state={data.radio.state} locale={data.locale} />{#if data.radio.data}<p>{t.noHealth}</p>
  <div class="admin-grid">
    <section class="admin-card">
      <h2>{t.radioHealth}</h2>
      <pre>{JSON.stringify(data.radio.data.channels, null, 2)}</pre>
    </section>
    <section class="admin-card">
      <h2>{t.briefing}</h2>
      <pre>{JSON.stringify(data.radio.data.metrics, null, 2)}</pre>
    </section>
  </div>{/if}
