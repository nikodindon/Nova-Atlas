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
    load_feeds, save_feeds, add_feed, remove_feed, toggle_feed
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
