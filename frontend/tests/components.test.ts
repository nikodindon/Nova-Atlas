import { render, screen } from '@testing-library/svelte';
import { it, expect } from 'vitest';
import Notice from '../src/lib/components/Notice.svelte';
import RichText from '../src/lib/components/RichText.svelte';
import ArticleCard from '../src/lib/components/ArticleCard.svelte';
it('announces API failure accessibly', () => {
  render(Notice, { state: 'unavailable', locale: 'fr' });
  expect(screen.getByRole('alert')).toHaveTextContent('momentanément');
});
it('keeps hostile rich text inert and rejects active tags', () => {
  const { container } = render(RichText, {
    nodes: [
      { text: '<img src=x onerror=alert(1)>' },
      { tag: 'script', children: [{ text: 'alert(2)' }] },
      { tag: 'a', href: 'javascript:alert(3)', children: [{ text: 'Link' }] },
    ],
  });
  expect(container.querySelector('script,img')).toBeNull();
  expect(screen.getByText('Link').getAttribute('href')).toBeNull();
});
it('escapes RSS title and keeps unknown category', () => {
  render(ArticleCard, {
    locale: 'fr',
    article: {
      id: 'id',
      hash: 'hash',
      title: '<script>hostile</script> 日本語',
      original_title: 'title',
      summary: 'Safe text',
      link: 'javascript:alert(1)',
      source: 'Source',
      category: 'custom',
      published_at: null,
      published_raw: 'date inconnue',
      observed_at: null,
      observed_raw: '',
      image: {
        url: '/assets/news-fallback.svg',
        width: 320,
        height: 180,
        fallback: true,
        caption: 'Fallback',
        attribution: null,
      },
    },
  });
  expect(screen.getByText('<script>hostile</script> 日本語')).toBeInTheDocument();
  expect(screen.queryByText('Lire la source')).toBeNull();
});

import { fireEvent } from '@testing-library/svelte';
import { vi } from 'vitest';
import { tick } from 'svelte';
import RadioPlayer from '../src/lib/components/RadioPlayer.svelte';
it('requires user play, bounds reconnects and cancels retries on pause', async () => {
  vi.useFakeTimers();
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {});
  try {
    const { container, unmount } = render(RadioPlayer, {
      streams: { primary: '/stream/nova', android: '/stream/nova-android' },
      locale: 'fr',
    });
    expect(play).not.toHaveBeenCalled();
    await fireEvent.click(screen.getByRole('button', { name: 'Écouter' }));
    expect(play).toHaveBeenCalledTimes(1);
    const audio = container.querySelector('audio')!;
    for (const delay of [1000, 3000, 8000]) {
      await fireEvent.error(audio);
      expect(screen.getByText('Reconnexion…')).toBeInTheDocument();
      await vi.advanceTimersByTimeAsync(delay);
      await tick();
    }
    await fireEvent.error(audio);
    expect(
      screen.getByText('Flux indisponible. Réessayez pour vous reconnecter.'),
    ).toBeInTheDocument();
    expect(play).toHaveBeenCalledTimes(4);
    await fireEvent.click(screen.getByRole('button', { name: 'Écouter' }));
    await fireEvent.error(audio);
    await fireEvent.click(screen.getByRole('button', { name: 'Pause' }));
    await vi.advanceTimersByTimeAsync(10000);
    expect(play).toHaveBeenCalledTimes(5);
    unmount();
  } finally {
    play.mockRestore();
    pause.mockRestore();
    load.mockRestore();
    vi.useRealTimers();
  }
});
