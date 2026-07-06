#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_llm_cache.py — tests du cache disque LLM.

Couvre :
  - cache_key : stable, indépendante de la casse/espaces
  - get/put : round-trip basique
  - get : miss (clé absente)
  - get : expiration par TTL
  - put : n'écrit pas les chaînes vides
  - put : écriture atomique (le fichier n'est jamais partial)
  - clear_expired : supprime les vieilles entrées
  - stats : rapport entries/size cohérent
"""

import time
import json
import pytest
from pathlib import Path

from modules.core.llm_cache import LLMCache, cache_key, DEFAULT_TTL_DAYS


def test_cache_key_stable():
    """Même titre+URL → même clé, quels que soient la casse/les espaces."""
    k1 = cache_key("  Hello World  ", "HTTPS://Example.com/A")
    k2 = cache_key("hello world", "https://example.com/a")
    assert k1 == k2
    assert len(k1) == 32  # sha256 truncated


def test_cache_key_differs_on_url():
    k1 = cache_key("Same", "https://a.com/1")
    k2 = cache_key("Same", "https://a.com/2")
    assert k1 != k2


def test_cache_key_differs_on_title():
    k1 = cache_key("Article A", "https://a.com")
    k2 = cache_key("Article B", "https://a.com")
    assert k1 != k2


def test_cache_key_handles_none_or_empty():
    """Ne crash pas si title ou url est None ou vide."""
    k1 = cache_key(None, "https://a.com")
    k2 = cache_key("",   "https://a.com")
    k3 = cache_key("Title", None)
    # k1 et k2 doivent être identiques (title None == title vide)
    assert k1 == k2
    # k3 ne crash pas et est une clé valide
    assert len(k3) == 32


def test_cache_put_get_roundtrip(tmp_path):
    cache = LLMCache(tmp_path, ttl_days=7)
    cache.put("abc123", "le résumé du jour", caller="fetch")
    assert cache.get("abc123") == "le résumé du jour"


def test_cache_get_missing_returns_none(tmp_path):
    cache = LLMCache(tmp_path, ttl_days=7)
    assert cache.get("nonexistent") is None


def test_cache_does_not_store_empty(tmp_path):
    """Une chaîne vide n'est jamais cachée (échec LLM → pas de polluer le cache)."""
    cache = LLMCache(tmp_path, ttl_days=7)
    cache.put("k1", "",         caller="fetch")
    cache.put("k2", "   ",      caller="fetch")
    cache.put("k3", "\n\t  \n", caller="fetch")
    assert cache.get("k1") is None
    assert cache.get("k2") is None
    assert cache.get("k3") is None


def test_cache_ttl_expiration(tmp_path):
    """Un TTL de 0j = pas d'expiration (désactivé). Utile pour debug/tests."""
    cache = LLMCache(tmp_path, ttl_days=0)
    cache.put("k1", "value")
    # ttl_seconds = 0 → le check d'expiration est skippé
    assert cache.get("k1") == "value"


def test_cache_ttl_short_expiration(tmp_path):
    """Avec un TTL très court, l'entrée expire vite."""
    cache = LLMCache(tmp_path, ttl_days=1)  # 86400s
    # Override ttl pour le test : on triche en re-créant le cache
    cache.ttl_seconds = 1
    cache.put("k1", "value")
    assert cache.get("k1") == "value"
    time.sleep(2)
    assert cache.get("k1") is None


def test_cache_overwrite(tmp_path):
    """put sur la même clé écrase (utilisé quand le résumé change)."""
    cache = LLMCache(tmp_path, ttl_days=7)
    cache.put("k1", "old value")
    cache.put("k1", "new value")
    assert cache.get("k1") == "new value"


def test_cache_meta_written(tmp_path):
    """Les .meta contiennent ts/caller/model — utilisé pour debug/stats."""
    cache = LLMCache(tmp_path, ttl_days=7)
    cache.put("k1", "value", caller="fetch", model="my-model.gguf")
    meta_path = tmp_path / "k1.meta"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert "ts" in meta
    assert meta["caller"] == "fetch"
    assert meta["model"] == "my-model.gguf"
    assert meta["ts"] <= time.time()


def test_cache_clear_expired(tmp_path):
    """clear_expired supprime uniquement les entrées hors TTL."""
    cache = LLMCache(tmp_path, ttl_days=7)
    # Entrée fraîche
    cache.put("fresh", "ok")
    # Entrée vieille : on triche en modifiant le meta
    cache.put("stale", "old")
    meta = json.loads((tmp_path / "stale.meta").read_text())
    meta["ts"] = time.time() - (10 * 86400)  # 10 jours
    (tmp_path / "stale.meta").write_text(json.dumps(meta))

    removed = cache.clear_expired()
    assert removed == 2  # 2 fichiers (stale.txt + stale.meta)
    assert cache.get("fresh") == "ok"
    assert cache.get("stale") is None


def test_cache_stats(tmp_path):
    """stats() reflète l'état du répertoire."""
    cache = LLMCache(tmp_path, ttl_days=7)
    cache.put("a", "x" * 100)
    cache.put("b", "y" * 200)
    s = cache.stats()
    assert s["entries"] == 2
    assert s["size_bytes"] == 300
    assert s["ttl_days"] == 7
    assert s["dir"] == str(tmp_path)


def test_cache_atomic_write_no_partial_files(tmp_path):
    """Si on put puis qu'on lit immédiatement, on a toujours un fichier complet."""
    cache = LLMCache(tmp_path, ttl_days=7)
    for i in range(50):
        cache.put(f"key-{i:03d}", "x" * 1000)
    # Pas de .tmp qui traînent
    tmps = list(tmp_path.glob(".*.tmp"))
    assert tmps == [], f"fichiers temporaires oubliés: {tmps}"
    # Toutes les entrées sont lisibles
    for i in range(50):
        assert cache.get(f"key-{i:03d}") == "x" * 1000
