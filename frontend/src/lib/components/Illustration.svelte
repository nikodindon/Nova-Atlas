<script lang="ts">
  import { onMount } from 'svelte';
  import type { Illustration } from '@lib/types';
  import { localImage, safeLink } from '@lib/security';
  let { image, eager = false }: { image: Illustration; eager?: boolean } = $props();
  function imageFallback(img: HTMLImageElement) {
    const failed = () => {
      img.removeAttribute('srcset');
      if (!img.src.endsWith('/assets/news-fallback.svg')) img.src = '/assets/news-fallback.svg';
    };
    img.addEventListener('error', failed);
    if (img.complete && !img.naturalWidth) failed();
    return () => img.removeEventListener('error', failed);
  }
  let element: HTMLImageElement;
  onMount(() => imageFallback(element));
  let src = $derived(localImage(image.url));
</script>

<figure class:neutral={image.fallback}>
  <img
    {src}
    srcset={image.large_url ? `${src} 320w, ${localImage(image.large_url)} 768w` : undefined}
    sizes="(max-width: 640px) 90vw, 40vw"
    width={image.width || 320}
    height={image.height || 180}
    loading={eager ? 'eager' : 'lazy'}
    decoding="async"
    alt={image.caption}
    bind:this={element}
  />
  {#if !image.fallback}<figcaption>
      {image.caption}{#if image.attribution}
        · {image.attribution.author} · {image.attribution.credit} ·
        <a href={safeLink(image.attribution.source)} rel="noopener noreferrer">Source</a>
        ·
        <a href={safeLink(image.attribution.license_url)} rel="license noopener noreferrer"
          >{image.attribution.license}</a
        >
        · {image.attribution.changes}{/if}
    </figcaption>{/if}
</figure>
