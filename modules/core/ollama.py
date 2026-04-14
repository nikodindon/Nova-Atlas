#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/core/ollama.py — Nova-Atlas
Point d'entrée unique pour tous les appels Ollama.
Gère le verrou fichier pour éviter les appels concurrents.

Refactorisé depuis atlas_ollama.py (pblart/nova-media) :
  - Les chemins sont résolus depuis le dict config Nova-Atlas
  - La langue est lue depuis config.service.default_language
  - Le modèle et les timeouts viennent de config.ollama.*
  - Toute la logique de verrou est conservée à l'identique
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

# ─── PRIORITÉS DES CALLERS ────────────────────────────────────────────────────

CALLER_PRIORITY = {
    "fetch":    10,
    "editions": 5,
    "report":   5,
    "posts":    1,
    "atlas":    3,
}


# ─── CLASSE PRINCIPALE ────────────────────────────────────────────────────────

class OllamaClient:
    """
    Client Ollama thread-safe avec verrou fichier.
    Instancié une fois par processus et partagé entre les modules.
    """

    def __init__(self, config: dict):
        ollama_cfg   = config.get("ollama", {})
        self.model   = ollama_cfg.get("model", "mistral:7b")
        self.timeout_fetch   = int(ollama_cfg.get("timeout_fetch",   240))
        self.timeout_report  = int(ollama_cfg.get("timeout_report",  600))
        self.timeout_edition = int(ollama_cfg.get("timeout_edition", 900))

        # Langue depuis service.default_language
        # Map ISO → nom complet utilisé dans les prompts Ollama
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
        lang = config.get("service", {}).get("default_language", "fr")
        self.language = _LANG_MAP.get(lang.lower(), lang)

        # Verrou fichier dans data/
        data_dir   = Path(config.get("paths", {}).get("data_dir", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = data_dir / "ollama.lock"

        import logging
        self.log = logging.getLogger("nova.ollama")

    # ── Helpers verrou (identiques à atlas_ollama.py) ─────────────────────────

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
            info   = self._read_lock_info()
            holder = info.get("caller", "?")
            age    = int(time.time()) - info.get("ts", int(time.time()))

            # Verrou expiré (> 20 min) → force
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
                    "since":  datetime.now().isoformat(),
                    "ts":     int(time.time()),
                }, f)
            return True
        except Exception:
            return True  # on continue même si l'écriture échoue

    def _release_lock(self):
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass

    # ── Nettoyage ANSI ────────────────────────────────────────────────────────

    @staticmethod
    def _clean_ansi(text: str) -> str:
        # Séquences ANSI complètes (ESC + [...)
        text = re.sub(r'\x1b\[[0-9;]*[mGKHFABCDsuJh]', '', text)
        # Résidus sans ESC : [nD, [nC, [nA, [nB etc. (cursor movement orphelins)
        text = re.sub(r'\[\d+[ABCDEFGHJKSTsu]', '', text)
        # Autres caractères de contrôle
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        return text.strip()

    # ── Appel principal ───────────────────────────────────────────────────────

    def call(self, prompt: str,
             model:   str = None,
             timeout: int = None,
             caller:  str = "atlas") -> str:
        """
        Appel Ollama thread-safe avec verrou fichier.
        Équivalent direct de ollama_call() dans atlas_ollama.py.
        """
        if model   is None: model   = self.model
        if timeout is None: timeout = self.timeout_fetch

        acquired = self._acquire_lock(caller, wait_max=timeout + 60)
        if not acquired:
            return ""

        env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1"}

        try:
            result = subprocess.run(
                ["ollama", "run", model],
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            return self._clean_ansi(result.stdout)
        except subprocess.TimeoutExpired:
            self.log.warning(f"Ollama timeout ({timeout}s) — caller={caller}")
            return ""
        except Exception as e:
            self.log.error(f"Erreur Ollama : {e}")
            return ""
        finally:
            self._release_lock()


# ─── INSTANCE GLOBALE (initialisée par main.py via init_ollama) ───────────────
#
# Chaque processus (NewsEngine, WebServer…) appelle init_ollama(config)
# une fois au démarrage. Les modules appelant ollama_call() utilisent
# ensuite l'instance sans avoir à se soucier de la config.

_client: OllamaClient | None = None


def init_ollama(config: dict) -> OllamaClient:
    """Initialise (ou recharge) le client Ollama global."""
    global _client
    _client = OllamaClient(config)
    return _client


def reload_ollama(config: dict) -> OllamaClient:
    """Recharge le client Ollama avec une nouvelle config (modèle, langue…).
    Alias explicite pour le reload à chaud — identique à init_ollama mais
    log un message pour traçabilité."""
    import logging
    logging.getLogger("nova.ollama").info(
        f"Rechargement Ollama : modèle={config.get('ollama',{}).get('model','?')} "
        f"langue={config.get('service',{}).get('default_language','?')}"
    )
    return init_ollama(config)


def get_client() -> OllamaClient:
    if _client is None:
        raise RuntimeError(
            "OllamaClient non initialisé. "
            "Appelle modules.core.ollama.init_ollama(config) en début de processus."
        )
    return _client


def ollama_call(prompt: str,
                model:   str = None,
                timeout: int = None,
                caller:  str = "atlas") -> str:
    """Raccourci global — équivalent de atlas_ollama.ollama_call()."""
    return get_client().call(prompt, model=model, timeout=timeout, caller=caller)


def get_language() -> str:
    """Langue des résumés — équivalent de atlas_ollama.get_summary_language()."""
    return get_client().language


def get_model() -> str:
    return get_client().model


def get_fetch_timeout() -> int:
    return get_client().timeout_fetch


def get_edition_timeout() -> int:
    return get_client().timeout_edition
