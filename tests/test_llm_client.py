#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_llm_client.py — tests du client LLM (avec mock HTTP).

Couvre :
  - construction du client, lecture des paramètres config
  - dispatch llama-server (on mocke urllib.request.urlopen)
  - retry cold-start : 1er appel vide → retry → 2e appel renvoie contenu
  - retry cold-start : 2 appels vides → on abandonne et retourne ""
  - gestion d'erreur HTTP (5xx, 4xx)
  - extraction du bon champ `choices[0].message.content`
  - use_cache=False bypass le cache
  - la clé du cache est propagée correctement
"""

import json
import time
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from modules.core.llm_client import OllamaClient


@pytest.fixture
def cfg():
    return {
        "service": {"default_language": "fr"},
        "llm": {
            "provider": "llama-server",
            "model": "test-model.gguf",
            "base_url": "http://test-llm:8080",
            "threads": 4,
            "timeout_fetch": 60,
        },
        "paths": {"data_dir": "/tmp/nova-test-data"},
    }


@pytest.fixture
def client(cfg, tmp_path):
    """Client avec cache désactivé pour ne pas toucher le disque."""
    cfg["paths"]["data_dir"] = str(tmp_path / "data")
    cfg["llm"]["cache"] = {"enabled": False}
    return OllamaClient(cfg)


def _make_response(content: str) -> MagicMock:
    """Construit un mock de réponse HTTP avec le payload JSON standard."""
    body = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": content}}]
    }).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _make_error_response(code: int, body: dict = None) -> MagicMock:
    """Construit un mock qui lève HTTPError à l'entrée de `with`."""
    import urllib.error
    err = urllib.error.HTTPError(
        url="http://test:8080/v1/chat/completions",
        code=code, msg="Server Error", hdrs={}, fp=None
    )
    if body is not None:
        err.fp = MagicMock()
        err.fp.read.return_value = json.dumps(body).encode("utf-8")
    # urllib raise à l'entrée du `with`, donc on doit raise ici
    raise_err = MagicMock(side_effect=err)
    raise_err.__enter__ = MagicMock(side_effect=err)
    raise_err.__exit__ = MagicMock(return_value=False)
    return raise_err


# ── Construction ────────────────────────────────────────────────────────


def test_client_init_reads_config(cfg, tmp_path):
    cfg["paths"]["data_dir"] = str(tmp_path / "data")
    cfg["llm"]["cache"] = {"enabled": False}
    c = OllamaClient(cfg)
    assert c.provider == "llama-server"
    assert c.model == "test-model.gguf"
    assert c.base_url == "http://test-llm:8080"
    assert c.threads == 4
    assert c.timeout_fetch == 60
    assert c.language == "français"


def test_client_init_default_provider(cfg, tmp_path):
    """Si provider absent du yaml → fallback 'ollama' (rétro-compat)."""
    cfg["paths"]["data_dir"] = str(tmp_path / "data")
    cfg["llm"]["cache"] = {"enabled": False}
    del cfg["llm"]["provider"]
    c = OllamaClient(cfg)
    assert c.provider == "ollama"


def test_client_cache_disabled_by_config(cfg, tmp_path):
    """Si cache.enabled=false, self._cache reste None."""
    cfg["paths"]["data_dir"] = str(tmp_path / "data")
    cfg["llm"]["cache"] = {"enabled": False}
    c = OllamaClient(cfg)
    assert c._cache is None


def test_client_cache_enabled_creates_dir(cfg, tmp_path):
    """Si cache.enabled=true (défaut), self._cache est créé et le dir existe."""
    cfg["paths"]["data_dir"] = str(tmp_path / "data")
    c = OllamaClient(cfg)
    assert c._cache is not None
    assert c._cache.cache_dir.exists()


# ── Dispatch HTTP ───────────────────────────────────────────────────────


def test_call_llama_server_returns_content(client):
    """Cas nominal : le serveur renvoie du texte, on le récupère."""
    with patch("urllib.request.urlopen", return_value=_make_response("Bonjour !")):
        out = client._call_llama_server("Dis bonjour", "test-model.gguf", 10)
    assert out == "Bonjour !"


