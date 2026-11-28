<script lang="ts">
  import { onMount } from 'svelte';
  import { messages } from '@lib/i18n';
  import { streamPath } from '@lib/security';
  import type { Locale } from '@lib/types';
  let { streams, locale, personalMusic = false }: {
    streams: { primary: string; android: string }; locale: Locale; personalMusic?: boolean;
  } = $props();
  let t = $derived(messages(locale));
  let audio: HTMLAudioElement;
  let channel = $state<'primary' | 'android' | 'personal'>('primary');
  let genres = $state<string[]>([]);
  let genre = $state('');
  let track = '';
  let playingBulletin = $state(false);
  let lastBulletin = '';
  let personalPath = '';
  let pending = $state(false);
  let selection: AbortController | undefined;
  let generation = 0;
  const personal = '/api/radio/personal';
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
      if (channel === 'personal' && playingBulletin) {
        playingBulletin = false;
        void nextTrack(true);
        return;
      }
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
    const attempt = generation;
    const path = channel === 'personal' ? personalPath : streamPath(streams[channel]);
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
      if (attempt === generation) failed();
    }
  }

  async function loadGenres() {
    try {
      const response = await fetch(personal + '/genres', { signal: AbortSignal.timeout(3000) });
      if (!response.ok) return;
      const data = await response.json();
      if (Array.isArray(data.genres)) genres = data.genres.filter((item: unknown) => typeof item === 'string').slice(0, 100);
    } catch {
      // The next time personal listening is selected, try the catalogue again.
    }
  }

  function cancelSelection() {
    generation++;
    selection?.abort();
    selection = undefined;
    pending = false;
  }

  async function nextTrack(replayLast = false) {
    if (!wanted || channel !== 'personal' || playingBulletin) return;
    cancelSelection();
    clear();
    attempts = 0;
    const controller = new AbortController();
    selection = controller;
    pending = true;
    try {
      const params = new URLSearchParams({ genre, previous: track });
      const response = await fetch(personal + '/next?' + params, {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]),
      });
      if (!response.ok) throw new Error('catalogue');
      const data = await response.json();
      if (controller.signal.aborted || !wanted || channel !== 'personal') return;
      if (data.track === null && track && !audio.paused) return;
      if (data.track === null && track && replayLast) {
        personalPath = personal + '/track/' + track + '.mp3';
        await play();
        return;
      }
      if (typeof data.track !== 'string' || !/^[a-f0-9]{64}$/.test(data.track)) {
        audio.pause();
        audio.removeAttribute('src');
        personalPath = '';
        wanted = false;
        playback = 'error';
        return;
      }
      track = data.track;
      personalPath = personal + '/track/' + track + '.mp3';
      await play();
    } catch {
      if (!controller.signal.aborted) {
        wanted = false;
        audio.pause();
        playback = 'error';
      }
    } finally {
      if (selection === controller) {
        pending = false;
        selection = undefined;
      }
    }
  }

  async function checkBulletin() {
    if (!wanted || channel !== 'personal' || playingBulletin) return;
    const observation = generation;
    try {
      const response = await fetch(personal + '/bulletin', { signal: AbortSignal.timeout(3000) });
      if (!response.ok) return;
      const data = await response.json();
      const bulletin = data.bulletin;
      if (observation !== generation || !wanted || channel !== 'personal' || playingBulletin || !bulletin) return;
      if (typeof bulletin.name !== 'string' ||
          !/^bulletin_\d{8}_\d{6}_[a-f0-9]{32}\.mp3$/.test(bulletin.name) ||
          typeof bulletin.started !== 'number' || Date.now() / 1000 - bulletin.started > 120 ||
          lastBulletin === bulletin.name) return;
      lastBulletin = bulletin.name;
      cancelSelection();
      clear();
      attempts = 0;
      playingBulletin = true;
      personalPath = personal + '/bulletin/' + bulletin.name;
      await play();
    } catch {
      // A missing bulletin must not interrupt music.
    }
  }

  function ended() {
    if (channel !== 'personal') {
      failed();
      return;
    }
    playingBulletin = false;
    void nextTrack(true);
  }

  function toggle() {
    clear();
    if (wanted) {
      wanted = false;
      cancelSelection();
      audio.pause();
      playback = 'idle';
    } else {
      wanted = true;
      attempts = 0;
      if (channel === 'personal' && !personalPath) void nextTrack();
      else void play();
    }
  }
  function selectChannel() {
    cancelSelection();
    clear();
    attempts = 0;
    audio.pause();
    audio.removeAttribute('src');
    playingBulletin = false;
    personalPath = '';
    if (channel === 'personal') {
      void loadGenres();
      if (wanted) void nextTrack();
    } else if (wanted) void play();
    else playback = 'idle';
  }
  onMount(() => {
    const timer = personalMusic ? setInterval(() => {
      if (channel === 'personal' && genres.length === 0) void loadGenres();
      void checkBulletin();
    }, 10000) : undefined;
    return () => {
      wanted = false;
      cancelSelection();
      clear();
      clearInterval(timer);
      audio.pause();
    };
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
    onended={ended}
  ></audio>
  <div class="radio-mark" aria-hidden="true">◉</div>
  <div class="radio-copy">
    <strong>NOVA RADIO</strong><span aria-live="polite"
      >{playback === 'playing'
        ? playingBulletin ? t.personalBulletin : t.audioPlaying
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
      ><option value="primary">{t.primary}</option><option value="android">{t.android}</option>
      {#if personalMusic}<option value="personal">{t.personalListening}</option>{/if}</select
    ></label
  >
  {#if personalMusic && channel === 'personal'}
    <label class="channel">{t.musicGenre}
      <select bind:value={genre} disabled={playingBulletin} onchange={() => {
        personalPath = '';
        track = '';
        if (wanted) void nextTrack();
      }}>
        <option value="">{t.allMusicGenres}</option>
        {#each genres as value}<option {value}>{value}</option>{/each}
      </select>
    </label>
    <button class="next-track" type="button" disabled={!wanted || pending || playingBulletin}
      onclick={() => void nextTrack()}>{t.nextTrack}</button>
  {/if}
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
