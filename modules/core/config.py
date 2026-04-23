#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/core/config.py — Central configuration loader
"""

import yaml
from pathlib import Path
from typing import Dict, Any

CONFIG_PATH = Path("config/config.yaml")
DEFAULT_CONFIG = {
    "service": {
        "name": "Nova Media",
        "tagline": "Your personal AI news engine & radio",
        "default_language": "fr"
    },
    "llm": {
        "provider": "ollama",
        "model": "qwen3:8b",
        "base_url": "http://localhost:8080",
        "timeout_fetch": 240,
        "timeout_report": 600,
        "timeout_edition": 900
    },
    "radio": {
        "post_hours": [7, 9, 11, 13, 15, 17, 19, 21],
        "news_per_bulletin": 5,
        "news_interval_seconds": 30,
        "background_volume": 0.30,
        "voices": {
            "fr": ["fr-FR-HenriNeural", "fr-FR-DeniseNeural"]
        }
    },
    "web": {
        "host": "0.0.0.0",
        "port": 5055
    },
    "paths": {
        "data_dir": "data",
        "articles_dir": "data/articles",
        "reports_dir": "data/reports",
        "editions_dir": "data/editions",
        "posts_dir": "data/posts",
        "site_dir": "site",
        "audio_queue": "audio_queue",
        "tmp_dir": "tmp",
        "background_music": "background_music",
        "music": "music"
    }
}


def load_config(config_path: str = None) -> Dict[str, Any]:
    """Load configuration from YAML file with fallback to defaults."""
    path = Path(config_path) if config_path else CONFIG_PATH

    if not path.exists():
        print(f"⚠️  Config file not found: {path}. Using default configuration.")
        return DEFAULT_CONFIG.copy()

    try:
        with open(path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f)

        # Merge user config with defaults
        config = DEFAULT_CONFIG.copy()
        for section, values in user_config.items():
            if isinstance(values, dict):
                config.setdefault(section, {}).update(values)
            else:
                config[section] = values

        # ── Backward compat: "ollama" key → "llm" ──────────────────────────────
        if "ollama" in user_config and "llm" not in user_config:
            config["llm"] = config.get("llm", {})
            for k, v in user_config["ollama"].items():
                if k not in config["llm"]:
                    config["llm"][k] = v
            # provider par défaut pour ne pas casser les configs existantes
            config["llm"].setdefault("provider", "ollama")
            del config["ollama"]

        return config

    except Exception as e:
        print(f"❌ Error loading config: {e}. Using defaults.")
        return DEFAULT_CONFIG.copy()


def get_service_name(config: Dict[str, Any]) -> str:
    """Return the service name defined in config."""
    return config.get("service", {}).get("name", "Nova Media")


def get_service_tagline(config: Dict[str, Any]) -> str:
    return config.get("service", {}).get("tagline", "")