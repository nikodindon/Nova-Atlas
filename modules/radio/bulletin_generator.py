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
import time
import uuid
from dataclasses import dataclass
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

from modules.radio.audio_integrity import (
    AudioInfo, AudioIntegrityError, inspect_audio, compatible_duration, publish_audio,
)


@dataclass
class Segment:
    index: int
    voice_tag: str
    text: str
    voice: str
    path: Path
    state: str = 'pending'
    attempts: int = 0
    info: AudioInfo | None = None


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
        logger.warning("bulletins.yaml absent; defaults selected")
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

def dedup_articles_by_similarity(articles: List[dict], threshold: float = 0.7) -> List[dict]:
    """
    Déduplique les articles par similarité du début de leur résumé.

    Les news tech/geopolitique/sport sont souvent rapportées par 5-10 sources
    différentes avec des résumés très proches (mais pas identiques). On groupe
    par similarité de préfixe (200 premiers chars du résumé) et on garde
    celui avec le résumé le plus long (= le plus informatif).

    O(n²) — adapté pour des catégories < 100 articles. Pour de plus gros
    volumes, pré-calculer et cacher le résultat.
    """
    from difflib import SequenceMatcher
    kept = []
    seen_prefixes = []  # (prefix_lowered, index_in_kept)
    for a in articles:
        s = (a.get("summary") or "").strip()[:200].lower()
        if not s:
            continue
        is_dup = False
        for k, prefix in enumerate(seen_prefixes):
            sim = SequenceMatcher(None, s, prefix).ratio()
            if sim > threshold:
                # Doublon : garder celui qui a le résumé le plus long
                if len(a.get("summary") or "") > len(kept[k].get("summary") or ""):
                    seen_prefixes[k] = s
                    kept[k] = a
                is_dup = True
                break
        if not is_dup:
            seen_prefixes.append(s)
            kept.append(a)
    return kept


