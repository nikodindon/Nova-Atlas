#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/report/atlas_report.py — Nova-Atlas
Génère le grand rapport narratif quotidien (lancé à 23h).

Refactorisé depuis atlas_report.py (pblart/nova-media) :
  - Enveloppé dans ReportGenerator(config)
  - Les chemins viennent de config.paths.*
  - Ollama passe par modules.core.ollama
  - Toute la logique de génération (intro, sections, conclusion) est conservée
"""

import json
import logging
import os
from datetime import datetime, date
from pathlib import Path

from modules.core.ollama import init_ollama, ollama_call, get_language, get_model

CATEGORY_LABELS = {
    "geopolitique":  "Géopolitique",
    "economie":      "Économie",
    "crypto":        "Crypto",
    "tech":          "Tech & IA",
    "france":        "France",
    "monde":         "Monde",
    "science":       "Science & Santé",
    "environnement": "Environnement",
    "societe":       "Société & Droits",
    "culture":       "Culture & Arts",
    "sport":         "Sport",
}

CATEGORY_ORDER = [
    "geopolitique","economie","france","monde","crypto",
    "tech","science","environnement","societe","culture","sport"
]


class ReportGenerator:
    def __init__(self, config: dict):
        self.log = logging.getLogger("nova.report")
        self._apply_config(config)
        init_ollama(config)

    def _apply_config(self, config: dict):
        paths = config.get("paths", {})
        self.articles_dir = Path(paths.get("articles_dir", "data/articles"))
        self.reports_dir  = Path(paths.get("reports_dir",  "data/reports"))
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = int(config.get("llm", {}).get("timeout_report", 600))

    def reload_config(self, config: dict):
        """Recharge la config à chaud."""
        self._apply_config(config)
        from modules.core.ollama import init_ollama as _init
        _init(config)
        self.log.info("[REPORT] Config rechargée à chaud.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _format_date_fr(day: str) -> str:
        months  = ["janvier","février","mars","avril","mai","juin",
                   "juillet","août","septembre","octobre","novembre","décembre"]
        days_fr = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
        d = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
        return f"{days_fr[d.weekday()]} {d.day} {months[d.month-1]} {d.year}"

    def _load_articles(self, day: str) -> list:
        f = self.articles_dir / f"{day}_articles.json"
        if not f.exists():
            return []
        with open(f, encoding="utf-8") as fp:
            return json.load(fp)

    def _ollama(self, prompt: str) -> str:
        return ollama_call(prompt, timeout=self.timeout, caller="report")

    # ── Génération des sections (identique à atlas_report.py) ─────────────────

    def _generate_section(self, category: str, articles: list, date_fr: str) -> str:
        label     = CATEGORY_LABELS.get(category, category)
        lang      = get_language()
        summaries = "\n".join(
            f"- [{a['source']}] {a['title']} : {a['summary']}"
            for a in articles[:12]
        )
        prompt = (
            f"Tu es un journaliste analytique de haut niveau. Réponds en {lang} uniquement. "
            f"Voici les faits marquants du jour ({date_fr}) dans la catégorie '{label}'.\n\n"
            f"{summaries}\n\n"
            f"Rédige un paragraphe narratif dense de 150 à 250 mots qui :\n"
            f"- Synthétise les événements les plus significatifs\n"
            f"- Établit des liens entre les faits quand c'est pertinent\n"
            f"- Utilise un style journalistique littéraire, fluide, sans bullet points\n"
            f"- Évite les répétitions et les formules creuses\n"
            f"- N'utilise pas la première personne\n"
            f"- Commence directement par les faits, sans titre ni introduction\n\n"
            f"Paragraphe :"
        )
        return self._ollama(prompt)

    def _generate_intro(self, date_fr: str, all_summaries: str, article_count: int) -> str:
        lang = get_language()
        prompt = (
            f"Tu es un grand reporter. Rédige en {lang} uniquement. "
            f"Introduction du rapport mondial du {date_fr}.\n\n"
            f"Aperçu des événements du jour ({article_count} articles traités) :\n"
            f"{all_summaries[:3000]}\n\n"
            f"Rédige une introduction de 200 à 300 mots qui :\n"
            f"- Capte immédiatement l'attention par une image ou un fait fort\n"
            f"- Donne le ton général de la journée\n"
            f"- Annonce les grandes lignes sans tout révéler\n"
            f"- A le style d'un chapitre d'ouverture d'un roman journalistique\n"
            f"- N'utilise pas la première personne\n"
            f"- Commence par une phrase d'accroche puissante\n\n"
            f"Introduction :"
        )
        return self._ollama(prompt)

    def _generate_conclusion(self, date_fr: str, section_texts: dict) -> str:
        lang = get_language()
        synthesis = "\n".join(
            f"[{CATEGORY_LABELS.get(cat, cat)}] {text[:300]}..."
            for cat, text in section_texts.items() if text
        )
        prompt = (
            f"Tu es un éditorialiste international. Rédige en {lang} uniquement. "
            f"Grandes lignes du {date_fr}.\n\n"
            f"{synthesis[:4000]}\n\n"
            f"Rédige une conclusion analytique de 250 à 350 mots qui :\n"
            f"- Identifie les tendances profondes de la journée\n"
            f"- Fait des liens entre les différents domaines\n"
            f"- Propose une lecture du monde actuel sans idéologie dominante\n"
            f"- Se termine par une question ouverte ou une perspective\n"
            f"- N'utilise pas la première personne\n"
            f"- Ne dit pas 'en conclusion' ou 'pour conclure'\n\n"
            f"Conclusion :"
        )
        return self._ollama(prompt)

    # ── Point d'entrée ────────────────────────────────────────────────────────

    def generate(self, day: str = None) -> str:
        """
        Génère et sauvegarde le rapport complet du jour.
        Retourne le chemin du fichier .md généré.
        Équivalent de generate_daily_report() dans atlas_report.py.
        """
        if day is None:
            day = datetime.now().strftime("%Y%m%d")

        self.log.info(f"Génération rapport pour le {day}")
        articles = self._load_articles(day)
        if not articles:
            self.log.error(f"Aucun article trouvé pour {day}")
            return ""

        date_fr = self._format_date_fr(day)
        by_cat  = {}
        for a in articles:
            by_cat.setdefault(a.get("category", "monde"), []).append(a)

        self.log.info(f"{len(articles)} articles dans {len(by_cat)} catégories")

        all_summaries = "\n".join(
            f"- {a['title']} : {a['summary'][:100]}"
            for a in articles[:40]
        )

        self.log.info("Génération introduction...")
        intro = self._generate_intro(date_fr, all_summaries, len(articles))

        sections = {}
        for cat in CATEGORY_ORDER:
            if cat in by_cat and by_cat[cat]:
                self.log.info(f"Section {cat} ({len(by_cat[cat])} articles)...")
                sections[cat] = self._generate_section(cat, by_cat[cat], date_fr)

        self.log.info("Génération conclusion...")
        conclusion = self._generate_conclusion(date_fr, sections)

        # Assemblage
        lines = [
            f"# {date_fr.upper()}",
            f"*Rapport Nova-Atlas — {len(articles)} articles traités*",
            "", "---", "",
            intro, "",
        ]
        for cat in CATEGORY_ORDER:
            if cat in sections and sections[cat]:
                label = CATEGORY_LABELS.get(cat, cat)
                lines += [f"## {label}", "", sections[cat], ""]
        lines += [
            "---", "",
            "## Lecture du monde", "",
            conclusion, "",
            "---", "",
            f"*Nova-Atlas — généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}*",
            f"*Modèle : {get_model()} — Sources : {len(set(a['source'] for a in articles))} médias*",
        ]

        out = self.reports_dir / f"{day}_report.md"
        out.write_text("\n".join(lines), encoding="utf-8")
        self.log.info(f"Rapport sauvegardé : {out}")
        return str(out)
