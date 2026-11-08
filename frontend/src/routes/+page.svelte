<script lang="ts">
  import ArticleCard from '@lib/components/ArticleCard.svelte';
  import EventCard from '@lib/components/EventCard.svelte';
  import Notice from '@lib/components/Notice.svelte';
  import { messages, dateLabel } from '@lib/i18n';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
</script>

<svelte:head
  ><title>Nova Atlas — {t.home}</title><meta name="description" content={t.intro} /></svelte:head
>
<section class="intro">
  <div>
    <p class="edition-note">NOVA ATLAS / {t.briefing}</p>
    <h1>{t.tagline}</h1>
  </div>
  <p>{t.intro}<br /><a href="/direct">{t.discover} ↗</a></p>
</section>
<div class="category-strip" aria-label={t.filter}>
  {#each data.site.data?.categories.filter((c) => c.active) || [] as cat}<a
      href={'/categories/' + encodeURIComponent(cat.id)}>{cat.name}</a
    >{/each}
</div>
<Notice
  state={data.articles.state}
  empty={!data.articles.data?.articles.length}
  locale={data.locale}
/>
{#if data.articles.data?.articles.length}<section class="headlines" aria-label={t.home}>
    <ArticleCard article={data.articles.data.articles[0]} locale={data.locale} lead />
    <div class="headlines-side">
      {#each data.articles.data.articles.slice(1, 4) as article}<ArticleCard
          {article}
          locale={data.locale}
        />{/each}
    </div>
  </section>{/if}
<div class="section-heading">
  <h2>{t.events}</h2>
  <a href="/evenements">{t.events} ↗</a>
</div>
<Notice state={data.events.state} empty={!data.events.data?.events.length} locale={data.locale} />
<div class="event-grid">
  {#each data.events.data?.events || [] as event}<EventCard {event} locale={data.locale} />{/each}
</div>
<div class="section-heading">
  <h2>{t.latest}</h2>
  <a href="/direct">{t.live} ↗</a>
</div>
<div class="article-grid">
  {#each data.articles.data?.articles.slice(4) || [] as article}<ArticleCard
      {article}
      locale={data.locale}
    />{/each}
</div>
<div class="section-heading">
  <h2>{t.briefing} &amp; {t.archives.toLowerCase()}</h2>
  <a href="/archives">{t.archives} ↗</a>
</div>
<div class="archive-grid">
  {#each data.archives.data?.days || [] as day}<article class="archive-card">
      <p class="eyebrow">NOVA ATLAS / {t.briefing}</p>
      <h2><a href={'/archives/' + day.date}>{dateLabel(day.date, data.locale)}</a></h2>
      <div class="archive-links">
        {#if day.report}<a href={'/rapports/' + day.date}>{t.report}</a
          >{/if}{#each day.editions as kind}<a href={'/editions-v2/' + day.date + '/' + kind}
            >{t[kind as 'matin' | 'midi' | 'soir']}</a
          >{/each}
      </div>
    </article>{/each}
</div>
