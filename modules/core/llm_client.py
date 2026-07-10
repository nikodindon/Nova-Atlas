#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/core/ollama.py — Nova-Atlas
Point d'entrée unique pour tous les appels LLM (Ollama CLI ou llama-server HTTP).
Gère le verrou fichier pour éviter les appels concurrents.

Providers supportés :
  - "ollama"       : ollama run <model>  (CLI, mode interactif, stdin/stdout)
  - "llama-server" : http://<base_url>/v1/chat/completions  (REST API)
"""

import json
import os
import re
import subprocess
import time
import urllib.request
import urllib.error
import threading
from datetime import datetime
from pathlib import Path

# ─── PRIORITÉS DES CALLERS ───────────────────────────────────────────────────

CALLER_PRIORITY = {
    "fetch":    10,   # Plus haute : alimente le pool de news
    "bulletin": 10,   # Bulletin 30 min scheduler (meme priorite que fetch) :
                      # peut interrompre un fetch en cours pour ne pas rater
                      # la fenetre de diffusion (l'article coupe sera re-resume
                      # au prochain cycle)
    "flash":    8,    # Bulletin a la demande : peut interrompre bulletin/report,
                      # mais doit attendre la fin d'un fetch en cours
    "editions": 5,
    "report":   5,
    "atlas":    3,    # Defaut
    "posts":    1,
}

# ─── LANGUE ───────────────────────────────────────────────────────────────────

_LANG_MAP = {
    "fr": "français", "en": "english", "de": "deutsch",
    "es": "español", "pt": "português", "it": "italiano",
    "nl": "nederlands", "ru": "русский", "ar": "العربية",
    "ja": "日本語", "zh": "中文",
}


# ─── CLASSE PRINCIPALE ────────────────────────────────────────────────────────

class OllamaClient:
    """
    Client LLM thread-safe avec verrou fichier.
    Supporte deux providers :
      - "ollama"        → ollama run <model> (CLI)
      - "llama-server"  → HTTP POST /v1/chat/completions

    Instancié une fois par processus et partagé entre les modules.
    """

    def __init__(self, config: dict):
        llm_cfg = config.get("llm", {})

        self.provider = llm_cfg.get("provider", "ollama")
        self.model    = llm_cfg.get("model", "mistral:7b")
        self.base_url = llm_cfg.get("base_url", "http://localhost:8080")
        self.threads  = int(llm_cfg.get("threads", 6))

        self.timeout_fetch   = int(llm_cfg.get("timeout_fetch",   240))
        self.timeout_report  = int(llm_cfg.get("timeout_report",  600))
        self.timeout_edition = int(llm_cfg.get("timeout_edition", 900))

        lang = config.get("service", {}).get("default_language", "fr")
        self.language = _LANG_MAP.get(lang.lower(), lang)

        # Verrou fichier dans data/
        data_dir = Path(config.get("paths", {}).get("data_dir", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = data_dir / "ollama.lock"

        # Cache LLM sur disque (data/llm_cache/). Désactivé si use_cache=false
        # dans la config, ou si on a un import error sur llm_cache.
        self._cache = None
        cache_cfg = llm_cfg.get("cache", {})
        if cache_cfg.get("enabled", True):
            try:
                from modules.core.llm_cache import LLMCache
                cache_dir = cache_cfg.get("dir") or (data_dir / "llm_cache")
                ttl_days = int(cache_cfg.get("ttl_days", 7))
                self._cache = LLMCache(cache_dir, ttl_days=ttl_days)
            except Exception as e:
                import logging as _l
                _l.getLogger("nova.ollama").warning(
                    f"Cache LLM désactivé (erreur init): {e}"
                )

        import logging
        self.log = logging.getLogger("nova.ollama")
        self.log.info(f"LLM provider={self.provider} model={self.model} base_url={self.base_url}")

        # Auto-détection du modèle via /v1/models (llama-server).
        # Si le modèle configuré n'est pas dans la liste, on prend
        # le premier dispo. Ça évite de garder un nom hardcodé obsolète.
        if self.provider == "llama-server":
            detected = self._detect_model_from_server()
            if detected and detected != self.model:
                self.log.info(
                    f"Modèle configuré '{self.model}' non trouvé sur le serveur, "
                    f"bascule sur '{detected}'"
                )
                self.model = detected
            elif detected:
                self.log.info(f"Modèle '{self.model}' confirmé sur le serveur")
        if self._cache:
            self.log.info(f"LLM cache activé: {self._cache.stats()}")

    def _detect_model_from_server(self) -> str | None:
        """
        Interroge /v1/models du llama-server pour récupérer la liste
        des modèles chargés. Retourne :
          - self.model si présent dans la liste
          - le 1er modèle de la liste si self.model n'y est pas
          - None en cas d'erreur (serveur pas joignable, etc.)
        """
        import urllib.request, json as _json
        try:
            url = f"{self.base_url}/v1/models"
            req = urllib.request.Request(url, headers={"User-Agent": "nova-atlas"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = _json.loads(r.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            self.log.debug(f"Auto-détection modèle impossible: {e}")
            return None
        # Format OpenAI-compatible : data[].id, ou llama.cpp natif : models[].name
        models = []
        for m in (data.get("data") or []):
            mid = m.get("id") or m.get("name")
            if mid: models.append(mid)
        for m in (data.get("models") or []):
            mid = m.get("name") or m.get("id")
            if mid: models.append(mid)
        if not models:
            return None
        # Priorité : self.model s'il est dans la liste
        if self.model in models:
            return self.model
        # Sinon, le premier
        return models[0]

    # ── Verrou fichier ─────────────────────────────────────────────────────────

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

            if age > 1200:
                self.log.warning(f"Verrou expiré ({age}s) de '{holder}' — forcé par '{caller}'")
                break

            holder_priority = CALLER_PRIORITY.get(holder, 3)

            # Cas spécial : si le holder est un flash (bulletin à la demande),
            # on attend TRÈS longtemps (le flash peut prendre 5-10 min pour les
            # gros prompts, et l'utilisateur attend). Le fetch ne doit PAS
            # interrompre un flash en cours, il attend même 30 min si nécessaire.
            if holder == "flash" and caller == "fetch":
                if waited >= 1800:  # 30 min max (flash peut être long)
                    self.log.warning(f"Timeout d'attente d'un flash ({waited}s) — '{caller}' abandonne")
                    return False
                if waited == 0:
                    self.log.info(f"'{caller}' attend la fin du flash en cours (max 30min)...")
                time.sleep(5)
                waited += 5
                continue

            if my_priority >= holder_priority:
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
            return True

    def _release_lock(self):
        try:
            if self.lock_file.exists():
                self.lock_file.unlink()
        except Exception:
            pass

    # ── Nettoyage ANSI ─────────────────────────────────────────────────────────

    @staticmethod
    def _clean_ansi(text: str) -> str:
        text = re.sub(r'\x1b\[[0-9;]*[mGKHFABCDSuJh]', '', text)
        text = re.sub(r'\[\d+[ABCDEFGHJKSTSu]', '', text)
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        return text.strip()

    # ── Provider : Ollama CLI ──────────────────────────────────────────────────

    def _call_ollama_cli(self, prompt: str, model: str, timeout: int) -> str:
        env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1"}
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

    # ── Provider : llama-server (HTTP) ─────────────────────────────────────────

    def _call_llama_server(self, prompt: str, model: str, timeout: int) -> str:
        url = f"{self.base_url.rstrip('/')}/v1/chat/completions"
        # max_tokens large : modèles "thinking" (Qwen3, DeepSeek-R1, etc.) brûlent
        # 500-2000 tokens de réflexion AVANT la réponse utile. À 256 ou 512, le
        # serveur tronque et on reçoit du texte vide / tronqué.
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": 4096,
            "temperature": 0.3,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        # Cold-start: le 1er appel après boot du serveur ou swap de modèle
        # peut renvoyer du vide. On retente 1 fois après 5s pour confirmer
        # que c'est bien un cold-start et pas un vrai échec.
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    content = (data.get("choices", [{}])[0]
                                   .get("message", {})
                                   .get("content", "")).strip()
                    if content:
                        return content
                    # Contenu vide : c'est peut-être le cold-start. On retente.
                    self.log.warning(
                        f"llama-server a renvoyé un contenu vide "
                        f"(attempt {attempt+1}/2) — "
                        f"probable cold-start, retry dans 5s"
                    )
                    if attempt == 0:
                        time.sleep(5)
                        continue
                    return ""
            except urllib.error.HTTPError as e:
                self.log.error(f"llama-server HTTP {e.code}: {e.reason}")
                try:
                    err_body = json.loads(e.read().decode("utf-8"))
                    self.log.error(f"  → {err_body}")
                except Exception:
                    pass
                return ""
            except Exception as e:
                self.log.error(f"Erreur llama-server : {e}")
                return ""
        return ""

    # ── Appel principal ────────────────────────────────────────────────────────

    def call(self, prompt: str,
             model:   str = None,
             timeout: int = None,
             caller:  str = "atlas",
             cache_key: str = None,
             use_cache: bool = True) -> str:
        """
        Appel LLM thread-safe avec verrou fichier.
        Dispatch vers _call_ollama_cli() ou _call_llama_server() selon provider.

        Si cache_key est fourni et use_cache=True (défaut), on :
          1. cherche dans le cache disque (data/llm_cache/)
          2. si trouvé, on renvoie sans appeler le LLM (gain ~67s/article)
          3. sinon, on appelle le LLM et on stocke le résultat
        Les chaînes vides (échecs LLM) ne sont jamais cachées.
        """
        if model   is None: model   = self.model
        if timeout is None: timeout = self.timeout_fetch

        # ── Cache hit (pas de lock, lecture seule) ─────────────────────────
        if cache_key and use_cache and self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self.log.info(f"  ⚡ cache hit  {cache_key[:8]}  "
                              f"({len(cached)} chars, caller={caller})")
                return cached

        acquired = self._acquire_lock(caller, wait_max=timeout + 60)
        if not acquired:
            return ""

        try:
            if self.provider == "llama-server":
                result = self._call_llama_server(prompt, model, timeout)
            else:
                result = self._call_ollama_cli(prompt, model, timeout)

            # ── Cache write (seulement si on a un contenu utile) ──────────
            if cache_key and use_cache and self._cache is not None and result:
                self._cache.put(cache_key, result, caller=caller, model=model)
                self.log.info(f"  💾 cache miss → wrote {cache_key[:8]} "
                              f"({len(result)} chars, caller={caller})")
            return result
        except subprocess.TimeoutExpired:
            self.log.warning(f"Timeout ({timeout}s) — caller={caller} provider={self.provider}")
            return ""
        except Exception as e:
            self.log.error(f"Erreur LLM ({self.provider}) : {e}")
            return ""
        finally:
            self._release_lock()


# ─── PROCESSUS LANCER / ARRÊTER LLAMA-SERVER ─────────────────────────────────

_llama_server_process: subprocess.Popen | None = None
_llama_server_lock   = threading.Lock()


def _find_llama_server() -> str | None:
    """Cherche llama-server dans les chemins habituels."""
    candidates = [
        Path("/home/niko/bin/llama-server"),
        Path("/usr/local/bin/llama-server"),
        Path("/tmp/llama.cpp/build/bin/llama-server"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    # Sinon cherche dans PATH
    for d in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(d) / "llama-server"
        if candidate.exists():
            return str(candidate)
    return None


def start_llama_server(model: str, base_url: str = "http://localhost:8080",
                       n_gpu_layers: int = 0, n_threads: int = 6) -> bool:
    """
    Démarre llama-server en arrière-plan si pas déjà running.
    Retourne True si le serveur est prêt.
    """
    global _llama_server_process

    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8080

    # Teste si déjà running
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/v1/models",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                import logging
                logging.getLogger("nova.ollama").info(
                    f"llama-server déjà actif sur {base_url}")
                return True
    except Exception:
        pass

    with _llama_server_lock:
        if _llama_server_process is not None:
            return True  # déjà en cours de démarrage

        binary = _find_llama_server()
        if not binary:
            import logging
            logging.getLogger("nova.ollama").error(
                "llama-server non trouvé — vérifie qu'il est compilé et dans le PATH")
            return False

        import logging
        log = logging.getLogger("nova.ollama")
        log.info(f"Démarrage llama-server : {binary} -m {model} --host {host} --port {port}")

        _llama_server_process = subprocess.Popen(
            [binary, "-m", model, "--host", host, "--port", str(port),
             "-ngl", str(n_gpu_layers), "-t", str(n_threads)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "NO_COLOR": "1"},
        )

        # Attend que le serveur soit prêt (max 30s)
        for i in range(60):
            time.sleep(0.5)
            try:
                req = urllib.request.Request(
                    f"{base_url.rstrip('/')}/v1/models",
                    headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=2) as resp:
                    if resp.status == 200:
                        log.info(f"✅ llama-server prêt sur {base_url}")
                        return True
            except Exception:
                pass
            if _llama_server_process.poll() is not None:
                out, err = _llama_server_process.communicate(timeout=1)
                log.error(f"llama-server a crashé : {err.decode('utf-8', errors='replace')}")
                _llama_server_process = None
                return False

        log.warning("llama-server n'a pas répondu dans les 30s — continues quand même")
        return True


def stop_llama_server():
    """Arrête le processus llama-server s'il a été lancé par nous."""
    global _llama_server_process
    with _llama_server_lock:
        if _llama_server_process is not None:
            _llama_server_process.terminate()
            _llama_server_process.wait(timeout=10)
            _llama_server_process = None