def get_flash_articles(paths: dict, category: str, since_minute_of_day: int = 0) -> List[dict]:
    """
    Récupère les articles d'aujourd'hui depuis `since_minute_of_day` minutes
    après minuit, filtrés par catégorie. Pour les Flashs spécialisés.

    since_minute_of_day: nombre de minutes après 00:00 (ex: 0 = depuis minuit,
    600 = depuis 10:00, etc.)
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = today_start + timedelta(minutes=since_minute_of_day)
    today = now.strftime("%Y%m%d")
    articles_root = paths["data"] / "articles"

    if not articles_root.exists():
        return []

    # Collecte les fichiers JSON du jour (2 formats possibles)
    json_files = []
    sub_dir = articles_root / today
    if sub_dir.exists():
        json_files.extend(sub_dir.glob("*.json"))
    single_file = articles_root / f"{today}_articles.json"
    if single_file.exists():
        json_files.append(single_file)

    found = []
    for f in json_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            for art in data:
                # Filtre par catégorie
                if (art.get("category") or "").strip() != category:
                    continue
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

    # Déduplication par hash
    seen = set()
    unique = []
    for art in found:
        h = art.get("hash", "")
        if h and h in seen:
            continue
        if h:
            seen.add(h)
        unique.append(art)

    # Tri par importance (longueur résumé desc) puis timestamp desc
    unique.sort(key=lambda a: (len(a.get("summary") or ""), a.get("timestamp", "")), reverse=True)

    # Déduplication par similarité (les news rapportées par plusieurs sources
    # ont des résumés très proches mais des hash différents). O(n²) mais
    # adapté pour des catégories < 100 articles.
    unique = dedup_articles_by_similarity(unique, threshold=0.7)

    # Cap à 40 articles max (catégories trop fournies comme geopolitique 80+
    # ou sport 60+ donneraient un prompt > 32k chars). Le tri par longueur
    # de résumé descendant a déjà mis les articles les plus informatifs en
    # premier, donc on garde les 40 meilleurs.
    MAX_FLASH_ARTICLES = 40
    if len(unique) > MAX_FLASH_ARTICLES:
        unique = unique[:MAX_FLASH_ARTICLES]

    return unique


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

def _format_articles_for_prompt(articles: List[dict], max_summary_chars: int = 400) -> str:
    """Formate les articles pour le prompt LLM (titre + résumé).

    `max_summary_chars` : tronque les résumés plus longs que cette limite
    (avec "…" en suffixe). Augmenter cette valeur donne au LLM plus de
    contexte par article, au prix d'un prompt plus gros.
    """
    lines = []
    for i, a in enumerate(articles, 1):
        title = (a.get("title") or "").strip()
        summary = (a.get("summary") or "").strip()
        cat = a.get("category", "monde")
        source = a.get("source", "")
        # Coupe les résumés trop longs pour le prompt
        if len(summary) > max_summary_chars:
            summary = summary[:max_summary_chars] + "…"
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
    tolerance = config.get("tolerance_words", 300)
    structure = config.get("structure", {})
    total_news_words = sum(
        s.get("target_words", 0) for k, s in structure.items()
        if k.startswith("news_block")
    )
    news_blocks = [k for k in structure if k.startswith("news_block")]

    # Nombre d'articles a traiter dans le bulletin (= combien de news
    # seront parlees). Lu depuis config.articles_in_bulletin (defaut 9).
    # Avant 2026-07-28 etait hardcode '4 dans news_block_1, 3 dans
    # news_block_2, 2 dans news_block_3 = 9 articles uniques'.
    # Maintenant parametre : si total=12 -> 5+4+3=12, si total=15 -> 7+5+3=15.
    total_articles_in_bulletin = config.get("articles_in_bulletin", 9)
    # Repartition 45% / 35% / 20% sur les 3 news_block
    n_block_1 = max(1, round(total_articles_in_bulletin * 0.45))
    n_block_2 = max(1, round(total_articles_in_bulletin * 0.35))
    n_block_3 = max(1, total_articles_in_bulletin - n_block_1 - n_block_2)

    articles_text = _format_articles_for_prompt(articles)

    # Choisit 1 intro et 1 outro au hasard parmi les options
    # On remplit {{heure}} et {{date}} maintenant pour que le LLM n'ait pas à le faire
    now_str = datetime.now().strftime("%Hh%M")
    date_str = datetime.now().strftime("%d/%m/%Y")
    intro = random.choice(intros).format(heure=now_str, date=date_str) if intros else f"Bonjour à tous, il est {now_str}."
    outro = random.choice(outros).format(heure=now_str, date=date_str) if outros else "C'est tout pour ce bulletin."

    # Langue cible : on prend celle de service.default_language via llm_client.
    # C'est la meme cle que pour les titres/articles, donc la radio parle
    # dans la meme langue que le site.
    # Le try/except gere le cas ou OllamaClient n'est pas encore init
    # (process/thread separe, cas observe en prod le 2026-07-28).
    # Si ca plante, on fallback sur 'francais' pour ne pas crasher tout
    # le bulletin : mieux un bulletin en francais qu'un bulletin absent.
    try:
        from modules.core.llm_client import get_language
        lang = get_language()
        if not lang:
            lang = "francais"
    except Exception:
        lang = "francais"

    prompt = f"""Tu es un journaliste radio professionnel. Tu produis un BULLETIN D'INFORMATION qui couvre les 30 dernières minutes d'actualité. Le bulletin est diffusé sur une radio d'information continue.

CONTEXTE :
- Heure du bulletin : ~{datetime.now().strftime("%Hh%M")}
- Langue de diffusion : {lang} (OBLIGATOIRE : tout le bulletin doit etre en {lang}, JAMAIS une autre langue)
- Durée cible du bulletin : 13-15 minutes de parole (~{target} mots, ±{tolerance})
- Fenêtre temporelle couverte : 30 dernières minutes d'actualité
- Audience : auditeur {lang} cultivé
- Style : professionnel, neutre, factuel

