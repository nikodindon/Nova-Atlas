#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_feeds_loader.py — tests du loader de flux RSS.

Couvre :
  - load_feeds : format simple, commentaires SPRINT0, lignes vides
  - load_feeds : fichier absent → dict vide
  - load_feeds : fichier corrompu → warning + ce qui a pu être parsé
  - save_feeds : round-trip load → save → load
  - save_feeds : préservation des flux désactivés
  - add_feed : ajout normal, dédup
  - remove_feed : retrait normal, introuvable, catégorie vide
  - toggle_feed : bascule actif/inactif
"""

import pytest
from pathlib import Path

from modules.core.feeds_loader import (
    load_feeds, save_feeds, add_feed, remove_feed, toggle_feed,
    move_feed, reorder_feeds, add_category
)


SAMPLE_YAML = """# header comment
geopolitique:
  - https://www.france24.com/fr/rss
  - https://www.france24.com/en/rss
  # [SPRINT0] https://feeds.reuters.com/reuters/topNews

economie:
  - https://www.cnbc.com/rss
  # [SPRINT0] https://bfmbusiness.bfmtv.com/rss
"""


@pytest.fixture
def feeds_file(tmp_path):
    p = tmp_path / "feeds.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    return p


def test_load_feeds_basic(feeds_file):
    feeds = load_feeds(feeds_file)
    assert "geopolitique" in feeds
    assert "economie" in feeds
    assert len(feeds["geopolitique"]) == 2
    assert "https://www.france24.com/fr/rss" in feeds["geopolitique"]


def test_load_feeds_skips_commented_urls(feeds_file):
    """Les URLs en commentaire (avec ou sans [SPRINT0]) sont ignorées."""
    feeds = load_feeds(feeds_file)
    all_urls = [u for urls in feeds.values() for u in urls]
    assert "https://feeds.reuters.com/reuters/topNews" not in all_urls
    assert "https://bfmbusiness.bfmtv.com/rss" not in all_urls


def test_load_feeds_missing_file(tmp_path):
    """Fichier absent → dict vide (pas de crash)."""
    feeds = load_feeds(tmp_path / "nope.yaml")
    assert feeds == {}


def test_load_feeds_empty_file(tmp_path):
    """Fichier vide → dict vide."""
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    feeds = load_feeds(p)
    assert feeds == {}


def test_load_feeds_only_comments(tmp_path):
    """Fichier que des commentaires → dict vide."""
    p = tmp_path / "comments.yaml"
    p.write_text("# only\n# comments\n# here\n", encoding="utf-8")
    feeds = load_feeds(p)
    assert feeds == {}


def test_load_feeds_malformed_logs_warning(tmp_path, caplog):
    """Ligne bizarre (pas une catégorie, pas un item) → warning + on continue."""
    p = tmp_path / "weird.yaml"
    p.write_text("""good_cat:
  - https://ok.com
this is a weird line without a colon
another_cat:
  - https://also-ok.com
""", encoding="utf-8")
    feeds = load_feeds(p)
    assert "good_cat" in feeds
    assert "another_cat" in feeds
    # Le warning doit être loggé
    assert any("malformée" in r.message for r in caplog.records)


def test_save_feeds_roundtrip(tmp_path):
    """load → save → load doit donner le même résultat."""
    p = tmp_path / "feeds.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    feeds1 = load_feeds(p)
    save_feeds(p, feeds1)
    feeds2 = load_feeds(p)
    assert feeds1 == feeds2


def test_save_feeds_preserves_disabled(feeds_file):
    """Quand on save, les flux désactivés (commentés) doivent être préservés."""
    feeds = load_feeds(feeds_file)
    # Sauvegarder sans rien changer
    save_feeds(feeds_file, feeds)
    # Recharger et vérifier que le contenu désactivé est toujours là
    text = feeds_file.read_text(encoding="utf-8")
    assert "[SPRINT0] https://feeds.reuters.com/reuters/topNews" in text
    assert "[SPRINT0] https://bfmbusiness.bfmtv.com/rss" in text


def test_save_feeds_atomic_no_partial_files(tmp_path):
    """save_feeds doit écrire atomiquement (pas de .tmp oublié)."""
    p = tmp_path / "feeds.yaml"
    feeds = {"test": ["https://example.com"]}
    save_feeds(p, feeds)
    tmps = list(tmp_path.glob(".feeds.yaml.*.tmp"))
    assert tmps == [], f"tmp files left: {tmps}"
    # Et le fichier est lisible
    assert load_feeds(p) == {"test": ["https://example.com"]}


def test_save_feeds_creates_missing_categories(tmp_path):
    """Si une catégorie est nouvelle, elle est ajoutée même si vide."""
    p = tmp_path / "feeds.yaml"
    feeds = {"existing": ["https://x.com"], "new_cat": ["https://y.com"]}
    save_feeds(p, feeds)
    text = p.read_text()
    assert "existing:" in text
    assert "new_cat:" in text


def test_add_feed_basic():
    feeds = {"test": ["https://existing.com"]}
    ok = add_feed(feeds, "test", "https://new.com")
    assert ok is True
    assert "https://new.com" in feeds["test"]


def test_add_feed_dedup():
    """L'URL est déjà là → False, pas d'ajout."""
    feeds = {"test": ["https://x.com"]}
    ok = add_feed(feeds, "test", "https://x.com")
    assert ok is False
    assert len(feeds["test"]) == 1


def test_add_feed_new_category():
    """Catégorie inexistante → créée."""
    feeds = {}
    ok = add_feed(feeds, "new_cat", "https://x.com")
    assert ok is True
    assert "new_cat" in feeds
    assert feeds["new_cat"] == ["https://x.com"]


def test_remove_feed_basic():
    feeds = {"test": ["https://a.com", "https://b.com"]}
    ok = remove_feed(feeds, "test", "https://a.com")
    assert ok is True
    assert feeds["test"] == ["https://b.com"]


def test_remove_feed_cleans_empty_category():
    """Catégorie qui devient vide → supprimée du dict."""
    feeds = {"test": ["https://x.com"]}
    remove_feed(feeds, "test", "https://x.com")
    assert "test" not in feeds


def test_remove_feed_not_found():
    """URL pas dans la catégorie → False."""
    feeds = {"test": ["https://x.com"]}
    ok = remove_feed(feeds, "test", "https://nope.com")
    assert ok is False
    assert len(feeds["test"]) == 1


def test_remove_feed_unknown_category():
    """Catégorie n'existe pas → False."""
    feeds = {}
    ok = remove_feed(feeds, "nope", "https://x.com")
    assert ok is False


