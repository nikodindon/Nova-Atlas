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


def load_feeds(feeds_path: str | Path) -> Dict[str, List[str]]:
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
            f"{len(bad_lines)} lignes malformées dans {feeds_path}: {bad_lines[:3]}"
        )
    return feeds


def _parse_disabled_feeds(text: str) -> Dict[str, List[str]]:
    """
    Parse les URLs désactivées (commentées) du fichier feeds.yaml.
    Deux conventions sont supportées :
      1) "- # [SPRINT0] https://..."  (item de liste préfixé par #)
      2) "# [SPRINT0] https://..."      (commentaire orphelin après un item,
                                          format historique du projet)
    Dans les deux cas, l'URL ne doit PAS apparaître comme active.
    """
    disabled: Dict[str, List[str]] = {}
    active_in_source: Dict[str, List[str]] = {}
    current_cat: str | None = None
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        # Catégorie de niveau 0
        if not raw_line.startswith(" ") and stripped.endswith(":"):
            current_cat = stripped[:-1].strip()
            continue
        # Pattern 1 : item désactivé "- # ..."
        if stripped.startswith("- #"):
            url = stripped[2:].lstrip("#").strip()
            if url.startswith("[SPRINT0]"):
                url = url[len("[SPRINT0]"):].strip()
            if current_cat and url.startswith(("http://", "https://")):
                disabled.setdefault(current_cat, []).append(url)
            continue
        # Pattern 2 : commentaire orphelin "# [SPRINT0] https://..."
        if stripped.startswith("#"):
            comment_body = stripped.lstrip("#").strip()
            if comment_body.startswith("[SPRINT0]"):
                comment_body = comment_body[len("[SPRINT0]"):].strip()
            if current_cat and comment_body.startswith(("http://", "https://")):
                disabled.setdefault(current_cat, []).append(comment_body)
            continue
        # Item actif "- https://..."
        if stripped.startswith("- ") and current_cat:
            url = stripped[2:].strip()
            if url.startswith(("http://", "https://")):
                active_in_source.setdefault(current_cat, []).append(url)
    return disabled, active_in_source


def save_feeds(feeds_path: str | Path, feeds: Dict[str, List[str]]) -> None:
    """
    Écrit le dict feeds dans feeds.yaml (atomique via tempfile+rename).
    Format : hierarchique, comme load_feeds() le parse.

    Préserve automatiquement les URLs désactivées qui étaient dans le
    fichier source. Si une URL était active dans la source mais absente
    de `feeds[cat]`, elle est ajoutée à disabled (cas du toggle).
    """
    feeds_path = Path(feeds_path)

    # Récupère les désactivés + les actifs du source AVANT d'écraser
    disabled: Dict[str, List[str]] = {}
    active_in_source: Dict[str, List[str]] = {}
    if feeds_path.exists():
        try:
            disabled, active_in_source = _parse_disabled_feeds(
                feeds_path.read_text(encoding="utf-8")
            )
        except Exception:
            pass

    # Si une URL était active dans la source mais n'est plus dans `feeds`,
    # c'est qu'on l'a retirée → elle devient disabled.
    for cat, urls in active_in_source.items():
        current_active = set(feeds.get(cat, []))
        for url in urls:
            if url not in current_active and url not in disabled.get(cat, []):
                disabled.setdefault(cat, []).append(url)

    # Si une URL est revenue dans les actifs, on la retire de disabled
    # (cas du re-activate : add_feed après un disable).
    for cat, urls in list(disabled.items()):
        active_set = set(feeds.get(cat, []))
        disabled[cat] = [u for u in urls if u not in active_set]
        if not disabled[cat]:
            del disabled[cat]

    lines: list[str] = []
    # Header
    lines.append("# ================================================")
    lines.append("# NOVA-ATLAS — Liste des flux RSS")
    lines.append("# ================================================")
    lines.append("# Édité via /config/feeds. Pour les commentaires,")
    lines.append("# utilise le préfixe '# ' devant l'URL.")
    lines.append("# Rechargement à chaud : POST /config/restart")
    lines.append("# ================================================")
    lines.append("")

    # Toutes les catégories = union(active + disabled), dans l'ordre
    all_cats = list(feeds.keys())
    for cat in disabled.keys():
        if cat not in all_cats:
            all_cats.append(cat)

    for cat in all_cats:
        active = feeds.get(cat, [])
        dead = disabled.get(cat, [])
        # On écrit toujours la catégorie (même vide, pour qu'elle apparaisse
        # dans l'UI). Le skip "if not active and not dead" supprimait les
        # catégories nouvellement créées par l'utilisateur.
        lines.append(f"{cat}:")
        for url in active:
            lines.append(f"  - {url}")
        for url in dead:
            lines.append(f"  # [SPRINT0] {url}")
        lines.append("")

    content = "\n".join(lines)
    # Écriture atomique
    import tempfile, os
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(feeds_path.parent), prefix=f".{feeds_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, feeds_path)
    except Exception:
        try: os.unlink(tmp_name)
        except OSError: pass
        raise


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
    if current != target:
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
