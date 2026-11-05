#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/core/feeds_loader.py — Nova-Atlas
Charge la liste des flux RSS depuis config/feeds.yaml.

Format du fichier :
    geopolitique:
      - https://www.france24.com/fr/rss
      - https://...
    # [SPRINT0] https://example.com/dead-feed      # commentaire = désactivé

Les URLs en commentaire (lignes commençant par #, même avec [SPRINT0])
sont ignorées. Seules les URLs actives sont retournées.

L'API expose une fonction load_feeds(path) qui retourne un dict
{category: [url1, url2, ...]} filtré.
"""

import re
from pathlib import Path
from typing import Dict, List


def _legacy_load_feeds(feeds_path: str | Path) -> Dict[str, List[str]]:
    """
    Parse le fichier feeds.yaml "à la main" (pas de dépendance yaml lourde).
    On extrait juste les URLs actives par catégorie.

    Gère :
      - lignes vides / commentaires seuls
      - URLs préfixées par "# " (désactivées) — on les ignore
      - URLs préfixées par "# [SPRINT0] " (notre convention)
      - URLs multi-ligne (avec > ou | yaml fold) — non géré, format simple uniquement

    Si le fichier n'existe pas → retourne un dict vide.
    Si le fichier est malformé → log warning et retourne ce qui a pu être parsé.
    """
    feeds_path = Path(feeds_path)
    if not feeds_path.exists():
        return {}

    feeds: Dict[str, List[str]] = {}
    current_cat: str | None = None
    bad_lines: list[str] = []

    for raw_line in feeds_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        # Skip empty + pure comment lines
        if not stripped or stripped.startswith("#"):
            continue
        # Top-level category : "category:" (no leading whitespace)
        if not line.startswith(" ") and stripped.endswith(":"):
            current_cat = stripped[:-1].strip()
            feeds.setdefault(current_cat, [])
            continue
        # List item : "  - https://..." or "  - # [SPRINT0] https://..."
        if stripped.startswith("- "):
            url = stripped[2:].strip()
            # Separe l'URL des commentaires inline (style "url  # comment").
            # On garde uniquement la partie avant "  #" (au moins 2 espaces).
            if "  #" in url:
                url = url.split("  #", 1)[0].strip()
            # Skip commented-out items (start with # or contain [SPRINT0])
            if url.startswith("#") or "[SPRINT0]" in url:
                continue
            if current_cat is None:
                bad_lines.append(raw_line)
                continue
            feeds[current_cat].append(url)
            continue
        # Anything else → log as malformed
        bad_lines.append(raw_line)

    if bad_lines:
        import logging
        logging.getLogger("nova.feeds").warning(
            "%d lignes malformées dans le fichier de flux (contenu masqué)", len(bad_lines)
        )
    return feeds


class FeedSnapshot(dict):
    """Retains the version for safe legacy read/edit/save callers."""
    version = None


def load_feeds(feeds_path):
    from modules.core.source_manager import SourceManager, SourceError
    try:
        doc=SourceManager(feeds_path).read()
        result=FeedSnapshot({cat:[s.url for s in doc.sources if s.category==cat and s.enabled] for cat in doc.categories})
        result.version=doc.version
        return result
    except SourceError:
        # Historical read-only compatibility: warn/partial legacy data; never used to mutate.
        import logging
        logging.getLogger('nova.feeds').warning('Liste malformée : lecture historique dégradée, modification interdite')
        return _legacy_load_feeds(feeds_path)


def _parse_disabled_feeds(text):
    from modules.core.source_manager import parse_document
    doc=parse_document(text)
    return ({cat:[s.url for s in doc.sources if s.category==cat and not s.enabled] for cat in doc.categories},
            {cat:[s.url for s in doc.sources if s.category==cat and s.enabled] for cat in doc.categories})


def save_feeds(feeds_path, feeds):
    from modules.core.source_manager import SourceManager
    SourceManager(feeds_path).save_active(feeds)


def add_feed(feeds: Dict[str, List[str]], category: str, url: str) -> bool:
    """Ajoute un flux. Renvoie True si ajouté, False si déjà présent."""
    if category not in feeds:
        feeds[category] = []
    if url in feeds[category]:
        return False
    feeds[category].append(url)
    return True


def remove_feed(feeds: Dict[str, List[str]], category: str, url: str) -> bool:
    """Retire un flux. Renvoie True si retiré, False si pas trouvé."""
    if category not in feeds:
        return False
    if url not in feeds[category]:
        return False
    feeds[category].remove(url)
    # Nettoie la catégorie si vide
    if not feeds[category]:
        del feeds[category]
    return True


def toggle_feed(feeds: Dict[str, List[str]], category: str, url: str) -> str | None:
    """
    Toggle l'état actif d'un flux. Renvoie le nouvel état ('active'|'removed'|None).
    None = pas trouvé.
    """
    if category in feeds and url in feeds[category]:
        feeds[category].remove(url)
        if not feeds[category]:
            del feeds[category]
        return "removed"
    return None


def move_feed(feeds: Dict[str, List[str]], category: str, url: str,
              direction: str) -> bool:
    """
    Déplace un flux d'un cran dans la liste. Direction = 'up' ou 'down'.
    Renvoie True si déplacé, False si pas trouvé ou déjà au bord.
    """
    if category not in feeds or url not in feeds[category]:
        return False
    lst = feeds[category]
    idx = lst.index(url)
    if direction == "up" and idx > 0:
        lst[idx], lst[idx - 1] = lst[idx - 1], lst[idx]
        return True
    if direction == "down" and idx < len(lst) - 1:
        lst[idx], lst[idx + 1] = lst[idx + 1], lst[idx]
        return True
    return False


def reorder_feeds(feeds: Dict[str, List[str]], category: str,
                  order: List[str]) -> bool:
    """
    Réordonne toute la catégorie selon la liste 'order' (liste d'URLs dans
    le nouvel ordre). Renvoie True si OK, False si les URLs ne matchent pas.
    """
    if category not in feeds:
        return False
    current = set(feeds[category])
    target = set(order)
    if current != target or len(order) != len(feeds[category]):
        return False
    feeds[category] = list(order)
    return True


def add_category(feeds: Dict[str, List[str]], category: str) -> bool:
    """
    Crée une catégorie vide si elle n'existe pas. Renvoie True si créée,
    False si elle existait déjà.
    """
    if category in feeds:
        return False
    feeds[category] = []
    return True
