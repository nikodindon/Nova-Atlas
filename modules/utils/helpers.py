#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/utils/helpers.py — Fonctions utilitaires diverses.
"""

from datetime import datetime


def format_date_fr(dt: datetime) -> str:
    """Formate une date en français (ex: 'lundi 7 juillet 2026')."""
    jours = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    mois  = ["janvier", "février", "mars", "avril", "mai", "juin",
             "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    return f"{jours[dt.weekday()]} {dt.day} {mois[dt.month - 1]} {dt.year}"


def format_heure(dt: datetime) -> str:
    """Formate une heure en français (ex: '15h30')."""
    return f"{dt.hour}h{dt.minute:02d}"


def clean_ansi(text: str) -> str:
    """Supprime les codes ANSI des sorties console."""
    import re
    text = re.sub(r'\x1b\[[0-9;]*[mGKHF]', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text.strip()


def clean_for_tts(text: str) -> str:
    """
    Nettoie un texte pour la synthèse vocale (TTS).
    Supprime le markdown et autres artefacts qui seraient lus tels quels :
      - **bold**, *italic*, __bold__, _italic_  → texte
      - `code`, ```bloc```  → texte
      - # titres, ## sous-titres  → texte
      - [lien](url)  → texte du lien
      - listes à puces (-, *, +)  → retire le préfixe
    Garde les accents, apostrophes, guillemets français, sauts de ligne.
    """
    import re
    if not text:
        return ""
    t = str(text)
    # Séparateurs ***, ---, ___ sur leur propre ligne → retire
    # IMPORTANT : avant les patterns bold/italic, sinon *** serait interprété
    # comme "contenu vide entre bold delimiters" et laisserait 1 *
    t = re.sub(r'^\s*\*{3,}\s*$', '', t, flags=re.MULTILINE)
    t = re.sub(r'^\s*-{3,}\s*$', '', t, flags=re.MULTILINE)
    t = re.sub(r'^\s*_{3,}\s*$', '', t, flags=re.MULTILINE)
    # Liens markdown [texte](url) → texte seul
    # (?<!!) : pas précédé d'un !, pour ne pas matcher les images ![alt](url)
    t = re.sub(r'(?<!!)\[([^\]]+)\]\([^\)]+\)', r'\1', t)
    # Images ![alt](url) → alt seul
    t = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', r'\1', t)
    # Bold/italic ***texte***, **texte**, *texte*
    t = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', t, flags=re.DOTALL)
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t, flags=re.DOTALL)
    t = re.sub(r'\*(.+?)\*', r'\1', t, flags=re.DOTALL)
    # Bold/italic ___texte___, __texte__, _texte_
    t = re.sub(r'___(.+?)___', r'\1', t, flags=re.DOTALL)
    t = re.sub(r'__(.+?)__', r'\1', t, flags=re.DOTALL)
    t = re.sub(r'_(.+?)_', r'\1', t, flags=re.DOTALL)
    # Blocs code ```...``` → retire (AVANT l'inline code, sinon le pattern
    # inline ``code`` matche le 1er ``` et laisse 2 backticks)
    t = re.sub(r'```[\s\S]+?```', '', t)
    # Inline code `texte` ou ``texte`` → texte
    t = re.sub(r'`{1,2}([^`]+)`{1,2}', r'\1', t)
    # Titres markdown (#, ##, ###...)
    t = re.sub(r'^#{1,6}\s+', '', t, flags=re.MULTILINE)
    # Listes à puces (-, *, +) en début de ligne
    t = re.sub(r'^\s*[-*+]\s+', '', t, flags=re.MULTILINE)
    # Listes numérotées (1. 2. ...)
    t = re.sub(r'^\s*\d+\.\s+', '', t, flags=re.MULTILINE)
    # Blockquotes > texte
    t = re.sub(r'^\s*>\s*', '', t, flags=re.MULTILINE)
    # Espaces multiples et sauts de ligne
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = re.sub(r'[ \t]+', ' ', t)
    return t.strip()
