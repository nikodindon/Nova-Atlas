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
2. {news_blocks[0] if len(news_blocks) > 0 else "news_block_1"} ({total_news_words // max(1, len(news_blocks))} mots) — Voix 1
   4-5 news principales
3. TRANSITION ({structure.get("transition", {}).get("target_words", 30)} mots) — Voix 2
4. {news_blocks[1] if len(news_blocks) > 1 else "news_block_2"} ({total_news_words // max(1, len(news_blocks))} mots) — Voix 2
   3-4 news secondaires
5. TRANSITION 2 ({structure.get("transition_2", {}).get("target_words", 30)} mots) — Voix 1
6. {news_blocks[2] if len(news_blocks) > 2 else "news_block_3"} ({total_news_words // max(1, len(news_blocks))} mots) — Voix 2
   2-3 news de fond
7. OUTRO ({structure.get("outro", {}).get("target_words", 100)} mots) — Voix 1

CONSIGNES STRICTES :
- Langue de sortie : {lang} OBLIGATOIRE. Reformule les expressions francaises donnees ci-dessous dans le style radio {lang} adapte (par exemple "France Inter" peut etre remplace par "BBC" ou "NPR" selon le style).
- Utilise les BALISES [VOIX1] et [VOIX2] pour marquer les changements de voix
  Format : [VOIX1] texte parlé par la voix 1. [VOIX2] texte parlé par la voix 2. [VOIX1] retour voix 1.
- INTRO (Voix 1) : adapte l'inspiration suivante au contexte, en {lang} — "{intro}"
- OUTRO (Voix 1) : utilise une variation, en {lang}, de — "{outro}"
- TRANSITIONS (Voix 2 ou Voix 1) : varie les expressions, n'utilise pas la même deux fois
- Classement par ordre d'importance décroissante (grosse news en premier)
- Chaque article n'est traité qu'UNE SEULE FOIS dans tout le bulletin :
  * Tu choisis une fois dans quel bloc (intro, transition, outro) tu vas l'utiliser
  * Tu ne dois JAMAIS reparler d'un même sujet plus tard (pas de redite)
  * Si tu as parlé du jugement Le Pen dans la news principale, tu ne le reprends
    pas dans les news secondaires, même sous un autre angle
  * Le nombre d'articles dans chaque bloc doit être compté : 4 dans news_block_1,
    3 dans news_block_2, 2 dans news_block_3 = 9 articles uniques au total
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
        logger.debug(f"init_ollama deja fait ou echoue: {e}")
    prompt = build_bulletin_prompt(articles, config, intros, transitions, outros)
    try:
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
        logger.debug(f"init_ollama deja fait ou echoue: {e}")
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
        logger.error(f"Erreur génération script flash LLM : {e}")
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
        logger.info(f"[bulletin] Voix choisies pour '{lang_key}' : "
                    f"VOIX1={primary}, VOIX2={secondary}")

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

    def build(self, articles: List[dict], script: Optional[str] = None) -> Optional[Path]:
        """
        Génère un bulletin complet (collecte LLM + TTS + mix).
        Retourne le chemin du mp3 final ou None.

        Si `script` est fourni, on l'utilise tel quel (skippe l'appel LLM).
        Utilisé par /api/flash pour injecter un script généré avec un
        prompt spécialisé par catégorie.
        """
        cfg = self.bulletins_cfg

        # Filtre : nombre minimum d'articles
        if len(articles) < cfg.get("min_articles", 3):
            logger.info(
                f"⏭️ Bulletin skippé : {len(articles)} article(s) < "
                f"{cfg.get('min_articles', 3)} minimum"
            )
            return None

        if script is None:
            logger.info(f"📡 Génération bulletin 10min ({len(articles)} articles)")

            # Charge les intros/transitions/outros depuis messages.yaml
            intros, transitions, outros = self._load_messages()

            # 1) Prompt LLM — passe self.config (la config globale qui a la
            # section llm) et non cfg (qui vient de bulletins.yaml et n'a pas
            # la section llm, ce qui forcerait les defaults ollama/mistral).
            script = generate_bulletin_script(articles, self.config, intros, transitions, outros)
            if not script:
                return None
        else:
            logger.info(f"📡 Génération bulletin 10min ({len(articles)} articles, script pré-généré)")

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
