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
    current_cat: str | None = None
    # On scanne ligne par ligne en détectant les 2 patterns
    # Pattern 1: item désactivé "  - # [SPRINT0] ..."
    # Pattern 2: commentaire orphelin "  # [SPRINT0] ..."
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        # Catégorie de niveau 0
        if not raw_line.startswith(" ") and stripped.endswith(":"):
            current_cat = stripped[:-1].strip()
            continue
        # Pattern 1 : "- # ..."
        if stripped.startswith("- #"):
            url = stripped[2:].lstrip("#").strip()
            if url.startswith("[SPRINT0]"):
                url = url[len("[SPRINT0]"):].strip()
            if current_cat and url.startswith(("http://", "https://")):
                disabled.setdefault(current_cat, []).append(url)
            continue
        # Pattern 2 : commentaire orphelin "# [SPRINT0] https://..."
        # On accepte aussi "# <URL>" simple si ça ressemble à un flux
        if stripped.startswith("#"):
            comment_body = stripped.lstrip("#").strip()
            if comment_body.startswith("[SPRINT0]"):
                comment_body = comment_body[len("[SPRINT0]"):].strip()
            # Vérifie que c'est bien une URL (pas un commentaire textuel)
            if current_cat and comment_body.startswith(("http://", "https://")):
                disabled.setdefault(current_cat, []).append(comment_body)
    return disabled


def save_feeds(feeds_path: str | Path, feeds: Dict[str, List[str]]) -> None:
    """
    Écrit le dict feeds dans feeds.yaml (atomique via tempfile+rename).
    Format : hierarchique, comme load_feeds() le parse.

    Préserve automatiquement les URLs désactivées (commentées) qui étaient
    dans le fichier avant : on les relit, on les réécrit. Aucune perte
    d'info au save.
    """
    feeds_path = Path(feeds_path)

    # Récupère les désactivés avant d'écraser
    disabled: Dict[str, List[str]] = {}
    if feeds_path.exists():
        try:
            disabled = _parse_disabled_feeds(feeds_path.read_text(encoding="utf-8"))
        except Exception:
            pass

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
        # Si la catégorie n'a que des flux morts, on l'écrit quand même
        if not active and not dead:
            continue
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
