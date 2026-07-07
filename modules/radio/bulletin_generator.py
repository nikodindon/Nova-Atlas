#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/radio/bulletin_generator.py — Nova-Atlas
Générateur de bulletins radio "30 minutes".

Pipeline :
  1. Charger les bulletins.yaml
  2. Récupérer les articles des 30 dernières minutes
  3. Si < min_articles → skip
  4. Prompt LLM : "Voici N news. Fais un journal balisé [VOIX1]...[VOIX2]..."
  5. Split le script en segments selon les balises
  6. TTS chaque segment avec sa voix assignée
  7. Concatène les mp3 (ffmpeg)
  8. Mix voix + musique de fond
  9. Push vers audio_queue/

Le script LLM est conçu pour :
  - Viser target_words ± tolerance_words
  - Classer par ordre d'importance décroissant
  - 2 voix qui s'alternent (intro V1, news V1, transition V2, news V2, outro V1)
  - Utiliser les expressions de messages.yaml (intros, outros, transitions)
  - Pas de markdown, pas de répétitions
"""

import json
import logging
import random
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from modules.core.llm_client import ollama_call
from modules.utils.helpers import clean_for_tts

logger = logging.getLogger("nova.bulletin")

# Tags de voix dans le script LLM
VOICE_TAGS = ["[VOIX1]", "[VOIX2]", "[VOIX3]", "[VOIX_BREAKING]"]


# ─────────────────────────────────────────────────────────────────────────────
#  Chargement config
# ─────────────────────────────────────────────────────────────────────────────

def load_bulletins_config(paths: dict) -> dict:
    """Charge config/bulletins.yaml avec fallback sur défauts."""
    bulletins_path = paths["root"] / "config" / "bulletins.yaml"
    if not bulletins_path.exists():
        logger.warning(f"bulletins.yaml introuvable ({bulletins_path}) → défauts")
        return {
            "post_hours": [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
            "first_slot_hour": 7,
            "target_words": 1500,
            "tolerance_words": 200,
            "min_articles": 3,
            "window_minutes": 30,
            "voices": {
                "primary":   "fr-FR-HenriNeural",
                "secondary": "fr-FR-DeniseNeural",
            },
            "voices_extra": {},
            "structure": {
                "intro":        {"voice": "primary",   "target_words": 100},
                "breaking":     {"voice": "breaking",  "target_words": 200, "optional": True},
                "news_block_1": {"voice": "primary",   "target_words": 400},
                "transition":   {"voice": "secondary", "target_words": 30},
                "news_block_2": {"voice": "secondary", "target_words": 400},
                "transition_2": {"voice": "primary",   "target_words": 30},
                "news_block_3": {"voice": "secondary", "target_words": 300},
                "outro":        {"voice": "primary",   "target_words": 100},
            },
            "background_volume": 0.30,
            "intro_jingle": True,
        }
    with open(bulletins_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────────────────────
#  Collecte des articles de la fenêtre temporelle
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_articles(paths: dict, window_minutes: int = 30) -> List[dict]:
    """
    Récupère tous les articles des `window_minutes` dernières minutes
    depuis data/articles/. Supporte 2 formats :
      - data/articles/<YYYYMMDD>/*.json  (un fichier par cycle)
      - data/articles/<YYYYMMDD>_articles.json  (un fichier par jour)

    Retourne une liste d'articles (dicts), triée par timestamp décroissant.
    """
    now = datetime.now()
    cutoff = now - timedelta(minutes=window_minutes)
    today = now.strftime("%Y%m%d")
    articles_root = paths["data"] / "articles"

    if not articles_root.exists():
        return []

    # Collecte tous les fichiers JSON du jour (2 formats possibles)
    json_files = []
    # Format 1 : sous-dossier par jour
    sub_dir = articles_root / today
    if sub_dir.exists():
        json_files.extend(sub_dir.glob("*.json"))
    # Format 2 : fichier unique par jour
    single_file = articles_root / f"{today}_articles.json"
    if single_file.exists():
        json_files.append(single_file)

    found = []
    for f in json_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for art in data:
                ts_str = art.get("timestamp", "")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    continue
                if ts >= cutoff:
                    s = (art.get("summary") or "").strip()
                    if s and not s.startswith("["):
                        found.append(art)
        except Exception as e:
            logger.debug(f"  (skip {f.name}: {e})")
            continue

    # Déduplication par hash (cas où les 2 formats coexistent
    # avec le même article dans les 2 fichiers)
    seen = set()
    unique = []
    for art in found:
        h = art.get("hash", "")
        if h and h in seen:
            continue
        if h:
            seen.add(h)
        unique.append(art)

    # Tri par timestamp décroissant (plus récent en premier)
    unique.sort(key=lambda a: a.get("timestamp", ""), reverse=True)
    return unique


# ─────────────────────────────────────────────────────────────────────────────
#  Construction du prompt LLM
# ─────────────────────────────────────────────────────────────────────────────

def _format_articles_for_prompt(articles: List[dict]) -> str:
    """Formate les articles pour le prompt LLM (titre + résumé)."""
    lines = []
    for i, a in enumerate(articles, 1):
        title = (a.get("title") or "").strip()
        summary = (a.get("summary") or "").strip()
        cat = a.get("category", "monde")
        source = a.get("source", "")
        # Coupe les résumés trop longs pour le prompt
        if len(summary) > 400:
            summary = summary[:400] + "…"
        lines.append(f"[{i}] ({cat}) {title}")
        if source:
            lines.append(f"    Source : {source}")
        lines.append(f"    {summary}")
        lines.append("")
    return "\n".join(lines)


def build_bulletin_prompt(articles: List[dict], config: dict,
                          intros: List[str], transitions: List[str],
                          outros: List[str]) -> str:
    """
    Construit le prompt LLM pour générer un journal balisé.

    Le LLM reçoit :
      - Les N articles (titres + résumés)
      - Les intros/transitions/outros disponibles
      - La structure cible (intro/news/transitions/outro)
      - L'ordre d'importance décroissant
    """
    target = config.get("target_words", 1500)
    structure = config.get("structure", {})
    total_news_words = sum(
        s.get("target_words", 0) for k, s in structure.items()
        if k.startswith("news_block")
    )
    news_blocks = [k for k in structure if k.startswith("news_block")]

    articles_text = _format_articles_for_prompt(articles)

    # Choisit 1 intro et 1 outro au hasard parmi les options
    # On remplit {{heure}} et {{date}} maintenant pour que le LLM n'ait pas à le faire
    now_str = datetime.now().strftime("%Hh%M")
    date_str = datetime.now().strftime("%d/%m/%Y")
    intro = random.choice(intros).format(heure=now_str, date=date_str) if intros else f"Bonjour à tous, il est {now_str}."
    outro = random.choice(outros).format(heure=now_str, date=date_str) if outros else "C'est tout pour ce bulletin."

    prompt = f"""Tu es un journaliste radio professionnel français. Tu produis un BULLETIN D'INFORMATION qui couvre les 30 dernières minutes d'actualité. Le bulletin est diffusé sur une radio d'information continue.

CONTEXTE :
- Heure du bulletin : ~{datetime.now().strftime("%Hh%M")}
- Durée cible du bulletin : 10 minutes de parole (~{target} mots, ±200)
- Fenêtre temporelle couverte : 30 dernières minutes d'actualité
- Audience : auditeur français cultivé
- Style : France Inter / France Info, professionnel, neutre

ARTICLES À TRAITER (les plus importants en premier) :
{articles_text}

STRUCTURE DU BULLETIN :
1. INTRO ({structure.get("intro", {}).get("target_words", 100)} mots) — Voix 1
2. {news_blocks[0] if len(news_blocks) > 0 else "news_block_1"} ({total_news_words // max(1, len(news_blocks))} mots) — Voix 1
   {len(news_blocks[0:1]) if news_blocks else 0}-3 news principales
3. TRANSITION ({structure.get("transition", {}).get("target_words", 30)} mots) — Voix 2
4. {news_blocks[1] if len(news_blocks) > 1 else "news_block_2"} ({total_news_words // max(1, len(news_blocks))} mots) — Voix 2
   2-3 news secondaires
5. TRANSITION 2 ({structure.get("transition_2", {}).get("target_words", 30)} mots) — Voix 1
6. {news_blocks[2] if len(news_blocks) > 2 else "news_block_3"} ({total_news_words // max(1, len(news_blocks))} mots) — Voix 2
   1-2 news de fond
7. OUTRO ({structure.get("outro", {}).get("target_words", 100)} mots) — Voix 1

CONSIGNES STRICTES :
- Utilise les BALISES [VOIX1] et [VOIX2] pour marquer les changements de voix
  Format : [VOIX1] texte parlé par la voix 1. [VOIX2] texte parlé par la voix 2. [VOIX1] retour voix 1.
- INTRO (Voix 1) : adapte l'inspiration suivante au contexte — "{intro}"
- OUTRO (Voix 1) : utilise une variation de — "{outro}"
- TRANSITIONS (Voix 2 ou Voix 1) : varie les expressions, n'utilise pas la même deux fois
- Classement par ordre d'importance décroissante (grosse news en premier)
- Chaque news est reformulée pour être naturelle à l'oral, pas lue
- 2-3 phrases par news, factuel, ton neutre
- N'invente rien : utilise UNIQUEMENT les informations données
- Pas de markdown (pas d'astérisques, pas de dièses, pas de liens)
- Phrases complètes, terminées par un point
- Vise {target} mots total

FORMAT DE SORTIE :
Écris UNIQUEMENT le script balisé, sans méta-commentaire.
Commence par [VOIX1], termine par [VOIX1] (pour l'outro).

Script :"""

    return prompt


# ─────────────────────────────────────────────────────────────────────────────
#  Split du script en segments (selon les balises [VOIX1]/[VOIX2])
# ─────────────────────────────────────────────────────────────────────────────

def split_script_by_voice(script: str) -> List[Tuple[str, str]]:
    """
    Split le script en segments selon les balises [VOIX1] et [VOIX2].

    Retourne une liste de (voice_tag, text).
    Ex: [("[VOIX1]", "Bonjour..."), ("[VOIX2]", "Dans un autre registre..."), ...]
    """
    # Pattern qui match [VOIX1] ou [VOIX2] ou [VOIX3] ou [VOIX_BREAKING]
    pattern = r"\[VOIX[123]\]|\[VOIX_BREAKING\]"
    parts = re.split(f"({pattern})", script)

    segments = []
    current_voice = "[VOIX1]"  # défaut
    for part in parts:
        if not part or part.isspace():
            continue
        if re.match(pattern, part):
            current_voice = part
        else:
            text = part.strip()
            if text:
                segments.append((current_voice, text))
    return segments


# ─────────────────────────────────────────────────────────────────────────────
#  LLM call wrapper
# ─────────────────────────────────────────────────────────────────────────────

def generate_bulletin_script(articles: List[dict], config: dict,
                              intros: List[str], transitions: List[str],
                              outros: List[str]) -> Optional[str]:
    """Appelle le LLM pour générer le script balisé. Retourne None si erreur."""
    prompt = build_bulletin_prompt(articles, config, intros, transitions, outros)
    try:
        # S'assure que le LLM est initialisé (sinon OllamaClient non initialisé)
        from modules.core.llm_client import init_ollama as _init_ollama
        _init_ollama(config)
        # Timeout long car génération ~1500 mots
        out = ollama_call(prompt, timeout=300, caller="bulletin")
        if not out:
            logger.warning("LLM a renvoyé un script vide")
            return None
        # Nettoyage de sécurité (filet contre markdown résiduel)
        out = clean_for_tts(out)
        # S'assure que ça commence par [VOIX1]
        if not out.startswith("["):
            out = "[VOIX1] " + out
        return out
    except Exception as e:
        logger.error(f"Erreur génération bulletin LLM : {e}")
        return None


def validate_bulletin(script: str, target: int, tolerance: int) -> Tuple[bool, int, str]:
    """
    Valide qu'un script est utilisable.
    Retourne (ok, word_count, message).
    """
    if not script:
        return False, 0, "script vide"
    # Compte les mots (sans les balises)
    text_only = re.sub(r"\[VOIX[123]\]", "", script)
    word_count = len(text_only.split())
    min_words = target - tolerance
    max_words = target + tolerance
    if word_count < min_words:
        return False, word_count, f"trop court ({word_count} < {min_words})"
    if word_count > max_words:
        return False, word_count, f"trop long ({word_count} > {max_words})"
    # Vérifie qu'il y a au moins 1 [VOIX1] et 1 [VOIX2]
    if "[VOIX1]" not in script:
        return False, word_count, "pas de [VOIX1]"
    if "[VOIX2]" not in script:
        return False, word_count, "pas de [VOIX2]"
    return True, word_count, "ok"


# ─────────────────────────────────────────────────────────────────────────────
#  Génération TTS par segment
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_segment(text: str, voice: str, output_path: Path) -> bool:
    """
    Synthétise un segment texte avec une voix edge-tts.
    Retourne True si succès.
    """
    import asyncio
    import edge_tts
    try:
        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_path))
        asyncio.run(_run())
        return output_path.exists() and output_path.stat().st_size > 0
    except Exception as e:
        logger.error(f"Erreur TTS ({voice}) : {e}")
        return False


def synthesize_all_segments(segments: List[Tuple[str, str]],
                             voice_map: Dict[str, str],
                             tmp_dir: Path) -> List[Path]:
    """
    Synthétise tous les segments avec leur voix assignée.
    Retourne la liste des chemins mp3 dans l'ordre.
    """
    paths = []
    for i, (voice_tag, text) in enumerate(segments):
        voice = voice_map.get(voice_tag, voice_map.get("[VOIX1]"))
        seg_path = tmp_dir / f"seg_{i:03d}_{voice_tag.strip('[]')}.mp3"
        logger.debug(f"  Segment {i+1}/{len(segments)} [{voice_tag}] "
                    f"({len(text.split())} mots, voix {voice})")
        if not synthesize_segment(text, voice, seg_path):
            logger.warning(f"  Segment {i} échoué, skip")
            continue
        paths.append(seg_path)
    return paths


# ─────────────────────────────────────────────────────────────────────────────
#  Concaténation des segments (ffmpeg)
# ─────────────────────────────────────────────────────────────────────────────

def concatenate_segments(seg_paths: List[Path], output_path: Path) -> bool:
    """Concatène les mp3 segments en un seul fichier via ffmpeg."""
    if not seg_paths:
        return False
    # Crée le fichier de liste pour ffmpeg
    list_file = output_path.with_suffix(".txt")
    with open(list_file, "w") as f:
        for p in seg_paths:
            f.write(f"file '{p.absolute()}'\n")
    try:
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", str(output_path)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error(f"ffmpeg concat a échoué : {result.stderr[-300:]}")
            return False
        return output_path.exists()
    finally:
        list_file.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Mix voix + musique de fond
# ─────────────────────────────────────────────────────────────────────────────

def mix_voice_with_background(voice_path: Path, bg_dir: Path,
                              volume: float, output_path: Path) -> bool:
    """Mixe la voix avec une musique de fond aléatoire."""
    if not bg_dir.exists():
        shutil_move = shutil_move_fallback
        shutil_move(voice_path, output_path)
        return True
    bg_files = list(bg_dir.glob("*.mp3"))
    if not bg_files:
        shutil_move_fallback(voice_path, output_path)
        return True
    bg = random.choice(bg_files)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(voice_path),
        "-stream_loop", "-1", "-i", str(bg),
        "-filter_complex",
        f"[0:a]volume=2.0[voice];[1:a]volume={volume}[bg];"
        f"[voice][bg]amix=inputs=2:duration=shortest[mix]",
        "-map", "[mix]", "-c:a", "libmp3lame", "-b:a", "128k",
        str(output_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.returncode == 0 and output_path.exists()
    except Exception as e:
        logger.error(f"Mix a échoué : {e}")
        return False


def shutil_move_fallback(src: Path, dst: Path):
    """Fallback si pas de musique : on déplace juste le fichier voix."""
    import shutil
    shutil.move(str(src), str(dst))


# ─────────────────────────────────────────────────────────────────────────────
#  Point d'entrée principal
# ─────────────────────────────────────────────────────────────────────────────

class BulletinGenerator:
    """Génère un bulletin radio "30 minutes" complet."""

    def __init__(self, config: dict, paths: dict):
        self.config = config
        self.paths = paths
        self.bulletins_cfg = load_bulletins_config(paths)
        # Voix
        voices_cfg = self.bulletins_cfg.get("voices", {})
        voices_extra = self.bulletins_cfg.get("voices_extra", {})
        self.voice_map = {
            "[VOIX1]":         voices_cfg.get("primary",   "fr-FR-HenriNeural"),
            "[VOIX2]":         voices_cfg.get("secondary", "fr-FR-DeniseNeural"),
            "[VOIX3]":         voices_cfg.get("primary",   "fr-FR-HenriNeural"),
            "[VOIX_BREAKING]": voices_extra.get("breaking", voices_cfg.get("primary", "fr-FR-HenriNeural")),
        }
        # Chemins
        self.bg_dir    = paths["root"] / paths.get("background_music", "background_music")
        self.queue_dir = paths["root"] / paths.get("audio_queue",     "audio_queue")
        self.tmp_dir   = paths["root"] / paths.get("tmp_dir",         "tmp")
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def build(self, articles: List[dict]) -> Optional[Path]:
        """
        Génère un bulletin complet (collecte LLM + TTS + mix).
        Retourne le chemin du mp3 final ou None.
        """
        cfg = self.bulletins_cfg

        # Filtre : nombre minimum d'articles
        if len(articles) < cfg.get("min_articles", 3):
            logger.info(
                f"⏭️ Bulletin skippé : {len(articles)} article(s) < "
                f"{cfg.get('min_articles', 3)} minimum"
            )
            return None

        logger.info(f"📡 Génération bulletin 10min ({len(articles)} articles)")

        # Charge les intros/transitions/outros depuis messages.yaml
        intros, transitions, outros = self._load_messages()

        # 1) Prompt LLM
        script = generate_bulletin_script(articles, cfg, intros, transitions, outros)
        if not script:
            return None

        # 2) Valide le script (longueur, présence des balises)
        target = cfg.get("target_words", 1500)
        tolerance = cfg.get("tolerance_words", 200)
        ok, word_count, msg = validate_bulletin(script, target, tolerance)
        if not ok:
            logger.warning(f"⚠️ Script {msg} (target: {target}±{tolerance})")
            # On accepte quand même, on coupe au max
            if word_count > target + tolerance:
                script = self._truncate_script(script, target + tolerance)
        logger.info(f"📝 Script généré : {word_count} mots, {len(script.splitlines())} lignes")

        # 3) Split en segments
        segments = split_script_by_voice(script)
        logger.info(f"🎬 {len(segments)} segments (alternance voix)")
        for i, (voice_tag, text) in enumerate(segments):
            n_words = len(text.split())
            logger.debug(f"  [{i+1}/{len(segments)}] {voice_tag} → {n_words} mots")

        # 4) TTS de chaque segment
        with tempfile.TemporaryDirectory(dir=str(self.tmp_dir)) as tmp:
            tmp_dir = Path(tmp)
            seg_paths = synthesize_all_segments(segments, self.voice_map, tmp_dir)
            if not seg_paths:
                logger.error("Aucun segment synthétisé avec succès")
                return None

            # 5) Concaténation
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            voice_path = tmp_dir / f"voice_{timestamp}.mp3"
            if not concatenate_segments(seg_paths, voice_path):
                logger.error("Concaténation des segments a échoué")
                return None

            # 6) Mix voix + musique
            final_path = self.queue_dir / f"bulletin_{timestamp}.mp3"
            if not mix_voice_with_background(
                voice_path, self.bg_dir,
                cfg.get("background_volume", 0.30), final_path
            ):
                logger.error("Mix voix + musique a échoué")
                return None

        logger.info(f"✅ Bulletin prêt : {final_path}")
        return final_path

    def build_async(self, articles: List[dict], callback=None):
        """Lance la génération dans un thread."""
        def _run():
            path = self.build(articles)
            if callback:
                callback(path)
        threading.Thread(target=_run, daemon=True).start()

    def _truncate_script(self, script: str, max_words: int) -> str:
        """Coupe le script à max_words mots (en gardant la dernière balise [VOIX1])."""
        words = script.split()
        if len(words) <= max_words:
            return script
        # Tronque en gardant l'outro
        truncated = " ".join(words[:max_words])
        if not truncated.rstrip().endswith("."):
            truncated += "."
        return truncated + " [VOIX1] C'est tout pour ce journal."

    def _load_messages(self) -> Tuple[List[str], List[str], List[str]]:
        """Charge intros/transitions/outros depuis config/messages.yaml."""
        try:
            import yaml
            messages_path = self.paths["root"] / "config" / "messages.yaml"
            if not messages_path.exists():
                return [], [], []
            with open(messages_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            intros = [i for i in data.get("intros", []) if not str(i).startswith("#")]
            transitions = [t for t in data.get("transitions", []) if not str(t).startswith("#")]
            outros = [o for o in data.get("outros", []) if not str(o).startswith("#")]
            return intros, transitions, outros
        except Exception as e:
            logger.warning(f"Erreur chargement messages.yaml : {e}")
            return [], [], []