def test_toggle_feed_present_to_removed():
    feeds = {"test": ["https://x.com"]}
    state = toggle_feed(feeds, "test", "https://x.com")
    assert state == "removed"
    assert "test" not in feeds  # catégorie vide supprimée


def test_toggle_feed_not_present():
    """toggle d'une URL pas dans active → renvoie None (no-op)."""
    feeds = {"test": ["https://x.com"]}
    state = toggle_feed(feeds, "test", "https://nope.com")
    assert state is None
    assert "https://x.com" in feeds["test"]  # intact


def test_full_workflow_load_edit_save_reload(tmp_path):
    """Workflow complet : load → remove → add → save → reload → vérif."""
    p = tmp_path / "feeds.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    # 1) Load
    feeds = load_feeds(p)
    assert len(feeds["geopolitique"]) == 2
    # 2) Remove
    assert remove_feed(feeds, "geopolitique", "https://www.france24.com/fr/rss")
    # 3) Add new
    assert add_feed(feeds, "geopolitique", "https://www.bbc.com/new-feed")
    # 4) Save
    save_feeds(p, feeds)
    # 5) Reload
    feeds2 = load_feeds(p)
    assert "https://www.france24.com/fr/rss" not in feeds2["geopolitique"]
    assert "https://www.bbc.com/new-feed" in feeds2["geopolitique"]
    # 6) Le désactivé SPRINT0 est préservé dans le fichier
    text = p.read_text()
    assert "[SPRINT0] https://feeds.reuters.com/reuters/topNews" in text


# ─── Tests pour move_feed ──────────────────────────────────────────────────────

def test_move_feed_up_basic():
    feeds = {"cat": ["a", "b", "c"]}
    assert move_feed(feeds, "cat", "b", "up") is True
    assert feeds["cat"] == ["b", "a", "c"]


def test_move_feed_down_basic():
    feeds = {"cat": ["a", "b", "c"]}
    assert move_feed(feeds, "cat", "b", "down") is True
    assert feeds["cat"] == ["a", "c", "b"]


def test_move_feed_up_at_top():
    """Le 1er élément ne peut pas monter."""
    feeds = {"cat": ["a", "b", "c"]}
    assert move_feed(feeds, "cat", "a", "up") is False
    assert feeds["cat"] == ["a", "b", "c"]


def test_move_feed_down_at_bottom():
    """Le dernier élément ne peut pas descendre."""
    feeds = {"cat": ["a", "b", "c"]}
    assert move_feed(feeds, "cat", "c", "down") is False
    assert feeds["cat"] == ["a", "b", "c"]


def test_move_feed_unknown_category():
    feeds = {"cat": ["a", "b"]}
    assert move_feed(feeds, "nope", "a", "up") is False


def test_move_feed_unknown_url():
    feeds = {"cat": ["a", "b"]}
    assert move_feed(feeds, "cat", "nope", "up") is False
    assert feeds["cat"] == ["a", "b"]


def test_move_feed_swap_is_undoable():
    """up puis down (sur la même URL) doit revenir à l'état initial."""
    feeds = {"cat": ["a", "b", "c"]}
    move_feed(feeds, "cat", "b", "up")
    assert feeds["cat"] == ["b", "a", "c"]
    move_feed(feeds, "cat", "b", "down")
    assert feeds["cat"] == ["a", "b", "c"]