ARTICLES À TRAITER (les plus importants en premier) :
{articles_text}

STRUCTURE DU BULLETIN :
1. INTRO ({structure.get("intro", {}).get("target_words", 100)} mots) — Voix 1
2. {news_blocks[0] if len(news_blocks) > 0 else "news_block_1"} ({structure.get("news_block_1", {}).get("target_words", 600)} mots) — Voix 1
   {n_block_1} news principales
3. TRANSITION ({structure.get("transition", {}).get("target_words", 30)} mots) — Voix 2
4. {news_blocks[1] if len(news_blocks) > 1 else "news_block_2"} ({structure.get("news_block_2", {}).get("target_words", 500)} mots) — Voix 2
   {n_block_2} news secondaires
5. TRANSITION 2 ({structure.get("transition_2", {}).get("target_words", 30)} mots) — Voix 1
6. {news_blocks[2] if len(news_blocks) > 2 else "news_block_3"} ({structure.get("news_block_3", {}).get("target_words", 400)} mots) — Voix 2
   {n_block_3} news de fond
7. OUTRO ({structure.get("outro", {}).get("target_words", 100)} mots) — Voix 1

CONSIGNES STRICTES :
- Langue de sortie : {lang} OBLIGATOIRE. Reformule les expressions francaises donnees ci-dessous dans le style radio {lang} adapte (par exemple "France Inter" peut etre remplace par "BBC" ou "NPR" selon le style).
- Utilise les BALISES [VOIX1] et [VOIX2] pour marquer les changements de voix
  Format : [VOIX1] texte parlé par la voix 1. [VOIX2] texte parlé par la voix 2. [VOIX1] retour voix 1.
