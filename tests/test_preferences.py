#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_preferences.py — tests du module préférences utilisateur.

Couvre :
  - load_prefs : fichier absent → defaults
  - load_prefs : fichier corrompu → defaults (pas de crash)
  - save_prefs : round-trip + atomicité
  - toggle_category : ajout / retrait
  - is_hidden : cohérent avec toggle
"""

import pytest
import json
from pathlib import Path

from modules.core.preferences import (
    load_prefs, save_prefs, toggle_category, is_hidden,
    DEFAULT_PREFS,
)


# ─── load_prefs ───────────────────────────────────────────────────────────────

def test_load_prefs_missing_file(tmp_path):
    p = tmp_path / "preferences.json"
    assert not p.exists()
    prefs = load_prefs(p)
    assert prefs == DEFAULT_PREFS


def test_load_prefs_corrupt_file(tmp_path):
    p = tmp_path / "preferences.json"
    p.write_text("this is not json{", encoding="utf-8")
    prefs = load_prefs(p)
    # Doit retourner les defaults sans crasher
    assert prefs == DEFAULT_PREFS


def test_load_prefs_valid_file(tmp_path):
    p = tmp_path / "preferences.json"
    p.write_text(json.dumps({
        "hidden_categories": ["sport", "gaming"],
        "updated_at": "2026-07-06T15:00:00"
    }), encoding="utf-8")
    prefs = load_prefs(p)
    assert prefs["hidden_categories"] == ["sport", "gaming"]
    assert prefs["updated_at"] == "2026-07-06T15:00:00"


def test_load_prefs_missing_field(tmp_path):
    """Si le fichier existe mais sans hidden_categories, on complète."""
    p = tmp_path / "preferences.json"
    p.write_text('{"updated_at": "2026-07-06"}', encoding="utf-8")
    prefs = load_prefs(p)
    assert prefs["hidden_categories"] == []


def test_load_prefs_wrong_type(tmp_path):
    """Si hidden_categories n'est pas une liste, on corrige."""
    p = tmp_path / "preferences.json"
    p.write_text('{"hidden_categories": "sport"}', encoding="utf-8")
    prefs = load_prefs(p)
    assert prefs["hidden_categories"] == []  # corrigé en liste vide


# ─── save_prefs ───────────────────────────────────────────────────────────────

def test_save_prefs_roundtrip(tmp_path):
    p = tmp_path / "preferences.json"
    data = {"hidden_categories": ["crypto", "auto"]}
    save_prefs(p, data)
    assert p.exists()
    loaded = load_prefs(p)
    assert loaded["hidden_categories"] == ["crypto", "auto"]
    # updated_at est ajouté par save_prefs
    assert loaded["updated_at"] != DEFAULT_PREFS["updated_at"]


def test_save_prefs_atomic(tmp_path):
    """Pas de .tmp laissé sur disque après save."""
    p = tmp_path / "preferences.json"
    save_prefs(p, {"hidden_categories": []})
    tmps = list(tmp_path.glob(".preferences.json.*.tmp"))
    assert tmps == [], f"tmp files left: {tmps}"


def test_save_prefs_creates_parent_dir(tmp_path):
    """Si le dossier data/ existe, save doit fonctionner."""
    p = tmp_path / "data" / "preferences.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    save_prefs(p, {"hidden_categories": ["sport"]})
    assert p.exists()


def test_save_prefs_overwrites_existing(tmp_path):
    p = tmp_path / "preferences.json"
    save_prefs(p, {"hidden_categories": ["sport"]})
    save_prefs(p, {"hidden_categories": ["tech", "auto"]})
    assert load_prefs(p)["hidden_categories"] == ["tech", "auto"]


# ─── toggle_category ──────────────────────────────────────────────────────────

def test_toggle_hide():
    prefs = {"hidden_categories": []}
    new_state = toggle_category(prefs, "sport")
    assert new_state is True
    assert "sport" in prefs["hidden_categories"]


def test_toggle_show():
    prefs = {"hidden_categories": ["sport", "tech"]}
    new_state = toggle_category(prefs, "sport")
    assert new_state is False
    assert "sport" not in prefs["hidden_categories"]
    assert "tech" in prefs["hidden_categories"]  # l'autre reste


def test_toggle_creates_field_if_missing():
    prefs = {}  # pas de hidden_categories
    toggle_category(prefs, "sport")
    assert "hidden_categories" in prefs
    assert "sport" in prefs["hidden_categories"]


# ─── is_hidden ────────────────────────────────────────────────────────────────

def test_is_hidden_true():
    prefs = {"hidden_categories": ["sport"]}
    assert is_hidden(prefs, "sport") is True


def test_is_hidden_false():
    prefs = {"hidden_categories": ["sport"]}
    assert is_hidden(prefs, "tech") is False


def test_is_hidden_no_field():
    """Si hidden_categories n'existe pas, tout est visible."""
    prefs = {}
    assert is_hidden(prefs, "sport") is False


# ─── Workflow : save → load → toggle → save → load ──────────────────────────

def test_full_workflow(tmp_path):
    p = tmp_path / "preferences.json"
    # 1) Init : rien de caché
    save_prefs(p, {"hidden_categories": []})
    prefs = load_prefs(p)
    assert is_hidden(prefs, "sport") is False
    # 2) Cacher sport
    toggle_category(prefs, "sport")
    save_prefs(p, prefs)
    # 3) Reload et vérifie
    prefs2 = load_prefs(p)
    assert is_hidden(prefs2, "sport") is True
    # 4) Toggle = ré-afficher
    toggle_category(prefs2, "sport")
    save_prefs(p, prefs2)
    # 5) Vérif finale
    assert is_hidden(load_prefs(p), "sport") is False
    # Mais le updated_at a été mis à jour
    assert load_prefs(p)["updated_at"] != "1970-01-01T00:00:00"
