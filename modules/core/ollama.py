#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/core/ollama.py — Nova-Atlas
Point d'entrée unique pour tous les appels LLM (Ollama ou OpenRouter).

Ce module est un wrapper de compatibilité vers le provider unifié
modules.core.llm_provider. Toutes les fonctions existantes (ollama_call,
init_ollama, get_language, etc.) continuent de fonctionner sans changement
pour les modules consommateurs (fetch, report, editions, posts).

Backend configuré via config.yaml :
  llm:
    provider: "ollama"       # ou "openrouter"
"""

from typing import Optional

# ─── INSTANCE GLOBALE (déléguée à llm_provider) ───────────────────────────────

_client: Optional[object] = None


def init_ollama(config: dict):
    """Initialise (ou recharge) le provider LLM global.
    Ancien nom conservé pour compatibilité — délègue à init_llm()."""
    global _client
    from modules.core.llm_provider import init_llm as _init
    _client = _init(config)
    return _client


def reload_ollama(config: dict):
    """Recharge le provider LLM avec une nouvelle config.
    Ancien nom conservé pour compatibilité."""
    import logging
    provider = get_provider()
    provider.reload(config)
    logging.getLogger("nova.ollama").info(
        f"Rechargement LLM : modèle={config.get('ollama',{}).get('model','?')} "
        f"provider={config.get('llm',{}).get('provider','ollama')} "
        f"langue={config.get('service',{}).get('default_language','?')}"
    )
    return provider


def get_client():
    """Retourne le provider LLM global (anciennement OllamaClient)."""
    return get_provider()


def get_provider():
    """Retourne le provider LLM global."""
    from modules.core.llm_provider import get_provider as _get
    return _get()


import traceback

def ollama_call(prompt: str,
                model:   str = None,
                timeout: int = None,
                caller:  str = "atlas") -> str:
    """
    Raccourci global — équivalent de l'ancien ollama_call().
    Délègue au provider unifié (Ollama ou OpenRouter selon config).
    Le paramètre 'model' est ignoré (géré par la config).
    """
    from modules.core.llm_provider import llm_generate
    # Diagnostic: forcer un délai minimum long si non spécifié
    if timeout is None:
        timeout = 240
    # Logger l'appel pour diagnostiquer les timeouts inopinés
    import logging
    log = logging.getLogger("nova.ollama.diagnostic")
    log.debug(f"ollama_call: caller={caller}, prompt_len={len(prompt)}, timeout={timeout}")
    try:
        return llm_generate(prompt, caller=caller, timeout=timeout)
    except Exception as e:
        log.error(f"ollama_call failed: {e}\nStack:\n{traceback.format_exc()}")
        raise


def get_language() -> str:
    """Langue des résumés."""
    from modules.core.llm_provider import get_language as _gl
    return _gl()


def get_model() -> str:
    """Modèle courant."""
    from modules.core.llm_provider import get_model as _gm
    return _gm()


def get_fetch_timeout() -> int:
    from modules.core.llm_provider import get_fetch_timeout as _gft
    return _gft()


def get_edition_timeout() -> int:
    from modules.core.llm_provider import get_edition_timeout as _get
    return _get()


def get_report_timeout() -> int:
    from modules.core.llm_provider import get_report_timeout as _grt
    return _grt()
