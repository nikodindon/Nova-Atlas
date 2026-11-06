<script lang="ts">
  import { page } from '$app/state';
</script>

<svelte:head><title>Nova Atlas — page indisponible</title></svelte:head>
<section class="reading">
  <p class="eyebrow">NOVA ATLAS / {page.status}</p>
  <h1>Cette page n’est pas disponible.</h1>
  <p role="alert">Le contenu est absent ou le service momentanément indisponible.</p>
  <a href="/">Revenir à la une ↗</a>
</section>
