<script lang="ts">
  import { onMount } from 'svelte';
  import { messages } from '@lib/i18n';
  import { streamPath } from '@lib/security';
  import type { Locale } from '@lib/types';
  let { streams, locale }: { streams: { primary: string; android: string }; locale: Locale } =
    $props();
  let t = $derived(messages(locale));
  let audio: HTMLAudioElement;
  let channel = $state<'primary' | 'android'>('primary');
  let volume = $state(0.7);
  let wanted = $state(false);
  let attempts = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let playback = $state<'idle' | 'loading' | 'playing' | 'retry' | 'error'>('idle');
  function clear() {
    if (timer) clearTimeout(timer);
    timer = undefined;
  }
  function failed() {
    if (!wanted) return;
    clear();
    if (attempts >= 3) {
      playback = 'error';
      wanted = false;
      return;
    }
    playback = 'retry';
    const delay = [1000, 3000, 8000][attempts++];
    timer = setTimeout(() => {
      if (wanted) {
        audio.load();
        void play();
      }
    }, delay);
  }
  async function play() {
    const path = streamPath(streams[channel]);
    if (!path) {
      playback = 'error';
      return;
    }
    if (audio.getAttribute('src') !== path) audio.src = path;
    audio.volume = volume;
    playback = 'loading';
    try {
      await audio.play();
    } catch {
      failed();
    }
  }
  function toggle() {
    clear();
    if (wanted) {
      wanted = false;
      audio.pause();
      playback = 'idle';
    } else {
      wanted = true;
      attempts = 0;
      void play();
    }
  }
  function selectChannel() {
    clear();
    attempts = 0;
    audio.pause();
    audio.removeAttribute('src');
    if (wanted) void play();
    else playback = 'idle';
  }
  onMount(() => () => {
    wanted = false;
    clear();
    audio.pause();
  });
</script>

<aside class="radio-player" aria-label="Radio Nova Atlas">
  <audio
    bind:this={audio}
    preload="none"
    onplaying={() => {
      playback = 'playing';
    }}
    onwaiting={() => {
      if (wanted) playback = 'loading';
    }}
    onerror={failed}
    onended={failed}
  ></audio>
  <div class="radio-mark" aria-hidden="true">◉</div>
  <div class="radio-copy">
    <strong>NOVA RADIO</strong><span aria-live="polite"
      >{playback === 'playing'
        ? t.audioPlaying
        : playback === 'loading'
          ? t.audioLoading
          : playback === 'retry'
            ? t.audioRetry
            : playback === 'error'
              ? t.audioError
              : t.audioIdle}</span
    >
  </div>
  <button class="play" onclick={toggle} aria-label={wanted ? t.pause : t.play}
    >{wanted ? 'Ⅱ' : '▶'}</button
  >
  <label class="channel"
    >{t.channel}<select aria-label={t.channel} bind:value={channel} onchange={selectChannel}
      ><option value="primary">{t.primary}</option><option value="android">{t.android}</option
      ></select
    ></label
  >
  <label class="volume"
    >{t.volume}<input
      type="range"
      min="0"
      max="1"
      step="0.05"
      bind:value={volume}
      oninput={() => {
        if (audio) audio.volume = volume;
      }}
    /></label
  >
</aside>
