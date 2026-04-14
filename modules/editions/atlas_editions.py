#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/editions/atlas_editions.py — Nova-Atlas
Génère les éditions Matin / Midi / Soir à partir des articles de la fenêtre de temps.
Style journalistique narratif, ~1000-1400 mots.
Archivées dans data/editions/YYYYMMDD_matin.md etc.
"""

import json
import re
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

# ─── DÉFINITION DES ÉDITIONS ──────────────────────────────────────────────────

EDITIONS = {
    "matin": {
        "label":          "Édition du Matin",
        "emoji":          "🌅",
        "hour_start":     22,   # depuis 22h la veille
        "hour_end":       6,    # jusqu'à 06h aujourd'hui
        "cross_midnight": True,
        "gen_hour":       6,
        "words":          1200,
        "tone": (
            "Ton journalistique matinal — résume les événements de la nuit, "
            "factuel et énergique. Prépare le lecteur pour sa journée. "
            "Va à l'essentiel. Pas de longueurs."
        ),
    },
    "midi": {
        "label":          "Édition de Midi",
        "emoji":          "☀️",
        "hour_start":     6,
        "hour_end":       12,
        "cross_midnight": False,
        "gen_hour":       12,
        "words":          1200,
        "tone": (
            "Ton journalistique de milieu de journée — synthétise la matinée, "
            "met en perspective les développements depuis le lever. "
            "Lecteur pressé qui lit pendant sa pause déjeuner."
        ),
    },
    "soir": {
        "label":          "Édition du Soir",
        "emoji":          "🌙",
        "hour_start":     12,
        "hour_end":       19,
        "cross_midnight": False,
        "gen_hour":       19,
        "words":          1400,
        "tone": (
            "Ton journalistique du soir — analyse l'après-midi, prend du recul "
            "sur la journée, identifie les tendances et les enjeux à suivre. "
            "Lecteur qui se pose et veut comprendre avant sa soirée."
        ),
    },
}

CAT_LABELS = {
    "geopolitique":  "GÉOPOLITIQUE",
    "france":        "FRANCE",
    "economie":      "ÉCONOMIE",
    "monde":         "MONDE",
    "crypto":        "CRYPTO",
    "tech":          "TECH",
    "science":       "SCIENCE",
    "environnement": "ENVIRONNEMENT",
    "societe":       "SOCIÉTÉ",
    "culture":       "CULTURE",
    "sport":         "SPORT",
}

CAT_ORDER = list(CAT_LABELS.keys())


# ─── CLASSE PRINCIPALE ────────────────────────────────────────────────────────

class EditionGenerator:
    """
    Génère les éditions Matin / Midi / Soir.
    Reçoit le dict config issu de config/config.yaml.
    """

    def __init__(self, config: dict):
        import logging
        self.log = logging.getLogger("nova.editions")
        self._apply_config(config)

    def _apply_config(self, config: dict):
        self.config = config   # conservé pour _save() et autres méthodes
        paths             = config.get("paths", {})
        self.articles_dir = Path(paths.get("articles_dir", "data/articles"))
        self.editions_dir = Path(paths.get("editions_dir", "data/editions"))
        self.editions_dir.mkdir(parents=True, exist_ok=True)

        ollama_cfg    = config.get("ollama", {})
        self.model    = ollama_cfg.get("model", "mistral:7b")
        self.timeout  = int(ollama_cfg.get("timeout_edition", 900))
        self.language = config.get("service", {}).get("default_language", "fr")

    def reload_config(self, config: dict):
        """Recharge la config à chaud (langue, modèle, chemins)."""
        self._apply_config(config)
        self.log.info("[EDITIONS] Config rechargée à chaud.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _format_date_fr(day: str) -> str:
        months  = ["janvier","février","mars","avril","mai","juin",
                   "juillet","août","septembre","octobre","novembre","décembre"]
        days_fr = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
        d = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
        return f"{days_fr[d.weekday()]} {d.day} {months[d.month-1]} {d.year}"

    @staticmethod
    def detect_current_edition() -> str:
        h = datetime.now().hour
        if h < 12:  return "matin"
        if h < 19:  return "midi"
        return "soir"

    def _ollama_call(self, prompt: str) -> str:
        try:
            from modules.core.ollama import ollama_call
            return ollama_call(
                prompt, model=self.model,
                timeout=self.timeout, caller="editions"
            )
        except Exception as e:
            self.log.error(f"Erreur Ollama : {e}")
            return ""

    # ── Collecte des articles de la fenêtre ───────────────────────────────────

    def get_articles_for_edition(self, edition_name: str,
                                  day: Optional[str] = None) -> list:
        """Retourne les articles dans la fenêtre de temps de l'édition."""
        if day is None:
            day = datetime.now().strftime("%Y%m%d")

        cfg      = EDITIONS[edition_name]
        ref_date = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
        yest_str = (ref_date - timedelta(days=1)).strftime("%Y%m%d")

        if cfg["cross_midnight"]:
            since        = datetime(ref_date.year, ref_date.month, ref_date.day) \
                           - timedelta(hours=(24 - cfg["hour_start"]))
            until        = datetime(ref_date.year, ref_date.month, ref_date.day,
                                    cfg["hour_end"])
            days_to_load = [yest_str, day]
        else:
            since        = datetime(ref_date.year, ref_date.month, ref_date.day,
                                    cfg["hour_start"])
            until        = datetime(ref_date.year, ref_date.month, ref_date.day,
                                    cfg["hour_end"])
            days_to_load = [day]

        all_articles = []
        for d in days_to_load:
            f = self.articles_dir / f"{d}_articles.json"
            if f.exists():
                try:
                    with open(f, encoding="utf-8") as fp:
                        all_articles.extend(json.load(fp))
                except Exception as e:
                    self.log.warning(f"Lecture {f} : {e}")

        valid = []
        for a in all_articles:
            if a.get("migrated"):
                continue
            summary = a.get("summary", "")
            if not summary or summary.startswith("["):
                continue
            try:
                ts = datetime.fromisoformat(a["timestamp"])
                if since <= ts <= until:
                    valid.append(a)
            except Exception:
                continue

        valid.sort(key=lambda a: a.get("timestamp", ""))
        return valid

    # ── Construction du prompt ────────────────────────────────────────────────

    def _build_prompt(self, edition_name: str, articles: list, day: str) -> str:
        cfg     = EDITIONS[edition_name]
        date_fr = self._format_date_fr(day)

        by_cat = {}
        for a in articles:
            by_cat.setdefault(a.get("category", "monde"), []).append(a)

        context_parts = []
        for cat in CAT_ORDER:
            arts = by_cat.get(cat, [])
            if not arts:
                continue
            summaries = [
                f"  · [{a.get('source','')}] {a.get('summary','')[:160]}"
                for a in arts[:3]
            ]
            context_parts.append(
                f"[{CAT_LABELS.get(cat, cat.upper())} — {len(arts)} articles]\n"
                + "\n".join(summaries)
            )

        context    = "\n\n".join(context_parts[:8])
        sources    = list({a.get("source","") for a in articles if a.get("source")})[:8]
        nb         = len(articles)
        lang_label = "français" if self.language == "fr" else self.language

        return (
            f"Tu es grand reporter pour un média international sérieux. "
            f"Rédige la {cfg['label']} du {date_fr} en {lang_label} exclusivement.\n\n"
            f"INFORMATIONS DISPONIBLES ({nb} articles) :\n"
            f"Sources : {', '.join(sources)}\n\n"
            f"{context}\n\n"
            f"INSTRUCTIONS DE RÉDACTION :\n"
            f"Longueur : EXACTEMENT {cfg['words']} mots. Développe chaque sujet en détail.\n"
            f"Langue : {lang_label} UNIQUEMENT, sauf noms propres.\n"
            f"Style : {cfg['tone']}\n"
            f"Structure obligatoire :\n"
            f"  1. TITRE en majuscules (1 ligne, percutant, résume le fil rouge)\n"
            f"  2. Paragraphe d'accroche (2-3 phrases, pose le contexte global)\n"
            f"  3. Développements thématiques (4-6 paragraphes, 1 sujet par paragraphe)\n"
            f"  4. Conclusion analytique (2-3 phrases, perspective et enjeux à venir)\n"
            f"Règles absolues :\n"
            f"  - Texte continu, aucun bullet point, aucun sous-titre\n"
            f"  - Transitions soignées entre les paragraphes\n"
            f"  - Cite les sources en fin de phrase entre parenthèses\n"
            f"  - Chiffres précis quand disponibles\n"
            f"  - Jamais de 'Dans cette édition', 'Il convient de noter'\n"
            f"  - Jamais de première personne ni de majuscules intempestives\n\n"
            f"Commence MAINTENANT par le TITRE puis le texte sans introduction :"
        )

    # ── Sauvegarde ────────────────────────────────────────────────────────────

    def _save(self, edition_name: str, text: str, day: str,
              article_count: int) -> str:
        cfg     = EDITIONS[edition_name]
        date_fr = self._format_date_fr(day)
        now_str = datetime.now().strftime("%H:%M")

        # Récupère le nom du service depuis la config si disponible
        svc_name = self.config.get("service", {}).get("name", "Nova Atlas")
        content = (
            f"# {cfg['emoji']} {cfg['label']} — {date_fr}\n\n"
            f"*{article_count} articles analysés · généré à {now_str} · {self.model}*\n\n"
            f"---\n\n"
            f"{text}\n"
        )

        out = self.editions_dir / f"{day}_{edition_name}.md"
        out.write_text(content, encoding="utf-8")
        return str(out)

    # ── Entry point ───────────────────────────────────────────────────────────

    def generate(self, edition_name: str = "auto",
                 day: Optional[str] = None) -> str:
        """
        Génère une édition. Retourne le chemin du fichier Markdown.
        edition_name : 'matin' | 'midi' | 'soir' | 'auto'
        """
        if edition_name == "auto":
            edition_name = self.detect_current_edition()

        if edition_name not in EDITIONS:
            self.log.error(f"Édition inconnue : {edition_name}")
            return ""

        if day is None:
            day = datetime.now().strftime("%Y%m%d")

        cfg     = EDITIONS[edition_name]
        date_fr = self._format_date_fr(day)
        self.log.info(f"Génération {cfg['label']} — {date_fr}")

        articles = self.get_articles_for_edition(edition_name, day)
        self.log.info(
            f"{len(articles)} articles · fenêtre "
            f"{cfg['hour_start']}h→{cfg['hour_end']}h"
        )

        if not articles:
            self.log.warning(f"Aucun article pour {edition_name} — placeholder")
            return self._save(
                edition_name,
                "*Aucun article disponible pour cette édition. La collecte est en cours...*",
                day, 0
            )

        prompt = self._build_prompt(edition_name, articles, day)
        self.log.info(
            f"Prompt {edition_name} : {len(prompt)} chars · "
            f"modèle={self.model} · timeout={self.timeout}s"
        )

        text = self._ollama_call(prompt)
        if not text:
            self.log.error("Ollama n'a pas produit de texte")
            return ""

        out = self._save(edition_name, text, day, len(articles))
        self.log.info(f"Édition sauvegardée : {out} ({len(text)} chars)")
        return out