# ─── INSTANCE GLOBALE ────────────────────────────────────────────────────────

_client: OllamaClient | None = None


def init_ollama(config: dict) -> OllamaClient:
    """Initialise (ou recharge) le client LLM global."""
    global _client
    _client = OllamaClient(config)

    # Auto-démarrage de llama-server si nécessaire
    if _client.provider == "llama-server":
        start_llama_server(_client.model, _client.base_url, n_threads=_client.threads)

    return _client


def reload_ollama(config: dict) -> OllamaClient:
    """Recharge le client LLM avec une nouvelle config."""
    import logging
    llm_cfg = config.get("llm", {})
    logging.getLogger("nova.ollama").info(
        f"Rechargement LLM : provider={llm_cfg.get('provider','?')} "
        f"model={llm_cfg.get('model','?')} "
        f"langue={config.get('service',{}).get('default_language','?')}"
    )
    return init_ollama(config)


def get_client() -> OllamaClient:
    if _client is None:
        raise RuntimeError(
            "OllamaClient non initialisé. "
            "Appelle modules.core.llm_client.init_ollama(config) en début de processus."
        )
    return _client


def ollama_call(prompt: str,
                model:       str = None,
                timeout:     int = None,
                caller:      str = "atlas",
                cache_key:   str = None,
                use_cache:   bool = True) -> str:
    """Raccourci global."""
    return get_client().call(prompt, model=model, timeout=timeout, caller=caller,
                             cache_key=cache_key, use_cache=use_cache)


def get_language() -> str:
    return get_client().language


def get_model() -> str:
    return get_client().model


def get_fetch_timeout() -> int:
    return get_client().timeout_fetch


def get_edition_timeout() -> int:
    return get_client().timeout_edition
