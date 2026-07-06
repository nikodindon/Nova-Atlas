#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_tts_clean.py — Tests de clean_for_tts() et de l'intégration au journal radio.

Couvre :
  - clean_for_tts : tous les artefacts markdown courants
  - Préservation des caractères français
  - _build_script : pas d'astérisques, '#', '`', 'http' dans le résultat final
"""

import pytest

from modules.utils.helpers import clean_for_tts
from modules.radio.journal_builder import _build_script


# ─── clean_for_tts : tests unitaires ─────────────────────────────────────────

class TestCleanForTTS:
    def test_empty(self):
        assert clean_for_tts("") == ""
        assert clean_for_tts(None) == ""

    def test_bold_double_asterisk(self):
        """Le bug rapporté : 'astérisque astérisque' au milieu des phrases."""
        assert clean_for_tts("**Trump** rencontre **Macron**") == "Trump rencontre Macron"

    def test_italic_single_asterisk(self):
        assert clean_for_tts("*italique*") == "italique"

    def test_italic_underscore(self):
        assert clean_for_tts("_italique_") == "italique"

    def test_bold_underscore(self):
        assert clean_for_tts("__gras__") == "gras"

    def test_combined(self):
        assert clean_for_tts("***gras italique***") == "gras italique"

    def test_inline_code(self):
        assert clean_for_tts("`du code`") == "du code"
        assert clean_for_tts("``du code``") == "du code"

    def test_code_block(self):
        assert clean_for_tts("```python\nprint('hi')\n```") == ""

    def test_heading(self):
        assert clean_for_tts("# Titre") == "Titre"
        assert clean_for_tts("### Sous-titre") == "Sous-titre"

    def test_bullet_list(self):
        assert clean_for_tts("- item 1\n- item 2") == "item 1\nitem 2"
        assert clean_for_tts("* item\n+ item") == "item\nitem"

    def test_numbered_list(self):
        assert clean_for_tts("1. premier\n2. deuxième") == "premier\ndeuxième"

    def test_link(self):
        assert clean_for_tts("[Le Monde](https://lemonde.fr)") == "Le Monde"

    def test_image(self):
        assert clean_for_tts("![alt](url.png)") == "alt"

    def test_blockquote(self):
        assert clean_for_tts("> citation") == "citation"

    def test_separator(self):
        assert clean_for_tts("---") == ""
        assert clean_for_tts("***") == ""
        assert clean_for_tts("___") == ""

    def test_french_chars_preserved(self):
        """Les accents, guillemets et apostrophes FR doivent passer."""
        txt = "L'Élysée : « il fait beau » à Paris, c'est sûr."
        out = clean_for_tts(txt)
        assert txt == out

    def test_paragraphs_normalized(self):
        assert "Salut\n\nça va" == clean_for_tts("Salut\n\n\n\n\nça va")

    def test_realistic_summary(self):
        """Le scénario du bug rapporté : LLM qui renvoie du markdown."""
        summary = (
            "**Donald Trump** et *Emmanuel Macron* se sont rencontrés à l'Élysée. "
            "Le président a déclaré : « Nous devons *agir maintenant* ». "
            "Plus d'infos sur [le site](https://example.com)."
        )
        out = clean_for_tts(summary)
        assert "**" not in out
        assert "*" not in out
        assert "https://" not in out
        assert "agir maintenant" in out
        assert "Donald Trump" in out
        assert "Emmanuel Macron" in out


# ─── _build_script : test d'intégration ──────────────────────────────────────

class TestBuildScript:
    """Vérifie que le script radio généré ne contient pas d'artefacts markdown."""

    def test_script_no_asterisks(self):
        articles = [
            {"summary": "**Gras** et *italique* dans le résumé"},
            {"summary": "Encore du **markdown** ici."},
        ]
        script, n = _build_script(articles)
        assert n == 2
        assert "*" not in script

    def test_script_no_headings(self):
        articles = [
            {"summary": "## Titre de l'article\nDu contenu"},
        ]
        script, n = _build_script(articles)
        assert "#" not in script
        assert "Titre de l'article" in script
        assert "Du contenu" in script

    def test_script_no_backticks(self):
        articles = [
            {"summary": "Voici du `code` dans le résumé."},
        ]
        script, n = _build_script(articles)
        assert "`" not in script
        assert "code" in script

    def test_script_no_links(self):
        articles = [
            {"summary": "Suite sur [le Monde](https://lemonde.fr/article)."},
        ]
        script, n = _build_script(articles)
        assert "http" not in script
        assert "le Monde" in script

    def test_script_preserves_french(self):
        articles = [
            {"summary": "L'Élysée : « il fait beau » à Paris."},
        ]
        script, n = _build_script(articles)
        assert "L'Élysée" in script
        assert "« il fait beau »" in script

    def test_empty_summary_skipped(self):
        """Un résumé qui devient vide après nettoyage est sauté."""
        articles = [
            {"summary": "Résumé valide"},
            {"summary": "---"},          # devient vide après clean
            {"summary": "Autre résumé"},
        ]
        script, n = _build_script(articles)
        # n = nb_articles_valides, mais ceux qui deviennent vide sont skippés
        assert "Résumé valide" in script
        assert "Autre résumé" in script

    def test_real_world_mixed(self):
        """Le cas typique : un mix de LLM avec/sans markdown."""
        articles = [
            {"summary": "**Trump** rencontre *Macron* à Paris."},
            {"summary": "Le CAC 40 gagne `+2.3%` aujourd'hui."},
            {"summary": "## Football\n- PSG 2 - 2 OM\n- Match nul"},
        ]
        script, n = _build_script(articles)
        # Pas un seul artefact markdown dans le résultat
        for forbidden in ["*", "#", "`", "http"]:
            assert forbidden not in script, f"'{forbidden}' trouvé dans : {script[:200]}"
        # Contenu préservé
        assert "Trump" in script
        assert "Macron" in script
        assert "+2.3%" in script
        assert "PSG 2" in script
