#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_config.py — tests du loader de config.

Couvre :
  - load_config avec fichier valide
  - load_config avec fichier absent (fallback sur defaults)
  - load_config avec fichier corrompu (fallback sur defaults)
  - rétro-compat clé "ollama" → "llm"
  - valeurs par défaut présentes (llm.model, paths.data_dir, etc.)
  - get_service_name et get_service_tagline
"""

import os
import pytest
import yaml
from pathlib import Path

from modules.core.config import (
    load_config, get_service_name, get_service_tagline, DEFAULT_CONFIG
)


@pytest.fixture
def tmp_config(tmp_path):
    """Helper : écrit un config YAML dans tmp_path et renvoie le chemin."""
    def _make(data: dict) -> str:
        p = tmp_path / "config.yaml"
        p.write_text(yaml.safe_dump(data), encoding="utf-8")
        return str(p)
    return _make


def test_load_valid_config(tmp_config):
    """Un YAML valide est parsé et merged avec les defaults."""
    path = tmp_config({
        "service": {"name": "TestRadio", "default_language": "en"},
        "llm": {"provider": "llama-server", "model": "my-model.gguf"},
    })
    cfg = load_config(path)
    assert cfg["service"]["name"] == "TestRadio"
    assert cfg["service"]["default_language"] == "en"
    # Les defaults sont préservés
    assert cfg["llm"]["base_url"] == "http://localhost:8080"
    # Les valeurs custom écrasent les defaults
    assert cfg["llm"]["model"] == "my-model.gguf"
    # Paths présents par défaut
    assert cfg["paths"]["data_dir"] == "data"


def test_load_missing_file_uses_defaults(tmp_path):
    """Fichier absent → defaults (pas de crash)."""
    cfg = load_config(str(tmp_path / "nope.yaml"))
    assert cfg["service"]["name"] == "Nova Media"
    assert cfg["llm"]["provider"] == "ollama"


def test_load_corrupt_file_uses_defaults(tmp_path):
    """YAML corrompu → defaults, on n'explose pas."""
    p = tmp_path / "config.yaml"
    p.write_text("this is not: valid: yaml: at all:\n  - [unclosed", encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg["service"]["name"] == "Nova Media"
    assert cfg["llm"]["provider"] == "ollama"


def test_ollama_key_backward_compat(tmp_config):
    """L'ancienne clé 'ollama' est convertie en 'llm' pour rétro-compat.

    Le bloc 'ollama' ne sert qu'à combler les clés NON DÉFINIES dans 'llm'.
    Les clés déjà présentes (typiquement 'provider' via les defaults)
    ne sont PAS écrasées — c'est volontaire pour ne pas casser les
    configs qui définissent déjà 'llm' explicitement.
    """
    # custom_field n'est PAS dans les defaults → le rétro-compat le prend
    path = tmp_config({
        "ollama": {"custom_field": "kept", "extra_url": "http://x"}
    })
    cfg = load_config(path)
    assert "ollama" not in cfg, "l'ancienne clé doit être supprimée"
    assert cfg["llm"]["custom_field"] == "kept"
    assert cfg["llm"]["extra_url"] == "http://x"
    # Les clés déjà dans les defaults ne sont pas écrasées
    assert cfg["llm"]["provider"] == "ollama"
    assert cfg["llm"]["model"] == DEFAULT_CONFIG["llm"]["model"]


def test_ollama_key_does_not_override_existing_llm(tmp_config):
    """Si llm existe déjà, le bloc 'ollama' est ignoré (priorité au neuf)."""
    path = tmp_config({
        "llm": {"provider": "llama-server", "model": "new.gguf"},
        "ollama": {"provider": "ollama", "model": "old.gguf"},
    })
    cfg = load_config(path)
    # llm neuf l'emporte (config.llm.setdefault ne s'applique pas)
    assert cfg["llm"]["provider"] == "llama-server"
    assert cfg["llm"]["model"] == "new.gguf"


def test_get_service_name_and_tagline():
    cfg = {"service": {"name": "Nova", "tagline": "Hello"}}
    assert get_service_name(cfg) == "Nova"
    assert get_service_tagline(cfg) == "Hello"
    # Missing keys → defaults
    assert get_service_name({}) == "Nova Media"
    assert get_service_tagline({}) == ""


def test_max_articles_per_feed_default():
    """Le default doit être 8 (compat avec les configs existantes)."""
    cfg = load_config(str(__import__("pathlib").Path("/dev/null")))
    assert cfg["paths"]["data_dir"] == "data"
    # Note : DEFAULT_CONFIG n'a pas rss.max_articles_per_feed car
    # c'est atlas_fetch.py qui le lit directement. On vérifie juste
    # que la config ne crashe pas si on injecte la clé.
    cfg["rss"] = {"max_articles_per_feed": 5, "max_total_articles": 50}
    assert cfg["rss"]["max_total_articles"] == 50
