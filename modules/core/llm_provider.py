#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/core/llm_provider.py — Nova-Atlas
Fournisseur LLM unifié avec support de plusieurs backends :
  - ollama : modèle local via subprocess (verrou fichier, timeout)
  - openrouter : API HTTP via le client OpenAI-compatible (gratuit, rapide)

Le provider est transparent pour les modules existants (fetch, report, etc.)
qui continuent d'appeler ollama_call() sans changement.
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import logging

# ─── PRIORITÉS DES CALLERS (pour le verrou Ollama) ────────────────────────────

CALLER_PRIORITY = {
    "fetch":    10,
    "editions": 5,
    "report":   5,
    "posts":    1,
    "atlas":    3,
}

# Map ISO → nom complet utilisé dans les prompts
_LANG_MAP = {
    "fr": "français",
    "en": "english",
    "de": "deutsch",
    "es": "español",
    "pt": "português",
    "it": "italiano",
    "nl": "nederlands",
    "ru": "русский",
    "ar": "العربية",
    "ja": "日本語",
    "zh": "中文",
}


# ─── NETTOYAGE ANSI (utilisé par les deux backends) ───────────────────────────

def _clean_ansi(text: str) -> str:
    """Supprime les séquences ANSI et caractères de contrôle."""
    text = re.sub(r'\x1b\[[0-9;]*[mGKHFABCDsuJh]', '', text)
    text = re.sub(r'\[\d+[ABCDEFGHJKSTsu]', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text.strip()


# ─── BACKEND OLLAMA ───────────────────────────────────────────────────────────

class OllamaBackend:
    """Backend Ollama local avec verrou fichier et subprocess CLI."""

    def __init__(self, config: dict):
        ollama_cfg = config.get("ollama", {})
        self.model = ollama_cfg.get("model", "mistral:7b")
        self.timeout_fetch   = int(ollama_cfg.get("timeout_fetch",   240))
        self.timeout_report  = int(ollama_cfg.get("timeout_report",  600))
        self.timeout_edition = int(ollama_cfg.get("timeout_edition", 900))

        data_dir = Path(config.get("paths", {}).get("data_dir", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = data_dir / "ollama.lock"

        self.log = logging.getLogger("nova.ollama")

    # ── Verrou fichier ────────────────────────────────────────────────────────

    def _read_lock_info(self) -> dict:
        try:
            with open(self.lock_file) as f:
                return json.load(f)
        except Exception:
            return {}

    def _acquire_lock(self, caller: str, wait_max: int = 1200) -> bool:
        my_priority = CALLER_PRIORITY.get(caller, 3)
        waited = 0

        while self.lock_file.exists():
            info = self._read_lock_info()
            holder = info.get("caller", "?")
            age = int(time.time()) - info.get("ts", int(time.time()))

            if age > 1200:
                self.log.warning(f"Verrou expiré ({age}s) de '{holder}' — forcé par '{caller}'")
                break

            holder_priority = CALLER_PRIORITY.get(holder, 3)

            if my_priority > holder_priority:
                if waited >= 30:
                    self.log.info(f"'{caller}' (prio {my_priority}) force '{holder}' (prio {holder_priority})")
                    break
                if waited == 0:
                    self.log.info(f"'{caller}' attend '{holder}' (prio inférieure, max 30s)...")
            else:
                if waited >= wait_max:
                    self.log.warning(f"Timeout d'attente du verrou ({wait_max}s) — '{caller}' abandonne")
                    return False
                if waited == 0:
                    self.log.info(f"'{caller}' attend verrou de '{holder}' ({age}s écoulées)...")

            time.sleep(5)
            waited += 5

        try:
            with open(self.lock_file, "w") as f:
                json.dump({
                    "caller": caller,
                    "since": datetime.now().isoformat(),
                    "ts": int(time.time()),
                }, f)
            return True
        except Exception:
            return True

    def _release_lock(self):
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass

    # ── Appel ─────────────────────────────────────────────────────────────────

    def generate(self, prompt: str, caller: str = "atlas", timeout: int = None) -> str:
        if timeout is None:
            timeout = self.timeout_fetch

        acquired = self._acquire_lock(caller, wait_max=timeout + 60)
        if not acquired:
            return ""

        env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1"}

        try:
            result = subprocess.run(
                ["ollama", "run", self.model],
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            return _clean_ansi(result.stdout)
        except subprocess.TimeoutExpired:
            self.log.warning(f"Ollama timeout ({timeout}s) — caller={caller}")
            return ""
        except Exception as e:
            self.log.error(f"Erreur Ollama : {e}")
            return ""
        finally:
            self._release_lock()


# ─── BACKEND OPENROUTER ──────────────────────────────────────────────────────

class OpenRouterBackend:
    """Backend OpenRouter via l'API compatible OpenAI.
    
    Utilise openrouter/free comme modèle par défaut — le routeur
    sélectionne automatiquement le meilleur modèle gratuit disponible.
    """

    def __init__(self, config: dict):
        llm_cfg = config.get("llm", {})
        or_cfg = llm_cfg.get("openrouter", {})

        self.model = or_cfg.get("model", "openrouter/free")
        self.api_key = or_cfg.get("api_key", "") or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = or_cfg.get("base_url", "https://openrouter.ai/api/v1")

        self.timeout_fetch   = int(or_cfg.get("timeout_fetch",   60))
        self.timeout_report  = int(or_cfg.get("timeout_report",  180))
        self.timeout_edition = int(or_cfg.get("timeout_edition", 180))

        self.log = logging.getLogger("nova.openrouter")

        # Client OpenAI initialisé à la demande
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key or "sk-or-dummy",  # OpenRouter accepte dummy key pour les free
                )
            except ImportError:
                self.log.error("Package 'openai' non installé. pip install openai")
                return None
        return self._client

    def generate(self, prompt: str, caller: str = "atlas", timeout: int = None) -> str:
        if timeout is None:
            timeout = self.timeout_fetch

        if not self.api_key:
            self.log.warning("OpenRouter : aucune clé API configurée (api_key vide et OPENROUTER_API_KEY non définie)")
            return ""

        client = self._get_client()
        if client is None:
            return ""

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            self.log.error(f"OpenRouter error: {e}")
            return ""


# ─── PROVIDER UNIFIÉ ─────────────────────────────────────────────────────────

class LLMProvider:
    """
    Point d'entrée unique pour tous les appels LLM.
    Délègue au backend configuré (ollama ou openrouter).
    """

    def __init__(self, config: dict):
        self.config = config
        llm_cfg = config.get("llm", {})
        self.provider_name = llm_cfg.get("provider", "ollama").lower()

        # Langue depuis service.default_language
        lang = config.get("service", {}).get("default_language", "fr")
        self.language = _LANG_MAP.get(lang.lower(), lang)

        # Timeouts par défaut (fallback sur section ollama legacy)
        ollama_cfg = config.get("ollama", {})
        self.timeout_fetch   = int(ollama_cfg.get("timeout_fetch",   240))
        self.timeout_report  = int(ollama_cfg.get("timeout_report",  600))
        self.timeout_edition = int(ollama_cfg.get("timeout_edition", 900))

        # Initialise le backend
        if self.provider_name == "openrouter":
            self.backend = OpenRouterBackend(config)
            # Surcharge les timeouts avec ceux d'OpenRouter si définis
            or_cfg = llm_cfg.get("openrouter", {})
            if or_cfg:
                self.timeout_fetch   = int(or_cfg.get("timeout_fetch",   self.timeout_fetch))
                self.timeout_report  = int(or_cfg.get("timeout_report",  self.timeout_report))
                self.timeout_edition = int(or_cfg.get("timeout_edition", self.timeout_edition))
            self.model = self.backend.model
        else:
            self.backend = OllamaBackend(config)
            self.model = self.backend.model

        self.log = logging.getLogger(f"nova.llm")
        self.log.info(f"LLM Provider initialisé : backend={self.provider_name} model={self.model}")

    def generate(self, prompt: str, caller: str = "atlas", timeout: int = None) -> str:
        """Appel LLM unifié — avec fallback sur Ollama si OpenRouter rate limite ou échoue."""
        try:
            result = self.backend.generate(prompt, caller=caller, timeout=timeout)
        except Exception as e:
            self.log.error(f"Backend {self.provider_name} a échoué : {e} — tentative de fallback")
            result = ""
        # Si le résultat vide ou erreur, essayer l'autre backend
        if not result.strip():
            if self.provider_name == "openrouter":
                self.log.warning("OpenRouter a échoué (rate limit?) — basculement vers Ollama local")
                self.backend = OllamaBackend(self.config)
                result = self.backend.generate(prompt, caller=caller, timeout=timeout)
            elif self.provider_name == "ollama":
                self.log.warning("Ollama a échoué — basculement vers OpenRouter")
                self.backend = OpenRouterBackend(self.config)
                result = self.backend.generate(prompt, caller=caller, timeout=timeout)
        return result

    def reload(self, config: dict):
        """Recharge la configuration (hot-reload)."""
        self.log.info("Rechargement LLM Provider...")
        self.__init__(config)


# ─── INSTANCE GLOBALE (initialisée par main.py) ───────────────────────────────

_provider: Optional[LLMProvider] = None


def init_llm(config: dict) -> LLMProvider:
    """Initialise (ou recharge) le provider LLM global."""
    global _provider
    _provider = LLMProvider(config)
    return _provider


def get_provider() -> LLMProvider:
    if _provider is None:
        raise RuntimeError(
            "LLMProvider non initialisé. "
            "Appelle modules.core.llm_provider.init_llm(config) en début de processus."
        )
    return _provider


def llm_generate(prompt: str, caller: str = "atlas", timeout: int = None) -> str:
    """Raccourci global pour appeler le LLM."""
    return get_provider().generate(prompt, caller=caller, timeout=timeout)


# ─── COMPATIBILITÉ BACKWARD — wrappers legacy ─────────────────────────────────
#
# Ces fonctions permettent aux modules existants (fetch, report, editions, posts)
# de continuer à fonctionner sans aucune modification.
# Elles délèguent simplement au provider unifié.

def get_language() -> str:
    return get_provider().language if _provider else "français"


def get_model() -> str:
    return get_provider().model if _provider else "unknown"


def get_fetch_timeout() -> int:
    return get_provider().timeout_fetch if _provider else 240


def get_edition_timeout() -> int:
    return get_provider().timeout_edition if _provider else 900


def get_report_timeout() -> int:
    return get_provider().timeout_report if _provider else 600
