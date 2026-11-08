<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { messages } from '@lib/i18n';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
  let username = $state(''),
    password = $state(''),
    token = '',
    ready = $state(false),
    busy = $state(false),
    message = $state('');
  async function bootstrap() {
    try {
      const response = await fetch('/api/auth/login', {
        cache: 'no-store',
        credentials: 'same-origin',
      });
      const value = await response.json();
      if (!response.ok || !value.configured || typeof value.csrf_token !== 'string') throw Error();
      token = value.csrf_token;
      ready = true;
    } catch {
      message = t.loginUnavailable;
    }
  }
  async function login(event: SubmitEvent) {
    event.preventDefault();
    if (!ready || busy) return;
    busy = true;
    message = '';
    try {
      const body = new URLSearchParams({ username, password, csrf_token: token });
      const response = await fetch('/admin/login', {
        method: 'POST',
        body,
        credentials: 'same-origin',
        redirect: 'manual',
      });
      if (response.type === 'opaqueredirect' || response.status === 303) {
        password = '';
        await goto('/administration');
      } else {
        message = t.loginError;
        password = '';
        await bootstrap();
      }
    } catch {
      message = t.loginUnavailable;
    } finally {
      busy = false;
    }
  }
  onMount(() => {
    void bootstrap();
  });
</script>

<svelte:head><title>{t.login} — Nova Atlas</title></svelte:head>
<section class="auth-card">
  <p class="eyebrow">NOVA ATLAS / {t.session}</p>
  <h1>{t.login}</h1>
  {#if message}<p role="alert" class="notice error">{message}</p>{/if}
  <form onsubmit={login}>
    <label
      >{t.username}<input
        name="username"
        autocomplete="username"
        bind:value={username}
        maxlength="128"
        required
      /></label
    ><label
      >{t.password}<input
        name="password"
        type="password"
        autocomplete="current-password"
        bind:value={password}
        required
      /></label
    ><button type="submit" disabled={!ready || busy}>{busy ? t.loading : t.login}</button>
  </form>
</section>
