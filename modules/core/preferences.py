#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/core/preferences.py — Nova-Atlas
Préférences utilisateur pour le filtrage par catégorie.

Pour l'instant (MVP) : un seul user, pas d'auth. On stocke les
préférences dans data/preferences.json. Les catégories cochées par
défaut = toutes.

Format du fichier :
    {
      "hidden_categories": ["sport", "gaming", ...],
      "updated_at": "2026-07-06T15:30:00"
    }
"""

import json
import time
from pathlib import Path
from typing import Dict, List


DEFAULT_PREFS = {
    "hidden_categories": [],   # vide = tout est visible
    "updated_at": "1970-01-01T00:00:00",
}


def load_prefs(prefs_path: str | Path) -> Dict:
    """
    Charge les préférences depuis le fichier JSON.
    Si le fichier n'existe pas ou est corrompu, retourne les défauts.
    """
    prefs_path = Path(prefs_path)
    if not prefs_path.exists():
        return dict(DEFAULT_PREFS)
    try:
        data = json.loads(prefs_path.read_text(encoding="utf-8"))
        # Validation basique
        if not isinstance(data, dict):
            return dict(DEFAULT_PREFS)
        if "hidden_categories" not in data:
            data["hidden_categories"] = []
        if not isinstance(data["hidden_categories"], list):
            data["hidden_categories"] = []
        return data
    except Exception:
        return dict(DEFAULT_PREFS)


def save_prefs(prefs_path: str | Path, prefs: Dict) -> None:
    """Sauve les préférences. Écriture atomique (tempfile + rename)."""
    prefs_path = Path(prefs_path)
    prefs = dict(prefs)
    prefs["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    import tempfile, os
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(prefs_path.parent), prefix=f".{prefs_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2, ensure_ascii=False)
        os.replace(tmp_name, prefs_path)
    except Exception:
        try: os.unlink(tmp_name)
        except OSError: pass
        raise


def is_hidden(prefs: Dict, category: str) -> bool:
    """Une catégorie est cachée si elle est dans hidden_categories."""
    return category in prefs.get("hidden_categories", [])


def toggle_category(prefs: Dict, category: str) -> bool:
    """
    Toggle l'état caché d'une catégorie. Renvoie le nouvel état
    (True = cachée, False = visible).
    """
    hidden = prefs.setdefault("hidden_categories", [])
    if category in hidden:
        hidden.remove(category)
        return False
    else:
        hidden.append(category)
        return True
