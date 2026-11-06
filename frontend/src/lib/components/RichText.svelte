<script lang="ts">
  import RichText from './RichText.svelte';
  import type { RichNode } from '@lib/types';
  import { safeLink } from '@lib/security';
  let { nodes }: { nodes: RichNode[] } = $props();
  const allowed = new Set([
    'p',
    'strong',
    'em',
    'b',
    'i',
    'del',
    's',
    'blockquote',
    'pre',
    'code',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'ul',
    'ol',
    'li',
    'table',
    'thead',
    'tbody',
    'tr',
    'th',
    'td',
    'sup',
    'sub',
    'dl',
    'dt',
    'dd',
  ]);
</script>

{#each nodes as node}{#if node.text !== undefined}{node.text}{:else if node.tag === 'a'}<a
      href={safeLink(node.href)}
      rel="noopener noreferrer"><RichText nodes={node.children || []} /></a
    >{:else if node.tag === 'br'}<br />{:else if node.tag === 'hr'}<hr
    />{:else if node.tag && allowed.has(node.tag)}<svelte:element this={node.tag}
      ><RichText nodes={node.children || []} /></svelte:element
    >{/if}{/each}
