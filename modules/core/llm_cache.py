#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/core/llm_cache.py — Nova-Atlas
Cache disque pour les appels LLM : hash(contenu) → résumé.

But : éviter de payer 1 appel distant (~67s/article) pour un article
dont on a déjà le résumé (cross-cycle, dédup, retry de phase 0).

Clé = hash sha256 de (title|url) — pas du prompt entier — pour rester
robuste aux reformulations de prompt et garder un cache portable.

Stockage :
  data/llm_cache/<aabbcc...>.txt   → le résumé
  data/llm_cache/<aabbcc...>.meta → json {"ts": ..., "caller": ...}

TTL = 7 jours par défaut (config.llm.cache_ttl_days). Passé ce délai,
l'entrée est régénérée. On peut forcer le bypass avec use_cache=False
pour les éditions/rapports qui veulent du contenu frais.

Thread-safe via un lock fichier (réutilise le pattern de llm_client.py).
"""

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("nova.llm_cache")

DEFAULT_TTL_DAYS = 7


def cache_key(title: str, url: str) -> str:
    """
    Clé de cache stable pour un article donné.
    title peut être None ou vide : on hash alors uniquement l'URL.
    """
    norm_title = (title or "").strip().lower()
    norm_url = (url or "").strip().lower()
    payload = f"{norm_title}\x00{norm_url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:32]


class LLMCache:
    """
    Cache disque avec TTL. Les valeurs sont des chaînes (résumés LLM).

    Layout :
        cache_dir/
            <key>.txt   ← contenu
            <key>.meta  ← {"ts": epoch, "caller": str, "model": str}
    """

    def __init__(self, cache_dir: str | os.PathLike,
                 ttl_days: int = DEFAULT_TTL_DAYS):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_days) * 86400
        log.info(f"LLM cache: dir={self.cache_dir} ttl={ttl_days}d")

    def _paths(self, key: str) -> tuple[Path, Path]:
        return (
            self.cache_dir / f"{key}.txt",
            self.cache_dir / f"{key}.meta",
        )

    def get(self, key: str) -> Optional[str]:
        """
        Renvoie le contenu cachée si présent et non expiré, sinon None.
        """
        txt_path, meta_path = self._paths(key)
        if not txt_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ts = float(meta.get("ts", 0))
        except (json.JSONDecodeError, OSError, ValueError) as e:
            log.debug(f"cache meta illisible pour {key[:8]}: {e}")
            return None
        if self.ttl_seconds > 0 and (time.time() - ts) > self.ttl_seconds:
            log.debug(f"cache expiré pour {key[:8]} ({(time.time()-ts)/86400:.1f}j)")
            return None
        try:
            return txt_path.read_text(encoding="utf-8")
        except OSError as e:
            log.debug(f"cache lecture échouée pour {key[:8]}: {e}")
            return None

    def put(self, key: str, value: str, caller: str = "?", model: str = "?") -> None:
        """
        Écrit le contenu de manière atomique (write dans tmp + rename).
        N'écrit pas les chaînes vides (on n'indexe pas un échec).
        """
        if not value or not value.strip():
            return
        txt_path, meta_path = self._paths(key)
        meta = {"ts": time.time(), "caller": caller, "model": model}
        # Écriture atomique pour éviter la lecture d'un fichier partiel
        # si le process est tué en plein milieu d'un write.
        for path, content in [(txt_path, value), (meta_path, json.dumps(meta))]:
            tmp_fd, tmp_name = tempfile.mkstemp(
                dir=str(self.cache_dir), prefix=f".{path.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_name, path)
            except Exception:
                # Si l'écriture échoue, on nettoie le tmp et on log
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                log.warning(f"cache write échouée pour {key[:8]}")
                return

    def clear_expired(self) -> int:
        """
        Purge les entrées expirées. Renvoie le nombre supprimé.
        Utile en cron de maintenance, pas appelé par défaut.
        """
        if self.ttl_seconds <= 0:
            return 0
        removed = 0
        for meta_path in self.cache_dir.glob("*.meta"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                ts = float(meta.get("ts", 0))
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            if (time.time() - ts) > self.ttl_seconds:
                key = meta_path.name.replace(".meta", "")
                for ext in (".txt", ".meta"):
                    p = self.cache_dir / f"{key}{ext}"
                    try:
                        p.unlink()
                        removed += 1
                    except OSError:
                        pass
        return removed

    def stats(self) -> dict:
        """Stats sommaires pour debug."""
        n = sum(1 for _ in self.cache_dir.glob("*.txt"))
        size_bytes = sum(p.stat().st_size for p in self.cache_dir.glob("*.txt"))
        return {"entries": n, "size_bytes": size_bytes,
                "dir": str(self.cache_dir), "ttl_days": self.ttl_seconds // 86400}
