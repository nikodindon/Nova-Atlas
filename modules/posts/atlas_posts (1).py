#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/posts/atlas_posts.py — Nova-Atlas
Génère des posts réseaux sociaux toutes les 2h.

Refactorisé depuis atlas_posts.py (pblart/nova-media) :
  - Enveloppé dans PostsGenerator(config)
  - Les chemins viennent de config.paths.*
  - Ollama passe par modules.core.ollama
  - Toute la logique (hot topics, JSON/TXT, fallback) est conservée à l'identique
"""

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path

from modules.core.ollama import init_ollama, ollama_call, get_language

NB_POSTS      = 4
WINDOW_HOURS  = 2
POST_MAX_X    = 280
POST_MAX_LONG = 500

CATEGORY_LABELS = {
    "geopolitique":  "GÉOPOLITIQUE",
    "economie":      "ÉCONOMIE",
    "crypto":        "CRYPTO",
    "tech":          "TECH",
    "france":        "FRANCE",
    "monde":         "MONDE",
    "science":       "SCIENCE",
    "environnement": "CLIMAT",
    "societe":       "SOCIÉTÉ",
    "culture":       "CULTURE",
    "sport":         "SPORT",
}

CATEGORY_EMOJIS = {
    "geopolitique":  "🌍",
    "economie":      "📈",
    "crypto":        "₿",
    "tech":          "⚡",
    "france":        "🗼",
    "monde":         "🌐",
    "science":       "🔬",
    "environnement": "🌿",
    "societe":       "✊",
    "culture":       "🎭",
    "sport":         "⚽",
}


class PostsGenerator:
    def __init__(self, config: dict):
        self.log = logging.getLogger("nova.posts")
        self._apply_config(config)
        init_ollama(config)

    def _apply_config(self, config: dict):
        paths = config.get("paths", {})
        self.articles_dir = Path(paths.get("articles_dir", "data/articles"))
        self.posts_dir    = Path(paths.get("posts_dir",    "data/posts"))
        self.posts_dir.mkdir(parents=True, exist_ok=True)

        posts_cfg     = config.get("posts", {})
        self.nb_posts = int(posts_cfg.get("nb_posts_per_cycle", NB_POSTS))
        self.window_h = int(posts_cfg.get("window_hours",       WINDOW_HOURS))
        self.max_x    = int(posts_cfg.get("max_chars_x",        POST_MAX_X))

    def reload_config(self, config: dict):
        """Recharge la config à chaud."""
        self._apply_config(config)
        from modules.core.ollama import init_ollama as _init
        _init(config)
        self.log.info("[POSTS] Config rechargée à chaud.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_articles(self, day: str) -> list:
        f = self.articles_dir / f"{day}_articles.json"
        if not f.exists():
            return []
        try:
            with open(f, encoding="utf-8") as fp:
                return json.load(fp)
        except Exception:
            return []

    def _get_recent(self) -> list:
        now   = datetime.now()
        since = now - timedelta(hours=self.window_h)
        today     = now.strftime("%Y%m%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y%m%d")

        all_articles = []
        for day in [yesterday, today]:
            all_articles.extend(self._load_articles(day))

        recent = []
        for a in all_articles:
            if a.get("migrated"):
                continue
            if not a.get("summary") or a["summary"].startswith("["):
                continue
            if not a.get("link"):
                continue
            try:
                if datetime.fromisoformat(a["timestamp"]) >= since:
                    recent.append(a)
            except Exception:
                continue
        return recent

    # ── Détection sujets chauds (identique à atlas_posts.py) ──────────────────

    @staticmethod
    def _detect_hot_topics(articles: list) -> list:
        stopwords = {
            "le","la","les","de","du","des","en","et","à","pour","sur","par",
            "dans","avec","un","une","au","aux","que","qui","est","sont","a",
            "the","of","in","to","and","is","are","for","on","at","an","that",
            "this","it","as","was","be","by","or","from","has","have","its",
            "but","not","with","they","their","been","more","will","can","all",
            "after","says","said","new","report","would","could","also","year",
        }

        def keywords(title: str) -> set:
            words = re.findall(r'\b[A-Za-zÀ-ÿ]{4,}\b', title.lower())
            return {w for w in words if w not in stopwords}

        topic_articles: dict = defaultdict(list)
        used = set()

        for i, a in enumerate(articles):
            if i in used:
                continue
            kw_i  = keywords(a.get("title", ""))
            group = [a]
            used.add(i)
            for j, b in enumerate(articles):
                if j <= i or j in used:
                    continue
                if len(kw_i & keywords(b.get("title", ""))) >= 2:
                    group.append(b)
                    used.add(j)

            all_kw: dict = defaultdict(int)
            for art in group:
                for w in keywords(art.get("title", "")):
                    all_kw[w] += 1
            top_kw   = sorted(all_kw.items(), key=lambda x: -x[1])[:3]
            topic_key = " + ".join(w for w, _ in top_kw) or a.get("title","")[:40]
            topic_articles[topic_key] = group

        return sorted(topic_articles.items(), key=lambda x: -len(x[1]))

    # ── Génération Ollama (identique à atlas_posts.py) ────────────────────────

    def _generate_posts(self, articles: list, now: datetime) -> list:
        if not articles:
            return []

        months   = ["janvier","février","mars","avril","mai","juin",
                    "juillet","août","septembre","octobre","novembre","décembre"]
        date_fr  = f"{now.day} {months[now.month-1]} {now.year}"
        heure    = now.strftime("%H:%M")
        topics   = self._detect_hot_topics(articles)
        lang     = get_language()

        context_parts = []
        hot_count = 0
        for topic, arts in topics:
            if len(arts) >= 2:
                sources   = list({a.get("source","") for a in arts})[:4]
                summaries = [a.get("summary","")[:120] for a in arts[:3]]
                context_parts.append(
                    f"🔥 SUJET CHAUD ({len(arts)} sources : {', '.join(sources)})\n"
                    f"Thème : {topic}\n"
                    + "\n".join(f"  - {s}" for s in summaries)
                )
                hot_count += 1
                if hot_count >= 5:
                    break

        hot_articles = {
            art for _, arts in topics for art in arts if len(arts) >= 2
        }
        for a in articles:
            if a not in hot_articles:
                cat   = CATEGORY_LABELS.get(a.get("category","monde"), "NEWS")
                context_parts.append(
                    f"[{cat}] {a.get('title','')[:80]}\n"
                    f"  Source: {a.get('source','')}\n"
                    f"  {a.get('summary','')[:150]}"
                )

        context = "\n\n".join(context_parts[:20])
        prompt  = (
            f"Tu es rédacteur en chef d'un compte de news indépendant sur les réseaux sociaux.\n"
            f"Il est {heure} le {date_fr}. Voici les dernières news des {self.window_h} dernières heures :\n\n"
            f"{context}\n\n"
            f"Génère exactement {self.nb_posts} posts pour les réseaux sociaux.\n\n"
            f"Règles :\n"
            f"- Rédige UNIQUEMENT en {lang}\n"
            f"- Priorise les SUJETS CHAUDS (mentionnés par plusieurs sources)\n"
            f"- Diversifie les catégories\n"
            f"- Accroche forte dès la première ligne\n"
            f"- Ton neutre, factuel, jamais sensationnaliste\n"
            f"- Texte max 200 caractères + 3-4 hashtags pertinents\n"
            f"- Commence par l'emoji de catégorie\n\n"
            f"Réponds UNIQUEMENT en JSON sans texte avant ou après :\n"
            f'{{"posts": [\n'
            f'  {{"categorie": "geopolitique", "texte": "🌍 GÉOPOLITIQUE\\n\\nAccroche.", '
            f'"hashtags": ["#tag1", "#tag2"], "source": "site.com", "lien": "https://..."}}\n'
            f']}}'
        )

        response = ollama_call(prompt, timeout=300, caller="posts")
        posts    = []

        try:
            match = re.search(r'\{[\s\S]*"posts"[\s\S]*\}', response)
            if match:
                raw  = re.sub(r',\s*([}\]])', r'\1', match.group())
                data = json.loads(raw)
                for rp in data.get("posts", [])[:self.nb_posts]:
                    texte    = rp.get("texte","").strip()
                    hashtags = " ".join(rp.get("hashtags",[])[:5])
                    lien     = rp.get("lien","")
                    cat      = rp.get("categorie","monde")

                    body_x = f"{texte}\n\n{hashtags}"
                    if len(body_x) > self.max_x - 25:
                        body_x = body_x[:self.max_x - 28] + "..."
                    post_x    = f"{body_x}\n\n{lien}" if lien else body_x
                    post_long = f"{texte}\n\n{hashtags}" + (f"\n\n🔗 {lien}" if lien else "")

                    posts.append({
                        "slot":      now.strftime("%Y%m%d_%H%M"),
                        "category":  cat,
                        "source":    rp.get("source",""),
                        "lien":      lien,
                        "post_x":    post_x,
                        "post_long": post_long,
                        "hashtags":  rp.get("hashtags",[]),
                        "generated": now.isoformat(),
                        "validated": False,
                    })
        except Exception:
            pass

        # Fallback si JSON échoue
        if not posts:
            for a in articles[:self.nb_posts]:
                cat   = a.get("category","monde")
                emoji = CATEGORY_EMOJIS.get(cat,"🌍")
                label = CATEGORY_LABELS.get(cat,"NEWS")
                summ  = a.get("summary","")[:180]
                lien  = a.get("link","")
                post_x = f"{emoji} {label}\n\n{summ}"[:self.max_x]
                posts.append({
                    "slot":      now.strftime("%Y%m%d_%H%M"),
                    "category":  cat,
                    "source":    a.get("source",""),
                    "lien":      lien,
                    "post_x":    post_x,
                    "post_long": f"{post_x}\n\n🔗 {lien}"[:POST_MAX_LONG],
                    "hashtags":  [],
                    "generated": now.isoformat(),
                    "validated": False,
                })
        return posts

    # ── Sauvegarde (identique à atlas_posts.py) ───────────────────────────────

    def _save(self, posts: list, slot: str) -> str:
        out_json = self.posts_dir / f"{slot}_posts.json"
        existing = []
        if out_json.exists():
            try:
                with open(out_json, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        all_posts = existing + posts
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(all_posts, f, ensure_ascii=False, indent=2)

        # TXT lisible
        out_txt = self.posts_dir / f"{slot}_posts.txt"
        sep     = "=" * 60
        lines   = [f"Nova-Atlas — Posts du {slot}", sep, ""]
        for i, post in enumerate(all_posts, 1):
            cat   = post.get("category","")
            emoji = CATEGORY_EMOJIS.get(cat,"🌐")
            lines += [
                f"--- POST {i} | {emoji} {cat.upper()} | {post.get('source','')} ---",
                "", "[ X / TWITTER ]", post.get("post_x",""),
                "", "[ BLUESKY / MASTODON ]", post.get("post_long",""),
                "", sep, "",
            ]
        out_txt.write_text("\n".join(lines), encoding="utf-8")
        return str(out_json)

    # ── Point d'entrée ────────────────────────────────────────────────────────

    def generate(self, slot: str = None) -> str:
        """
        Génère les posts pour le créneau actuel (ou un créneau précis YYYYMMDD_HHMM).
        Retourne le chemin du fichier JSON.
        Équivalent de generate_posts() dans atlas_posts.py.
        """
        now = datetime.now()
        if slot and slot != "today":
            for fmt in ("%Y%m%d_%H%M", "%Y%m%d"):
                try:
                    now = datetime.strptime(slot, fmt)
                    break
                except ValueError:
                    continue

        slot_key = now.strftime("%Y%m%d_%H%M")
        self.log.info(f"Génération posts pour le créneau {slot_key}")

        articles = self._get_recent()
        self.log.info(f"{len(articles)} articles dans la fenêtre des {self.window_h}h")

        if not articles:
            self.log.warning("Aucun article récent — posts non générés")
            return ""

        topics = self._detect_hot_topics(articles)
        hot    = [(t, a) for t, a in topics if len(a) >= 2]
        if hot:
            self.log.info(
                f"{len(hot)} sujets chauds : "
                + ", ".join(t[:30] for t, _ in hot[:3])
            )

        self.log.info("Ollama rédige les posts...")
        posts = self._generate_posts(articles, now)
        self.log.info(f"{len(posts)} posts générés")

        if not posts:
            self.log.error("Échec génération posts")
            return ""

        return self._save(posts, slot_key)