- INTRO (Voix 1) : COMMENCE OBLIGATOIREMENT par lister les {total_articles_in_bulletin} sujets
  du bulletin en 1 phrase introductive ("Bonjour, voici les {total_articles_in_bulletin}
  informations qui ont marque l'actualité des 30 dernieres minutes...")
  PUIS liste les {total_articles_in_bulletin} sujets en une ligne chacun (sujet + categorie).
  Exemple :
    "Bonjour, il est 21h, voici votre bulletin d'information.
     Au programme aujourd'hui :
     1. Trump rencontre Netanyahou a Washington (geopolitique)
     2. Incendie dans les Landes (france)
     3. ..."
  Cette liste doit correspondre EXACTEMENT aux {n_block_1}+{n_block_2}+{n_block_3}
  sujets que tu developpes ensuite. Pas de surprise, pas de substitution
  en cours de route : si tu annonces un sujet, tu le developpes, point.
- OUTRO (Voix 1) : termine en {lang} en rappelant brievement les themes
  principaux, puis utilise une variation de — "{outro}"
- TRANSITIONS (Voix 2 ou Voix 1) : varie les expressions, n'utilise pas la même deux fois
- Classement par ordre d'importance décroissante (grosse news en premier)
- Chaque article n'est traité qu'UNE SEULE FOIS dans tout le bulletin :
  * Tu choisis une fois dans quel bloc (intro, transition, outro) tu vas l'utiliser
  * Tu ne dois JAMAIS reparler d'un même sujet plus tard (pas de redite)
  * Si tu as parlé du jugement Le Pen dans la news principale, tu ne le reprends
    pas dans les news secondaires, même sous un autre angle
  * Le nombre d'articles dans chaque bloc doit être compté : {n_block_1} dans news_block_1,
    {n_block_2} dans news_block_2, {n_block_3} dans news_block_3 = {total_articles_in_bulletin} articles uniques au total
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

def split_script_by_voice(script):
    """Strict structure, preserving initial text's historical VOIX1 default."""
    if not isinstance(script, str) or not script.strip() or len(script) > 128000:
        raise AudioIntegrityError('invalid_script')
    parts = re.split(r'(\[[^\]]*\])', script)
    segments, current, awaiting = [], '[VOIX1]', False
    for part in parts:
        if not part.strip():
            continue
        if part.startswith('[') and part.endswith(']'):
            if part not in VOICE_TAGS or awaiting:
                raise AudioIntegrityError('invalid_or_empty_voice_segment')
            current, awaiting = part, True
        else:
            text = part.strip()
            if '[' in text or ']' in text or not any(c.isalnum() for c in text):
                raise AudioIntegrityError('malformed_spoken_text')
            segments.append((current, text))
            awaiting = False
    if awaiting or not segments or len(segments) > 256:
        raise AudioIntegrityError('empty_or_excessive_segments')
    return segments


# ─────────────────────────────────────────────────────────────────────────────
#  LLM call wrapper
# ─────────────────────────────────────────────────────────────────────────────

def generate_bulletin_script(articles: List[dict], config: dict,
                              intros: List[str], transitions: List[str],
                              outros: List[str]) -> Optional[str]:
    """Appelle le LLM pour générer le script balisé. Retourne None si erreur."""
    # IMPORTANT : initialiser Ollama AVANT de construire le prompt, car
    # build_bulletin_prompt appelle get_language() qui crash si le
    # client Ollama n'est pas initialise (process/thread separe).
    # Ce bug est apparu quand on a ajoute la langue dynamique au
    # prompt (commit 3d4a40f) : avant, le prompt etait 100% hardcode
    # francais et get_language() n'etait pas appele.
    try:
        from modules.core.llm_client import init_ollama as _init_ollama
        _init_ollama(config)
    except Exception as e:
        logger.debug("LLM initialization unavailable")
    prompt = build_bulletin_prompt(articles, config, intros, transitions, outros)
    try:
        # Timeout long car génération ~1500 mots
        # 2026-07-28 : timeout configurable via config.llm.timeout_bulletin
        # (defaut 900s = 15 min). Avant : hardcode 300s = 5 min, trop
        # court quand le fetch occupe le GPU en parallele.
        timeout = config.get("llm", {}).get("timeout_bulletin", 900)
        out = ollama_call(prompt, timeout=timeout, caller="bulletin")
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
        logger.error("Bulletin LLM generation failed")
        return None


def validate_bulletin(script, target, tolerance):
    try:
        segments = split_script_by_voice(script)
    except AudioIntegrityError:
        return False, 0, 'structure invalide'
    count = sum(len(text.split()) for _, text in segments)
    if count < target - tolerance:
        return False, count, 'trop court'
    if count > target + tolerance:
        return False, count, 'trop long'
    return True, count, 'ok'


# ─────────────────────────────────────────────────────────────────────────────
#  Génération TTS par segment
# ─────────────────────────────────────────────────────────────────────────────

def synthesize_segment(text, voice, output_path, *, timeout=90):
    import asyncio
    import edge_tts
    try:
        async def run():
            communicate = edge_tts.Communicate(text, voice)
            await asyncio.wait_for(communicate.save(str(output_path)), timeout=timeout)
        asyncio.run(run())
        return output_path.is_file() and output_path.stat().st_size > 0
    except Exception:
        logger.warning('TTS attempt failed')
        return False


def synthesize_all_segments(segments, voice_map, tmp_dir, *, attempts=3, timeout=90,
                            backoff=1, max_backoff=4, manifest=None,
                            synthesizer=None, inspector=None, wait=None):
    """All mandatory segments or an exception; retry only the failing segment."""
    synthesizer, inspector, wait = synthesizer or synthesize_segment, inspector or inspect_audio, wait or time.sleep
    if not segments or not 1 <= attempts <= 5 or not 0 < timeout <= 300 or not 0 <= backoff <= max_backoff <= 30:
        raise AudioIntegrityError('invalid_segment_policy')
    records = [] if manifest is None else manifest
    if records:
        raise AudioIntegrityError('manifest_already_used')
    for index, segment in enumerate(segments):
        if not isinstance(segment, (tuple, list)) or len(segment) != 2:
            raise AudioIntegrityError('malformed_segment')
        tag, text = segment
        if not isinstance(tag, str):
            raise AudioIntegrityError('invalid_voice_tag')
        voice = voice_map.get(tag)
        if (tag not in VOICE_TAGS or not isinstance(text, str) or not text.strip()
                or not any(c.isalnum() for c in text) or not isinstance(voice, str) or not voice.strip()):
            raise AudioIntegrityError('invalid_segment_voice_or_text')
        records.append(Segment(index, tag, text, voice, Path(tmp_dir) / f"seg_{index:03d}_{tag.strip('[]')}.mp3"))
    for record in records:
        for attempt in range(1, attempts + 1):
            record.attempts, record.state = attempt, 'generating'
            record.path.unlink(missing_ok=True)
            try:
                if not synthesizer(record.text, record.voice, record.path, timeout=timeout):
                    raise AudioIntegrityError('tts_failed')
                if record.path.is_symlink() or not record.path.is_file() or not record.path.stat().st_size:
                    raise AudioIntegrityError('missing_segment_output')
                record.info = inspector(record.path)
                record.state = 'valid'
                break
            except Exception:
                record.path.unlink(missing_ok=True)
                record.state = 'failed'
                logger.warning('TTS segment %d attempt %d/%d failed', record.index, attempt, attempts)
                if attempt == attempts:
                    raise AudioIntegrityError('mandatory_segment_failed') from None
                wait(min(max_backoff, backoff * 2 ** (attempt - 1)))
    return [record.path for record in records]


# ─────────────────────────────────────────────────────────────────────────────
#  Concaténation des segments (ffmpeg)
# ─────────────────────────────────────────────────────────────────────────────

def concatenate_segments(seg_paths, output_path):
    if not seg_paths:
        return False
    list_file = output_path.with_suffix('.txt')
    success = False
    try:
        with list_file.open('w', encoding='utf-8') as out:
            for path in seg_paths:
                # ffmpeg concat quoting, including apostrophes in the parent directory.
                escaped = str(path.absolute()).replace("'", "'\\''")
                out.write(f"file '{escaped}'\n")
        result = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'concat',
            '-safe', '0', '-protocol_whitelist', 'file,pipe', '-i', str(list_file), '-map', '0:a:0', '-c', 'copy', str(output_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, timeout=120)
        success = result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0
        return success
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        list_file.unlink(missing_ok=True)
        if not success:
            output_path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
#  Mix voix + musique de fond
# ─────────────────────────────────────────────────────────────────────────────

def mix_voice_with_background(voice_path, bg_dir, volume, output_path):
    success = False
    try:
        bg_files = list(bg_dir.glob('*.mp3')) if bg_dir.exists() else []
        if not bg_files:
            shutil_move_fallback(voice_path, output_path)
            success = output_path.is_file() and output_path.stat().st_size > 0
            return success
        bg = random.choice(bg_files)
        result = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-protocol_whitelist', 'file,pipe', '-f', 'mp3', '-i', str(voice_path.resolve()),
            '-stream_loop', '-1', '-protocol_whitelist', 'file,pipe', '-f', 'mp3', '-i', str(bg.resolve()), '-filter_complex',
            f'[0:a]volume=2.0[voice];[1:a]volume={volume}[bg];[voice][bg]amix=inputs=2:duration=shortest[mix]',
            '-map', '[mix]', '-c:a', 'libmp3lame', '-b:a', '128k', str(output_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, timeout=120)
        success = result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0
        return success
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        if not success:
            output_path.unlink(missing_ok=True)


def shutil_move_fallback(src: Path, dst: Path):
    """Fallback si pas de musique : on déplace juste le fichier voix."""
    import shutil
    shutil.move(str(src), str(dst))


# ─────────────────────────────────────────────────────────────────────────────
#  Flash : bulletin court spécialisé par catégorie, à la demande
# ─────────────────────────────────────────────────────────────────────────────

def build_flash_prompt(articles: List[dict], category: str, cat_label: str,
                        intros: List[str], outros: List[str]) -> str:
    """
    Construit un prompt court (~5 min de parole) pour un Flash spécialisé
    sur une catégorie. Le LLM doit générer un script factuel qui couvre
    uniquement les articles de la catégorie, depuis minuit de la journée.
    """
    target_words = 750  # 5 min de parole
    # Tronque les résumés à 500 chars (vs 400 par défaut) pour donner au LLM
    # plus de contexte par article. Avec dedup (~5% d'articles en moins) +
    # ce prompt : ~20k chars pour 40 articles tech, sous la fenêtre 32k.
    # Catégories plus fournies (geopolitique 80+, sport 60+) : ~30-40k,
    # le LLM tronquera la fin — acceptable car la fin est moins importante.
    articles_text = _format_articles_for_prompt(articles, max_summary_chars=700)

    # Choisit 1 intro et 1 outro au hasard
    now_str = datetime.now().strftime("%Hh%M")
    date_str = datetime.now().strftime("%d/%m/%Y")
    intro = random.choice(intros).format(heure=now_str, date=date_str) if intros \
        else f"Bonjour, il est {now_str}, voici le flash {cat_label}."
    outro = random.choice(outros).format(heure=now_str, date=date_str) if outros \
        else f"C'est tout pour ce flash {cat_label}."

    # Langue cible : idem que le bulletin, on prend service.default_language.
    # Try/except pour le cas ou Ollama n'est pas init dans ce thread.
    try:
        from modules.core.llm_client import get_language
        lang = get_language()
        if not lang:
            lang = "francais"
    except Exception:
        lang = "francais"

    prompt = f"""Tu es un journaliste radio professionnel. Tu produis un FLASH D'INFORMATION SPECIALISE sur la catégorie « {cat_label} ».

CONTEXTE :
- Heure du flash : ~{datetime.now().strftime("%Hh%M")}
- Langue de diffusion : {lang} (OBLIGATOIRE : tout le flash doit etre en {lang}, JAMAIS une autre langue)
- Catégorie : {cat_label} ({category})
- Durée cible : 5 minutes de parole (~{target_words} mots, ±100)
- Fenêtre temporelle : depuis minuit de la journée en cours ({date_str})
- Audience : auditeur {lang} qui veut un résumé rapide
- Style : professionnel, neutre, factuel

ARTICLES DE LA CATÉGORIE « {cat_label} » DEPUIS MINUIT :
{articles_text}

CONSIGNES STRICTES :
- Langue de sortie : {lang} OBLIGATOIRE. Reformule les expressions francaises donnees en style radio {lang} adapte.
- Utilise les BALISES [VOIX1] et [VOIX2] pour marquer les changements de voix
  Format : [VOIX1] texte parlé par la voix 1. [VOIX2] texte parlé par la voix 2.
- INTRO (Voix 1) : COMMENCE OBLIGATOIREMENT par une phrase qui annonce
  explicitement qu'il s'agit d'un FLASH SPÉCIALISÉ sur la catégorie « {cat_label} ».
  Adapte l'inspiration suivante en l'enrichissant de cette annonce, en {lang} — "{intro}"
- OUTRO (Voix 1) : termine en rappelant qu'il s'agissait du flash {cat_label.lower()},
  puis utilise une variation, en {lang}, de — "{outro}"
- Traite TOUS les articles, classés du plus important au moins important
- Format : 1-2 phrases par article, ton radio rapide et factuel
- N'invente rien : utilise UNIQUEMENT les informations données
- Pas de markdown
- Phrases courtes, terminées par un point
- Vise {target_words} mots total

FORMAT DE SORTIE :
- UNIQUEMENT le texte balisé, pas de méta-commentaires
- Commence par [VOIX1] (l'intro)
- Termine par [VOIX1] (l'outro)
"""
    return prompt


def generate_flash_script(articles: List[dict], category: str, cat_label: str,
                          config: dict, intros: List[str], outros: List[str]) -> Optional[str]:
    """Appelle le LLM pour générer le script d'un Flash spécialisé. Retourne None si erreur."""
    # Initialiser Ollama AVANT de construire le prompt (meme raison que
    # pour le bulletin : get_language() est appele dans build_flash_prompt).
    try:
        from modules.core.llm_client import init_ollama as _init_ollama
        _init_ollama(config)
    except Exception as e:
        logger.debug("LLM initialization unavailable")
    prompt = build_flash_prompt(articles, category, cat_label, intros, outros)
    try:
        # Timeout court pour un flash de 5 min
        out = ollama_call(prompt, timeout=600, caller="flash")  # 10 min max pour les gros prompts
        if not out:
            logger.warning("LLM a renvoyé un script flash vide")
            return None
        out = clean_for_tts(out)
        if not out.startswith("["):
            out = "[VOIX1] " + out
        return out
    except Exception as e:
        logger.error("Flash LLM generation failed")
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Point d'entrée principal
# ─────────────────────────────────────────────────────────────────────────────

class BulletinGenerator:
    """Génère un bulletin radio "30 minutes" complet."""

    def __init__(self, config: dict, paths: dict):
        self.config = config
        self.paths = paths
        self.bulletins_cfg = load_bulletins_config(paths)
        # Voix : on prend dans la langue cible (service.default_language)
        # depuis config.yaml.voices[lang]. Avant, on lisait bulletins.yaml
        # qui etait hardcode francais (primary: fr-FR-HenriNeural). Bug
        # rapporte en prod le 2026-07-28 : 'le bulletin est en anglais
        # mais la voix est clairement francaise'.
        #
        # Depuis le 2026-07-28 on pioche 2 voix random parmi TOUTES les
        # voix disponibles pour la langue, pour avoir de la variete audio
        # d'un bulletin a l'autre (au lieu d'entendre toujours les memes
        # voix). Si la liste ne contient qu'1 voix, on l'utilise 2 fois.
        # Si la langue n'a aucune voix, fallback sur bulletins.yaml.
        import random
        from modules.core.llm_client import get_language
        try:
            target_lang = get_language()
        except Exception:
            target_lang = "francais"
        lang_key = self._lang_to_config_key(target_lang)
        radio_voices = config.get("radio", {}).get("voices", {})
        voices_for_lang = list(radio_voices.get(lang_key, []))
        voices_cfg = self.bulletins_cfg.get("voices", {})
        if len(voices_for_lang) >= 2:
            # Pioche 2 voix differentes au hasard
            primary, secondary = random.sample(voices_for_lang, 2)
        elif len(voices_for_lang) == 1:
            primary = secondary = voices_for_lang[0]
        else:
            # Fallback sur bulletins.yaml (compatibilite anciennes configs)
            primary   = voices_cfg.get("primary",   "fr-FR-HenriNeural")
            secondary = voices_cfg.get("secondary", "fr-FR-DeniseNeural")
        voices_extra = self.bulletins_cfg.get("voices_extra", {})
        self.voice_map = {
            "[VOIX1]":         primary,
            "[VOIX2]":         secondary,
            "[VOIX3]":         primary,
            "[VOIX_BREAKING]": voices_extra.get("breaking", primary),
        }
        # Chemins
        self.bg_dir    = paths["root"] / paths.get("background_music", "background_music")
        self.queue_dir = paths["root"] / paths.get("audio_queue",     "audio_queue")
        self.tmp_dir   = paths["root"] / paths.get("tmp_dir",         "tmp")
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        # Log des voix choisies pour ce bulletin (debug/audit)
        logger.info("Bulletin voices selected")

    def _lang_to_config_key(self, lang: str) -> str:
        """Convertit une langue LLM (francais, english) en cle config (fr, en)."""
        if not lang:
            return "fr"
        lang = lang.lower().strip()
        aliases = {
            "francais": "fr", "français": "fr", "french": "fr",
            "english": "en", "anglais": "en",
            "deutsch": "de", "german": "de", "allemand": "de",
            "espanol": "es", "español": "es", "spanish": "es",
            "italiano": "it", "italian": "it",
            "portugues": "pt", "portuguese": "pt",
            "nederlands": "nl", "dutch": "nl",
        }
        return aliases.get(lang, lang[:2])

    def build(self, articles, script=None, *, profile='bulletin'):
        """Return only a complete, validated, atomically published MP3."""
        cfg = self.bulletins_cfg
        if len(articles) < cfg.get('min_articles', 3):
            return None
        try:
            if script is None:
                intros, transitions, outros = self._load_messages()
                script = generate_bulletin_script(articles, {**cfg, **self.config}, intros, transitions, outros)
            segments = split_script_by_voice(script)
            if profile not in ('bulletin', 'flash'):
                raise AudioIntegrityError('invalid_profile')
            target = cfg.get('flash_target_words', 750) if profile == 'flash' else cfg.get('target_words', 1500)
            tolerance = cfg.get('flash_tolerance_words', 100) if profile == 'flash' else cfg.get('tolerance_words', 200)
            policy = cfg.get('flash_length_policy', 'warn') if profile == 'flash' else cfg.get('length_policy', 'strict')
            if policy not in ('strict', 'warn'):
                raise AudioIntegrityError('invalid_length_policy')
            ok, count, _ = validate_bulletin(script, target, tolerance)
            if not ok:
                logger.warning('Script outside editorial length bounds (%d words)', count)
                if policy == 'strict':
                    return None
            generation = uuid.uuid4().hex
            # Same filesystem as the public queue; private work is never globbed as a bulletin.
            with tempfile.TemporaryDirectory(prefix='.bulletin-', dir=self.queue_dir) as tmp:
                work = Path(tmp)
                manifest = []
                paths = synthesize_all_segments(segments, self.voice_map, work,
                    attempts=int(cfg.get('tts_max_attempts', 3)), timeout=float(cfg.get('tts_timeout_seconds', 90)),
                    backoff=float(cfg.get('tts_retry_backoff', 1)), max_backoff=float(cfg.get('tts_retry_max_backoff', 4)),
                    manifest=manifest)
                if len(paths) != len(segments) or any(s.state != 'valid' for s in manifest):
                    raise AudioIntegrityError('incomplete_manifest')
                formats = {(s.info.sample_rate, s.info.channels) for s in manifest
                           if s.info.sample_rate is not None and s.info.channels is not None}
                if len(formats) > 1:
                    raise AudioIntegrityError('incompatible_segment_formats')
                voice_path, staged = work / 'voice.mp3', work / 'final.mp3'
                if not concatenate_segments(paths, voice_path):
                    raise AudioIntegrityError('concat_failed')
                voice_info = inspect_audio(voice_path)
                if formats and voice_info.sample_rate is not None and (voice_info.sample_rate, voice_info.channels) not in formats:
                    raise AudioIntegrityError('concat_format_changed')
                compatible_duration(voice_info, sum(s.info.duration for s in manifest), segments=len(manifest))
                if not mix_voice_with_background(voice_path, self.bg_dir, cfg.get('background_volume', .30), staged):
                    raise AudioIntegrityError('mix_failed')
                final_info = inspect_audio(staged)
                compatible_duration(final_info, voice_info.duration)
                target_path = self.queue_dir / f"bulletin_{datetime.now():%Y%m%d_%H%M%S}_{generation}.mp3"
                return publish_audio(staged, target_path, final_info, len(manifest))
        except Exception as exc:
            reason = str(exc) if isinstance(exc, AudioIntegrityError) else type(exc).__name__
            logger.error('Bulletin generation refused (%s)', reason)
            return None

    def build_async(self, articles: List[dict], callback=None):
        """Lance la génération dans un thread."""
        def _run():
            path = self.build(articles)
            if callback:
                callback(path)
        threading.Thread(target=_run, daemon=True).start()


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
            logger.warning("Bulletin messages unavailable")
            return [], [], []
