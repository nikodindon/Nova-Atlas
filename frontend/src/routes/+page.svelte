<script lang="ts">
  import ArticleCard from '@lib/components/ArticleCard.svelte';
  import HeadlineRotation from '@lib/components/HeadlineRotation.svelte';
  import EventCard from '@lib/components/EventCard.svelte';
  import Notice from '@lib/components/Notice.svelte';
  import { messages, dateLabel } from '@lib/i18n';
  import { safeLink } from '@lib/security';
  let { data }: import('./$types').PageProps = $props();
  let t = $derived(messages(data.locale));
  let breaking = $derived(data.headlines.data?.articles ?? []);
  let latest = $derived(data.articles.data?.articles ?? []);
  const editions = ['matin', 'midi', 'soir'] as const;
  let days = $derived(data.archives.data?.days ?? []);
  let today = $derived(days.find((day) => day.date === data.today));
  let previousDays = $derived(days.filter((day) => day.date < data.today));
  let other = $derived(breaking.length
    ? latest.filter((article) => !breaking.some((item) => item.id === article.id))
    : latest.slice(4));
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
{#if breaking.length}
  <HeadlineRotation articles={breaking} locale={data.locale} />
{:else if data.articles.data?.articles.length}<section class="headlines" aria-label={t.home}>
    <ArticleCard article={data.articles.data.articles[0]} locale={data.locale} lead compact />
    <div class="headlines-side">
      {#each data.articles.data.articles.slice(1, 4) as article (article.id)}
        {@const edition = data.locale === 'en' ? article.english_edition : undefined}
        <details class="headline-teaser">
          <summary>{edition?.title ?? article.title}</summary>
          <p>{edition?.summary ?? article.summary}</p>
          {#if safeLink(article.link)}
            <a href={safeLink(article.link)} target="_blank" rel="noopener noreferrer">{t.origin} ↗</a>
          {/if}
        </details>
      {/each}
    </div>
  </section>{/if}
<section class="daily-editions" aria-labelledby="daily-editions-title">
  <div class="section-heading">
    <h2 id="daily-editions-title">{t.todayEditions}</h2>
    <time datetime={data.today.replace(/^(\d{4})(\d{2})(\d{2})$/, '$1-$2-$3')}>
      {dateLabel(data.today, data.locale)}
    </time>
  </div>
  {#if data.archives.data}
    <ul class="daily-editions-grid">
      {#each editions as kind}
        <li>
          {#if today?.editions.includes(kind)}
            <a href={'/editions-v2/' + data.today + '/' + kind}>{t[kind]} <span aria-hidden="true">↗</span></a>
          {:else}
            <span>{t[kind]} <small>{t.notPublishedYet}</small></span>
          {/if}
        </li>
      {/each}
      <li>
        {#if today?.report}
          <a href={'/rapports/' + data.today}>{t.fullDailyReport} <span aria-hidden="true">↗</span></a>
        {:else}
          <span>{t.fullDailyReport} <small>{t.notPublishedYet}</small></span>
        {/if}
      </li>
    </ul>
  {:else}
    <Notice state={data.archives.state} empty={false} locale={data.locale} />
  {/if}
</section>
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
  {#each other as article}<ArticleCard
      {article}
      locale={data.locale}
    />{/each}
</div>
<div class="section-heading">
  <h2>{t.archives}</h2>
  <a href="/archives">{t.archives} ↗</a>
</div>
<div class="archive-grid">
  {#each previousDays as day}<article class="archive-card">
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

<style>
  .headline-teaser {
    border-bottom: 1px solid var(--line);
    padding-bottom: 0.8rem;
  }

  .headline-teaser summary {
    cursor: pointer;
    font-size: 1.1rem;
    font-weight: 650;
    line-height: 1.35;
  }

  .headline-teaser p {
    color: var(--muted);
    margin: 0.6rem 0;
  }


  .daily-editions-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 11rem), 1fr));
    gap: 0.65rem;
    padding: 0;
    margin: 0;
    list-style: none;
  }

  .daily-editions-grid li > :is(a, span) {
    display: block;
    height: 100%;
    padding: 0.8rem;
    border: 1px solid var(--line);
    font-weight: 650;
  }

  .daily-editions-grid a {
    text-decoration-thickness: 1px;
  }

  .daily-editions-grid small,
  .daily-editions time {
    color: var(--muted);
  }

  .daily-editions-grid small {
    display: block;
    margin-top: 0.2rem;
    font-weight: normal;
  }
</style>