def test_call_llama_server_sends_correct_payload(client):
    """Le payload HTTP contient bien model/messages/max_tokens/temperature."""
    captured = {}
    def fake_urlopen(req, **kw):
        captured["url"]    = req.full_url
        captured["method"] = req.get_method()
        captured["body"]   = json.loads(req.data.decode())
        return _make_response("ok")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client._call_llama_server("hello", "m.gguf", 10)
    assert captured["url"] == "http://test-llm:8080/v1/chat/completions"
    assert captured["method"] == "POST"
    p = captured["body"]
    assert p["model"] == "m.gguf"
    assert p["messages"] == [{"role": "user", "content": "hello"}]
    assert p["stream"] is False
    # Le patch thinking (sprint 0) doit être présent
    assert p["max_tokens"] == 4096
    assert p["temperature"] == 0.3


# ── Cold-start retry ────────────────────────────────────────────────────


def test_call_retries_on_empty_response(client):
    """1er appel vide → 2e appel renvoie du texte → on garde le 2e."""
    responses = [_make_response(""), _make_response("Final answer")]
    with patch("urllib.request.urlopen", side_effect=responses), \
         patch("time.sleep") as _sleep:  # on évite le vrai sleep
        out = client._call_llama_server("q", "m", 30)
    assert out == "Final answer"
    # Le sleep n'est appelé qu'une fois (entre les 2 tentatives)
    assert _sleep.call_count == 1
    assert _sleep.call_args[0][0] == 5


def test_call_gives_up_after_two_empty(client):
    """2 appels vides consécutifs → on abandonne et retourne ''."""
    with patch("urllib.request.urlopen",
               return_value=_make_response("")), \
         patch("time.sleep"):
        out = client._call_llama_server("q", "m", 30)
    assert out == ""


# ── Error handling ──────────────────────────────────────────────────────


def test_call_http_error_returns_empty(client):
    """HTTPError 500 → on log et on retourne '' (pas de crash)."""
    fake = _make_error_response(500, {"error": "boom"})
    with patch("urllib.request.urlopen", side_effect=fake):
        out = client._call_llama_server("q", "m", 10)
    assert out == ""


def test_call_socket_error_returns_empty(client):
    """Exception réseau → ''. Le log contient l'erreur."""
    with patch("urllib.request.urlopen",
               side_effect=ConnectionError("refused")):
        out = client._call_llama_server("q", "m", 10)
    assert out == ""


# ── Cache integration ──────────────────────────────────────────────────


def test_call_with_cache_hit_skips_network(cfg, tmp_path):
    """Si cache hit, on ne touche PAS au réseau."""
    cfg["paths"]["data_dir"] = str(tmp_path / "data")
    cfg["llm"]["cache"] = {"enabled": True, "ttl_days": 7}
    c = OllamaClient(cfg)
    c._cache.put("existing_key", "cached text", caller="fetch", model="m")

    with patch("urllib.request.urlopen") as urlopen_mock:
        out = c.call("prompt", caller="fetch", cache_key="existing_key")
    assert out == "cached text"
    urlopen_mock.assert_not_called()


def test_call_with_cache_miss_calls_llm_and_writes(cfg, tmp_path):
    """Cache miss → on appelle le LLM, et on stocke le résultat."""
    cfg["paths"]["data_dir"] = str(tmp_path / "data")
    cfg["llm"]["cache"] = {"enabled": True, "ttl_days": 7}
    c = OllamaClient(cfg)

    with patch("urllib.request.urlopen",
               return_value=_make_response("fresh answer")):
        out = c.call("prompt", caller="fetch", cache_key="newkey")
    assert out == "fresh answer"
    # Et c'est bien mis en cache
    assert c._cache.get("newkey") == "fresh answer"


def test_call_use_cache_false_bypasses_cache(cfg, tmp_path):
    """use_cache=False : on appelle le LLM même si le cache a la clé."""
    cfg["paths"]["data_dir"] = str(tmp_path / "data")
    cfg["llm"]["cache"] = {"enabled": True, "ttl_days": 7}
    c = OllamaClient(cfg)
    c._cache.put("k", "STALE", caller="fetch", model="m")

    with patch("urllib.request.urlopen",
               return_value=_make_response("FRESH")):
        out = c.call("prompt", caller="fetch", cache_key="k", use_cache=False)
    assert out == "FRESH"  # le cache est bypassé