def test_move_feed_serialize_to_disk(tmp_path):
    """Move + save + reload = ordre persisté sur disque."""
    p = tmp_path / "feeds.yaml"
    p.write_text("""cat:
  - https://a.com
  - https://b.com
  - https://c.com
""", encoding="utf-8")
    feeds = load_feeds(p)
    move_feed(feeds, "cat", "https://c.com", "up")
    move_feed(feeds, "cat", "https://c.com", "up")  # c goes from idx 2 → idx 0
    save_feeds(p, feeds)
    feeds2 = load_feeds(p)
    assert feeds2["cat"] == ["https://c.com", "https://a.com", "https://b.com"]


def test_move_feed_preserves_disabled_after_save(tmp_path):
    """Après move + save, les désactivés sont toujours là."""
    p = tmp_path / "feeds.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    feeds = load_feeds(p)
    move_feed(feeds, "geopolitique",
              "https://www.france24.com/en/rss", "up")
    save_feeds(p, feeds)
    text = p.read_text()
    assert "[SPRINT0] https://feeds.reuters.com/reuters/topNews" in text
    # Et l'ordre a bien changé
    feeds2 = load_feeds(p)
    assert feeds2["geopolitique"][0] == "https://www.france24.com/en/rss"


# ─── Tests pour reorder_feeds ────────────────────────────────────────────────

def test_reorder_feeds_full():
    feeds = {"cat": ["a", "b", "c"]}
    assert reorder_feeds(feeds, "cat", ["c", "b", "a"]) is True
    assert feeds["cat"] == ["c", "b", "a"]


def test_reorder_feeds_mismatch():
    """L'ordre ne contient pas les mêmes URLs → False."""
    feeds = {"cat": ["a", "b", "c"]}
    assert reorder_feeds(feeds, "cat", ["a", "b", "x"]) is False
    assert feeds["cat"] == ["a", "b", "c"]


def test_reorder_feeds_partial():
    """L'ordre est incomplet → False."""
    feeds = {"cat": ["a", "b", "c"]}
    assert reorder_feeds(feeds, "cat", ["a", "b"]) is False


def test_reorder_feeds_unknown_category():
    feeds = {}
    assert reorder_feeds(feeds, "nope", ["a"]) is False


# ─── Tests pour add_category ──────────────────────────────────────────────────

def test_add_category_new():
    feeds = {"existing": []}
    assert add_category(feeds, "new_cat") is True
    assert "new_cat" in feeds
    assert feeds["new_cat"] == []


def test_add_category_existing():
    feeds = {"existing": ["x"]}
    assert add_category(feeds, "existing") is False
    assert feeds["existing"] == ["x"]


def test_add_category_persists_through_save(tmp_path):
    p = tmp_path / "feeds.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    feeds = load_feeds(p)
    add_category(feeds, "nouveauté_2026")
    save_feeds(p, feeds)
    feeds2 = load_feeds(p)
    assert "nouveauté_2026" in feeds2


# ─── Tests d'intégration : toggle + save préserve l'URL pour réactivation ────

def test_toggle_then_readd_preserves_disabled_state(tmp_path):
    """Toggle (disable) puis re-add (re-activate) → l'URL ne doit pas
    apparaître en double, et l'historique disabled est OK."""
    p = tmp_path / "feeds.yaml"
    p.write_text(SAMPLE_YAML, encoding="utf-8")
    feeds = load_feeds(p)
    # Disable
    remove_feed(feeds, "geopolitique", "https://www.france24.com/en/rss")
    save_feeds(p, feeds)
    # Re-add (réactive l'URL désactivée, ne la duplique pas)
    feeds2 = load_feeds(p)
    add_feed(feeds2, "geopolitique", "https://www.france24.com/en/rss")
    save_feeds(p, feeds2)
    # L'URL est revenue en actif
    feeds3 = load_feeds(p)
    assert feeds3["geopolitique"].count("https://www.france24.com/en/rss") == 1
    assert "https://www.france24.com/en/rss" in feeds3["geopolitique"]


# ─── Tests pour l'API du toggle ───────────────────────────────────────────────
# (Le toggle = remove + save → l'URL reste en disabled, peut être ré-activée)

def test_disabled_url_can_be_reactivated(tmp_path):
    """Quand on désactive une URL (toggle), save_feeds l'écrit en SPRINT0.
    Un add suivant la réactive : load_feeds la lit comme active, save_feeds
    la retire de disabled. Aucun doublon en sortie."""
    p = tmp_path / "feeds.yaml"
    p.write_text("""geopolitique:
  - https://a.com
  - https://b.com
""", encoding="utf-8")
    feeds = load_feeds(p)
    # Disable b
    remove_feed(feeds, "geopolitique", "https://b.com")
    save_feeds(p, feeds)
    # b is now in disabled
    text = p.read_text()
    assert "[SPRINT0] https://b.com" in text
    # Reactivate b
    feeds2 = load_feeds(p)
    add_feed(feeds2, "geopolitique", "https://b.com")
    save_feeds(p, feeds2)
    # b is back, no duplicate
    text2 = p.read_text()
    assert text2.count("https://b.com") == 1
    assert "[SPRINT0] https://b.com" not in text2
    # a is still there
    assert "https://a.com" in text2
