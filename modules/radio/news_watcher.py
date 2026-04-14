"""
modules/radio/news_watcher.py
Surveille le fichier JSON du jour et détecte les nouvelles entrées.
"""

import json
import time
import logging
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("nova.watcher")

class NewsWatcher:
    def __init__(self, radio_config: dict, on_bulletin_ready):
        """
        radio_config : section 'radio' du config principal
        on_bulletin_ready : callable(articles: list)
        """
        self.config = radio_config
        self.on_bulletin_ready = on_bulletin_ready

        paths = radio_config.get("paths", {}) if "paths" in radio_config else radio_config  # compatibilité temporaire

        self.data_dir = Path(paths.get("articles_dir", "data/articles"))
        self.processed_file = Path(paths.get("processed_hashes_file", "data/processed_hashes.json"))

        self.interval = radio_config.get("news_interval_seconds", 30)
        self.per_bulletin = radio_config.get("news_per_bulletin", 5)

        self._stop_event = threading.Event()
        self._processed_hashes: set = self._load_processed_hashes()
        self._pending: list = []

        self._init_existing_hashes()

    def _load_processed_hashes(self) -> set:
        if self.processed_file.exists():
            try:
                with open(self.processed_file, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception:
                return set()
        return set()

    def _save_processed_hashes(self):
        self.processed_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.processed_file, "w", encoding="utf-8") as f:
            json.dump(list(self._processed_hashes), f, ensure_ascii=False)

    def _init_existing_hashes(self):
        articles = self._read_today_articles()
        for a in articles:
            self._processed_hashes.add(a.get("hash"))
        self._save_processed_hashes()
        logger.info(f"{len(articles)} news déjà présentes → ignorées")
        logger.info(f"En attente de {self.per_bulletin} nouvelles news")

    def _get_today_json_path(self) -> Path:
        today = datetime.now().strftime("%Y%m%d")
        return self.data_dir / f"{today}_articles.json"

    def _read_today_articles(self) -> list:
        path = self._get_today_json_path()
        if not path.exists():
            return []

        for attempt in range(3):
            try:
                raw = path.read_bytes()
                if not raw.strip():
                    time.sleep(0.2)
                    continue
                data = json.loads(raw.decode("utf-8"))
                return data if isinstance(data, list) else []
            except (PermissionError, OSError, json.JSONDecodeError):
                time.sleep(0.3 * (attempt + 1))
        return []

    def run(self):
        logger.info("Surveillance des news démarrée")
        while not self._stop_event.is_set():
            try:
                self._check_new_articles()
            except Exception as e:
                logger.error(f"Erreur watcher : {e}")
            time.sleep(self.interval)

    def _check_new_articles(self):
        articles = self._read_today_articles()
        new_ones = [a for a in articles if a.get("hash") not in self._processed_hashes]

        for article in new_ones:
            summary = article.get("summary", "").strip()
            if summary.startswith("[") or not summary:   # ignorer contenu inaccessible
                self._processed_hashes.add(article["hash"])
                continue

            self._pending.append(article)
            self._processed_hashes.add(article["hash"])
            logger.info(f"{len(self._pending)}/{self.per_bulletin} nouvelles news détectées")

        if len(self._pending) >= self.per_bulletin:
            batch = list(self._pending)
            self._pending.clear()
            self._save_processed_hashes()
            logger.info(f"Déclenchement journal avec {len(batch)} news")
            self.on_bulletin_ready(batch)

    def stop(self):
        self._stop_event.set()