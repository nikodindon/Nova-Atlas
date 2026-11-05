#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/fetch/atlas_fetch.py — Nova-Atlas
Collecte RSS, fetch contenu articles, résumé court via Ollama.
Stockage incrémental dans data/articles/YYYYMMDD_articles.json

Refactorisé depuis atlas_fetch.py (pblart/nova-media) :
  - Enveloppé dans la classe ArticleFetcher(config)
  - Round-robin et formats JSON publics conservés
  - Journal de reprise et écritures atomiques : voir docs/ARTICLE_INTEGRITY.md
  - Les chemins viennent de config.paths.*
  - Ollama passe par modules.core.llm_client
"""

import hashlib
import logging
import re
import time
import math
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from modules.fetch.article_state import (atomic_json, backup, read_json, calendar_day,
    valid_article, collection_lock, StorageError, ArticleAttemptError)
from pathlib import Path
from urllib.parse import urlparse

from modules.http import external as source_http
from bs4 import BeautifulSoup
from lxml import etree

from modules.core.llm_client import init_ollama, ollama_call, get_language, get_fetch_timeout


def _rss_xml_parser(**kwargs):
    # XML/DTD entities are another potential outbound path outside the HTTP client.
    kwargs.update(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)
    return etree.XMLParser(**kwargs)

# ─── SOURCES RSS (chargées depuis config/feeds.yaml) ───────────────────────
# Avant : dict Python hardcodé (90+ lignes).
# Maintenant : fichier YAML externe éditable + UI /config/feeds à venir.
# Pour ajouter/retirer un flux : modifier config/feeds.yaml puis POST /config/restart.

DEFAULT_FEEDS_PATH = Path("config/feeds.yaml")

# Cache module-level pour éviter de relire le fichier à chaque fetch
_cached_feeds: dict | None = None
_cached_feeds_mtime: float = 0


def _load_rss_sources(feeds_path: str | Path = None) -> dict:
    """
    Charge la liste des flux depuis config/feeds.yaml.
    Met en cache par mtime pour éviter de relire si le fichier n'a pas changé.
    En cas d'erreur, fallback sur un dict vide (le fetch tournera mais ne
    ramènera rien — mieux qu'un crash).
    """
    global _cached_feeds, _cached_feeds_mtime
    path = Path(feeds_path or DEFAULT_FEEDS_PATH)
    if not path.exists():
        return {}

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}

    if _cached_feeds is not None and mtime == _cached_feeds_mtime:
        return _cached_feeds

    try:
        from modules.core.feeds_loader import load_feeds
        feeds = load_feeds(path)
    except Exception as e:
        import logging
        logging.getLogger("nova.fetch").warning(
            f"Impossible de charger {path}: {e} — fetch tournera à vide"
        )
        feeds = {}

    _cached_feeds = feeds
    _cached_feeds_mtime = mtime
    return feeds


def invalidate_feeds_cache():
    """Force le rechargement au prochain appel (utilisé après /config/restart)."""
    global _cached_feeds, _cached_feeds_mtime
    _cached_feeds = None
    _cached_feeds_mtime = 0


# Backward-compat : RSS_SOURCES reste accessible comme un dict, mais
# il est maintenant calculé à l'import. Les anciens imports continuent
# de fonctionner mais le contenu vient de feeds.yaml.
def _get_rss_sources() -> dict:
    return _load_rss_sources()

# Au premier import, on log combien de flux on a chargé
def _log_rss_sources_count():
    import logging
    sources = _load_rss_sources()
    total = sum(len(urls) for urls in sources.values())
    logging.getLogger("nova.fetch").info(
        f"RSS_SOURCES: {len(sources)} catégories, {total} flux actifs "
        f"(depuis config/feeds.yaml)"
    ) if total else None

# Note: l'ancien dict RSS_SOURCES ci-dessous est conservé temporairement
# comme fallback si config/feeds.yaml n'existe pas. Sera supprimé en v0.3.
RSS_SOURCES = {
    "geopolitique": [],
    "economie": [],
    "crypto": [],
    "tech": [],
    "france": [],
    "monde": [],
    "science": [],
    "environnement": [],
    "societe": [],
    "culture": [],
    "sport": [],
}

MIN_TITLE_LEN     = 25
MAX_ARTICLE_CHARS = 3500
FETCH_TIMEOUT_HTTP = 10


# ─── NETTOYAGE DES RÉSUMÉS ────────────────────────────────────────────────────

def _clean_summary(text: str) -> str:
    """
    Supprime les artefacts de répétition / bégaiement produits par Ollama.

    Problème racine : Ollama (via subprocess CLI) stream sa réponse et
    coupe parfois en milieu de mot avec un saut de ligne, puis recommence
    le mot complet sur la ligne suivante.
    Exemples réels observés :
      "ainsi u\\nune étape"          → "ainsi une étape"
      "crypto-mon\\ncrypto-monnaies" → "crypto-monnaies"
      "de l\\nl'offre totale"        → "de l'offre totale"
      "des e\\nexperts"              → "des experts"
      "contre l失败的establishm\\nestablishment" → "contre l'établissement"

    Cas traités :
    0. TRONCATURE + \n + mot complet (PRIORITAIRE — nettoie d'abord)
    1. Préfixe≥2 + mot complet  : "avr avril"  → "avril"
    2. Consonne isolée + mot    : "r responsables" "n ne" "b baissé" → mot complet
    3. Article/prép. doublé     : "à à" "de de" → un seul
    4. Mot entier répété        : "titre titre" → un seul
    5. Nombre partiel           : "202 2026" → "2026"
    6. Parenthèse non fermée    : "(heu (heure)" → "(heure)"
    7. Consonne tronquée en fin → supprime
    8. Caractères non-latins parasites (CJK, etc.) dans texte fr/en
    """
    import re as _re

    if not text:
        return text

    # ── 8. Nettoyage CJK / caractères non-latins parasites ────────────────────
    # Ollama injecte parfois des fragments CJK (bug modèle)
    text = _re.sub(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uff00-\uffef]', '', text)

    # ── 0. TRONCATURE + \n + reprise du mot (CAS PRINCIPAL) ──────────────────
    # Pattern : fragment de mot (1+ chars) + \n + mot complet qui commence
    # par ce même fragment. Le fragment tronqué peut contenir un tiret.
    # Ex: "crypto-mon\ncrypto-monnaies" → "crypto-monnaies"
    # Ex: "u\nune" → "une"
    # Ex: "l\nl'offre" → "l'offre"
    # Ex: "e\nexperts" → "experts"
    # Ex: "d'\nd'une" → "d'une"
    changed = True
    while changed:
        changed = False
        # Cas avec apostrophe : "l\nl'offre" → "l'offre"
        new = _re.sub(
            r"(?<!\S)([A-Za-zÀ-ÿ'-]{1,20})\n\1([A-Za-zÀ-ÿà-ÿ'-]+)",
            r"\1\2", text
        )
        if new != text:
            text = new
            changed = True
            continue
        # Cas standard : "mottronqué\nmotcomplet"
        # Le fragment tronqué (2-30 chars) est un préfixe du mot suivant
        new = _re.sub(
            r'(?<!\S)([A-Za-zÀ-ÿ]{2,30})\n\1([A-Za-zÀ-ÿ]+)',
            r'\1\2', text
        )
        if new != text:
            text = new
            changed = True
            continue
        # Cas espace+newline : "obtenu \n54%" → "obtenu 54%"
        # (espace avant \n suivi de texte)
        new = _re.sub(r' \n(\S)', r' \1', text)
        if new != text:
            text = new
            changed = True

    # Normalise les sauts de ligne résiduels en espaces
    text = text.replace('\n', ' ')
    # Nettoie les espaces multiples
    text = _re.sub(r'  +', ' ', text)

    # ── 6. Parenthèse orpheline ───────────────────────────────────────────────
    text = _re.sub(r'\([A-Za-zÀ-ÿ]{2,6}\s+\(', '(', text)
    text = _re.sub(r'\s*\([^)]*$', '', text.rstrip())

    # ── 1. Préfixe≥2 + mot complet : "avr avril" "heu heure" ─────────────────
    changed = True
    while changed:
        changed = False
        new = _re.sub(r'\b([A-Za-zÀ-ÿ]{2,})\s+(\1[A-Za-zÀ-ÿ]+)', r'\2', text)
        if new != text:
            text = new
            changed = True

    # ── 5. Nombre partiel : "202 2026" → "2026" ───────────────────────────────
    text = _re.sub(r'\b(\d{2,})\s+\1(\d+)\b', r'\1\2', text)

    # ── 2. Consonne seule + mot : "r responsables" "n ne" "b baissé" ──────────
    _consonnes = r'(?:(?<=\s)|(?<=\())([bcdfghjklmnpqrstvwxzBCDFGHJKLMNPQRSTVWXZ])\s+(\1[A-Za-zÀ-ÿ]+)'
    text = _re.sub(_consonnes, r'\2', text)

    # ── 3. Article/préposition doublé : "à à" → "à" ───────────────────────────
    text = _re.sub(
        r'\b(à|de|du|le|la|les|un|une|des|au|aux|en)\s+\1\b',
        r'\1', text, flags=_re.IGNORECASE
    )

    # ── 4. Mot entier répété ≥2 chars : "titre titre" ─────────────────────────
    changed = True
    while changed:
        changed = False
        new = _re.sub(r'\b([A-Za-zÀ-ÿ]{2,})\s+\1\b', r'\1', text)
        if new != text:
            text = new
            changed = True

    # ── 7. Consonne(s) tronquée(s) en fin de texte ────────────────────────────
    text = text.rstrip()
    words = text.split()
    if words:
        last = words[-1]
        if (len(last) <= 2
                and last.isalpha()
                and _re.match(r'^[bcdfghjklmnpqrstvwxz]+$', last.lower())):
            text = " ".join(words[:-1])

    return text.strip()


# ─── CLASSE PRINCIPALE ────────────────────────────────────────────────────────

class ArticleFetcher:
    """
    Collecte RSS + résumé Ollama.
    Équivalent de run_fetch_cycle() mais encapsulé pour Nova-Atlas.
    """

    def __init__(self, config: dict, *, clock=None):
        self._clock = clock or datetime.now
        self._config = config   # conservé pour reload à chaud
        self._apply_config(config)
        init_ollama(config)
        self.log = logging.getLogger("nova.fetch")

    def _apply_config(self, config: dict):
        """Applique (ou reapplique) la config — appelé au __init__ et au reload."""
        paths = config.get("paths", {})
        self.data_dir  = Path(paths.get("articles_dir", "data/articles"))
        self.seen_file = Path(paths.get("data_dir", "data")) / "seen_hashes.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        rss_cfg = config.get("rss", {})
        self.max_per_feed = int(rss_cfg.get("max_articles_per_feed", 8))
        # 0 = pas de cap par catégorie. Active via config: rss.max_per_category: 8
        self.max_per_category = int(rss_cfg.get("max_per_category", 0))

        # Réparation optionnelle des anciennes entrées publiées sans résumé.
        # Les reprises des nouveaux articles sont indépendantes de ce drapeau.
        fetch_cfg = config.get("fetch", {})
        self.retry_summaries = fetch_cfg.get("retry_summaries", False)
        self.max_attempts = max(1, min(20, int(fetch_cfg.get('max_attempts', 3))))
        self.retry_base = max(1, min(86400, int(fetch_cfg.get('retry_base_seconds', 1800))))
        self.retry_cap = max(self.retry_base, min(604800, int(fetch_cfg.get('retry_max_seconds', 21600))))
        self.state_file = self.seen_file.with_name('article_processing.json')

    def reload_config(self, config: dict):
        """Recharge la config à chaud sans redémarrer le processus."""
        self._config = config
        self._apply_config(config)
        # Recharge aussi le client Ollama (modèle peut avoir changé)
        from modules.core.llm_client import init_ollama as _init
        _init(config)
        # Invalide le cache feeds.yaml pour forcer le rechargement
        # des flux RSS au prochain cycle.
        invalidate_feeds_cache()
        self.log.info("[FETCH] Config rechargée à chaud.")

    # ── Seen hashes ───────────────────────────────────────────────────────────

    def _now(self):
        return getattr(self, '_clock', datetime.now)()

    def _recent(self, value):
        try:
            return 0 <= (self._now().date() - calendar_day(value)).days < 7
        except (ValueError, TypeError):
            return False

    def _load_seen(self):
        return {h for h, day in read_json(self.seen_file, dict).items() if self._recent(day)}

    def _save_seen(self, seen):
        old = read_json(self.seen_file, dict)
        for day in old.values():
            try:
                anomalous = calendar_day(day) > self._now().date()
            except (ValueError, TypeError):
                anomalous = True
            if anomalous:
                backup(self.seen_file)
                break
        existing = {h: day for h, day in old.items() if self._recent(day)}
        for h in seen:
            existing.setdefault(h, self._now().strftime('%Y%m%d'))
        atomic_json(self.seen_file, existing)

    @staticmethod
    def _hash(url: str) -> str:
        return hashlib.md5(url.strip().encode()).hexdigest()

    # ── Fichier articles du jour ───────────────────────────────────────────────

    def _today_file(self):
        day = getattr(self, '_active_day', None) or self._now().strftime('%Y%m%d')
        return self.data_dir / f'{day}_articles.json'

    def _load_today(self):
        return read_json(self._today_file(), list)

    def _save_today(self, articles):
        target = self._today_file()
        read_json(target, list)
        atomic_json(target, articles)

    # ── RSS fetch ─────────────────────────────────────────────────────────────

    def _fetch_rss(self, url: str) -> list:
        try:
            r = source_http.get(url, timeout=FETCH_TIMEOUT_HTTP)
            r.raise_for_status()
            soup  = BeautifulSoup(r.content, "xml", parser=_rss_xml_parser)
            items = []
            for item in soup.find_all("item")[:self.max_per_feed]:
                title_tag = item.find("title")
                link_tag  = item.find("link")
                pub_tag   = item.find("pubDate")
                title = title_tag.get_text().strip() if title_tag else ""
                link  = link_tag.get_text().strip()  if link_tag  else ""
                pub   = pub_tag.get_text().strip()   if pub_tag   else ""
                if title and link:
                    items.append({"title": title, "link": link, "pub_date": pub})
            return items
        except Exception as e:
            self.log.debug("RSS retrieval failed (%s)", type(e).__name__)
            return []

    # ── Article content ───────────────────────────────────────────────────────

    @staticmethod
    def _fetch_content(url: str, *, raise_errors=False) -> str:
        try:
            r = source_http.get(url, timeout=FETCH_TIMEOUT_HTTP)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            for tag in soup(["script","style","nav","header","footer",
                              "aside","form","iframe","noscript"]):
                tag.decompose()
            for sel in ["article","main","[role='main']",".article-body",
                         ".post-content",".entry-content",".content"]:
                c = soup.select_one(sel)
                if c:
                    text = c.get_text(separator="\n", strip=True)
                    if len(text) > 200:
                        return text[:MAX_ARTICLE_CHARS]
            paras = [p.get_text(strip=True) for p in soup.find_all("p")
                     if len(p.get_text(strip=True)) > 50]
            return "\n".join(paras)[:MAX_ARTICLE_CHARS]
        except Exception:
            if raise_errors:
                raise
            return ""

    # ── Résumé Ollama ─────────────────────────────────────────────────────────

    def _summarize(self, title: str, content: str, category: str) -> str:
        if not content.strip():
            return ""
        from modules.core.llm_cache import cache_key as _cache_key
        # La clé ne dépend PAS du prompt (qui peut évoluer) ni de la
        # catégorie (peut être reclassifiée). Uniquement du contenu
        # de l'article (title + content tronqué = signature stable).
        key = _cache_key(title, content[:500])
        lang = get_language()
        prompt = (
            f"Tu es un journaliste de qualité. Résume cet article de manière claire et complète.\n"
            f"Catégorie : {category}\n"
            f"Titre : {title}\n\n"
            f"Contenu :\n{content[:3000]}\n\n"
            f"Consignes :\n"
            f"- Commence par l'information LA PLUS IMPORTANTE (qui, quoi, où, quand)\n"
            f"- Donne les détails clés (scores, chiffres, dates, noms)\n"
            f"- Ajoute le contexte nécessaire pour comprendre l'article\n"
            f"- Ton neutre et factuel\n"
            f"- Réponds en {lang}\n"
            f"- N'invente rien qui ne soit pas dans le texte\n"
            f"- Texte brut, sans markdown : pas d'astérisques (**), pas de dièses (#), "
            f"pas de backticks (`), pas de liens [texte](url). Juste des phrases.\n\n"
            f"Résumé :"
        )
        output = ollama_call(prompt, timeout=get_fetch_timeout(),
                             caller="fetch", cache_key=key)
        if not output:
            self.log.warning("LLM summary empty or timed out")
            return output
        return _clean_summary(output)

    # ── Retry résumés en attente ───────────────────────────────────────────────

    # Filet de securite multi-langues : pour chaque langue cible, on
    # detecte les mots tres specifiques des AUTRES langues qui ne
    # devraient PAS apparaitre. Si on en trouve, c'est que le LLM a
    # traduit vers la mauvaise langue.
    #
    # Langues latines/syllabiques couvertes par mots-cles :
    #   - francais, english, deutsch, espanol, italiano, portuges, nederlands
    # Langues non-latines couvertes par regex Unicode (cf _NON_LATIN_SCRIPT_RE) :
    #   - russe, chinois, japonais, coreen, arabe, hindi
    _LANG_KEYWORDS = {
        "francais":  (" the ", " and ", " with ", " this ", " that ", " are ", " have ",
                      " from ", " they ", " been ", " der ", " die ", " das ", " und ",
                      " ist ", " nicht ", " el ", " la ", " los ", " que ", " con ",
                      " il ", " lo ", " gli ", " che ", " per ", " o ", " os ", " as ",
                      " que ", " com ", " het ", " een ", " van "),
        "french":    (" the ", " and ", " with ", " this ", " that ", " are ", " have ",
                      " from ", " they ", " been ", " der ", " die ", " das ", " und ",
                      " ist ", " nicht ", " el ", " la ", " los ", " que ", " con ",
                      " il ", " lo ", " gli ", " che ", " per ", " o ", " os ", " as ",
                      " que ", " com ", " het ", " een ", " van "),
        "english":   (" le ", " la ", " les ", " des ", " une ", " est ", " sont ",
                      " avec ", " pour ", " dans ", " der ", " die ", " das ", " und ",
                      " ist ", " el ", " la ", " los ", " que ", " con ", " il ", " gli ",
                      " che ", " o ", " os ", " com ", " het ", " een ", " van "),
        "deutsch":   (" the ", " and ", " with ", " is ", " are ", " le ", " la ", " les ",
                      " une ", " est ", " avec ", " pour ", " dans ", " el ", " la ",
                      " los ", " que ", " con ", " il ", " gli ", " che ", " o ", " os ",
                      " com ", " het ", " een ", " van "),
        "espanol":   (" the ", " and ", " with ", " is ", " are ", " le ", " la ", " les ",
                      " une ", " est ", " avec ", " pour ", " dans ", " der ", " die ",
                      " das ", " und ", " ist ", " il ", " gli ", " che ", " o ", " os ",
                      " com ", " het ", " een ", " van "),
        "italiano":  (" the ", " and ", " with ", " is ", " are ", " le ", " la ", " les ",
                      " une ", " est ", " avec ", " pour ", " dans ", " der ", " die ",
                      " das ", " und ", " ist ", " el ", " la ", " los ", " que ", " con ",
                      " o ", " os ", " com ", " het ", " een ", " van "),
        "portugues": (" the ", " and ", " with ", " is ", " are ", " le ", " la ", " les ",
                      " une ", " est ", " avec ", " pour ", " dans ", " der ", " die ",
                      " das ", " und ", " ist ", " el ", " la ", " los ", " que ", " con ",
                      " il ", " gli ", " che ", " het ", " een ", " van "),
        "nederlands":(" the ", " and ", " with ", " is ", " are ", " le ", " la ", " les ",
                      " une ", " est ", " avec ", " pour ", " dans ", " der ", " die ",
                      " das ", " und ", " ist ", " el ", " la ", " los ", " que ", " con ",
                      " il ", " gli ", " che ", " o ", " os ", " com "),
    }

    # Regex pour les langues a ecriture non-latine (cyrillique, CJK, arabe, etc.)
    _NON_LATIN_SCRIPT_RE = re.compile(
        "[Ѐ-ӿ぀-ゟ゠-ヿ一-鿿가-힯؀-ۿ֐-׿ऀ-ॿ฀-๿]"
    )

    def _is_wrong_language(self, text: str, target_lang: str) -> bool:
        """
        Detecte si 'text' est ecrit dans une AUTRE langue que target_lang.

        Deux strategies :
        1. Si la cible attend des caracteres non-latins (russe, CJK, etc.)
           et que text n'en a pas, c'est louche (probablement traduit
           vers le francais par defaut).
        2. Pour les langues latines : on cherche des mots-cles specifiques
           d'autres langues dans text.

        Retourne True si text semble etre dans une mauvaise langue.
        """
        if not text or not target_lang:
            return False

        target = target_lang.lower().strip()

        # Cas 1 : cible non-latine (russe, japonais, chinois, coreen, arabe, hindi)
        # Si la traduction ne contient AUCUN caractere non-latin, c'est suspect.
        non_latin_targets = {
            "russian", "русский", "japanese", "日本語",
            "chinese", "中文", "korean", "한국어",
            "arabic", "العربية", "hindi",
        }
        if target in non_latin_targets and not self._NON_LATIN_SCRIPT_RE.search(text):
            return True

        # Cas 2 : recherche de mots-cles d'autres langues latines
        # On normalise la cle de lookup
        lang_aliases = {
            "fr": "francais", "french": "francais", "francais": "francais", "français": "francais",
            "en": "english", "english": "english", "anglais": "english",
            "de": "deutsch", "german": "deutsch", "deutsch": "deutsch", "allemand": "deutsch",
            "es": "espanol", "spanish": "espanol", "espanol": "espanol", "español": "espanol",
            "it": "italiano", "italian": "italiano", "italiano": "italiano", "italien": "italiano",
            "pt": "portugues", "portuguese": "portugues", "portugues": "portugues",
            "nl": "nederlands", "dutch": "nederlands", "nederlands": "nederlands", "neerlandais": "nederlands",
        }
        key = lang_aliases.get(target, target)
        markers = self._LANG_KEYWORDS.get(key)
        if markers is None:
            # Langue non couverte par le filet (on ne sait pas detecter)
            return False
        lower = " " + text.lower() + " "
        hits = sum(1 for marker in markers if marker in lower)
        # On exige au moins 2 marqueurs pour declencher le filet. Un seul
        # mot commun (ex: " la " qui est OK en francais) declencherait
        # trop de faux positifs.
        return hits >= 2

    def _translate_title(self, title: str, force: bool = False) -> str:
        """Traduit le titre dans la langue cible.

        On traduit TOUJOURS (meme si le titre semble deja lisible),
        pour avoir une home coherente : tous les titres dans la meme
        langue que les resumes. Le cache LLM (via ollama_call) evite
        les repetitions si un meme titre repasse.

        Le parametre force est garde pour compatibilite.

        Filet de securite multi-langues : si la traduction semble etre
        dans une AUTRE langue que la cible, on fallback sur l'original.
        """
        from modules.core.llm_client import get_language
        if not title or not title.strip():
            return title
        lang = get_language()
        # Prompt renforce : on est tres explicite sur la langue cible.
        prompt = (
            f"Tu es un redacteur de titres de presse en {lang}. "
            f"Reformule ce titre de maniere naturelle en {lang} "
            f"(la langue cible est OBLIGATOIREMENT {lang}, JAMAIS une autre). "
            f"Si le titre est deja en {lang}, reformule-le legerement pour "
            f"varier le style. Reponds UNIQUEMENT avec le titre reformule, "
            f"rien d'autre :\n{title}"
        )
        translated = ollama_call(prompt, timeout=30, caller="fetch", operation="translate")
        if translated and len(translated) > 3 and not translated.startswith("["):
            cleaned = translated.strip().strip('"').strip("'")
            # Filet de securite multi-langues : detecte si la traduction
            # a ete faite dans une autre langue que la cible.
            if self._is_wrong_language(cleaned, lang):
                self.log.warning(
                    "Title translation language mismatch; original retained"
                )
                return title
            return cleaned
        return title  # fallback sur l'original si echec

    # ── Nettoyage du JSON du jour ─────────────────────────────────────────────

    def cleanup(self, dry_run: bool = False) -> int:
        with collection_lock(self.seen_file.with_name('article-integrity.lock')):
            return self._cleanup(dry_run)

    def _cleanup(self, dry_run: bool = False) -> int:
        """
        Supprime du JSON du jour toutes les entrées inutilisables :
          - summary vide ou None
          - summary commençant par [ (Timeout, Contenu inaccessible, etc.)
          - no_content=True (articles stockés avant ce patch)

        dry_run=True : affiche ce qui serait supprimé sans écrire.
        Retourne le nombre d'articles supprimés.

        Une sauvegarde précède la suppression explicite. Les reprises automatiques
        sont indépendantes de cette commande de nettoyage manuel.
        """
        articles = self._load_today()
        if not articles:
            self.log.info("[CLEANUP] Aucun article aujourd'hui.")
            return 0

        def _is_bad(a: dict) -> bool:
            s = a.get("summary") or ""
            return (
                not s.strip()
                or s.startswith("[")
                or a.get("no_content") is True
            )

        bad  = [a for a in articles if _is_bad(a)]
        good = [a for a in articles if not _is_bad(a)]

        if not bad:
            self.log.info(
                f"[CLEANUP] Rien à nettoyer — {len(articles)} articles tous valides."
            )
            return 0

        self.log.info(
            f"[CLEANUP] {len(bad)} articles à supprimer sur {len(articles)} :"
        )
        if not dry_run:
            backup(self._today_file())
            self._save_today(good)
            self.log.info(
                f"[CLEANUP] ✅ {len(bad)} supprimés → {len(good)} articles conservés."
            )
        else:
            self.log.info("[CLEANUP] (dry-run — rien écrit)")

        return len(bad)

    # ── Journal de reprise et traitement ──────────────────────────────────────

    def _load_state(self):
        state = read_json(self.state_file, dict)
        if not self.state_file.exists():
            return {'version': 1, 'records': {}}
        if state.get('version') != 1 or not isinstance(state.get('records'), dict) or len(state['records']) > 10000:
            backup(self.state_file)
            raise StorageError('invalid_processing_store')
        allowed = {'discovered', 'processing', 'retryable', 'succeeded', 'failed', 'ignored'}
        for h, rec in state['records'].items():
            if (not isinstance(rec, dict) or not isinstance(rec.get('status'), str)
                    or rec['status'] not in allowed
                    or type(rec.get('attempts')) is not int or not 0 <= rec['attempts'] <= 20
                    or not isinstance(rec.get('item'), dict)
                    or not all(isinstance(rec['item'].get(k, ''), str) for k in ('link', 'title', 'pub_date'))
                    or not rec['item'].get('link') or not rec['item'].get('title')
                    or self._hash(rec['item']['link']) != h
                    or not isinstance(rec.get('category'), str)
                    or not isinstance(rec.get('updated_at'), (int, float))
                    or not isinstance(rec.get('next_attempt_at'), (int, float))
                    or not isinstance(rec.get('discovered_at', rec.get('updated_at')), (int, float))
                    or not all(math.isfinite(rec.get(k, rec.get('updated_at', 0)))
                               for k in ('updated_at', 'next_attempt_at', 'discovered_at'))):
                backup(self.state_file)
                raise StorageError('invalid_processing_record')
            if 'prepared' in rec:
                if (not valid_article(rec['prepared']) or rec['prepared'].get('hash') != h
                        or self._hash(rec['prepared']['link']) != h):
                    backup(self.state_file)
                    raise StorageError('invalid_prepared_article')
                try:
                    if not isinstance(rec.get('target_day'), str):
                        raise ValueError('invalid_day_type')
                    calendar_day(rec['target_day'])
                except (ValueError, TypeError):
                    backup(self.state_file)
                    raise StorageError('invalid_prepared_day') from None
        return state

    def _save_state(self):
        atomic_json(self.state_file, self._state)

    def _record(self, item, category):
        if (not isinstance(category, str) or len(category) > 200
                or any(not isinstance(item.get(k, ''), str) or len(item.get(k, '')) > limit
                       for k, limit in (('link', 4096), ('title', 4096), ('pub_date', 1024)))
                or not item.get('title', '').strip() or not item.get('link', '').strip()):
            self.log.warning('Invalid or oversized article metadata; discovery rejected')
            return None
        h = self._hash(item['link'])
        records = self._state['records']
        if h not in records:
            if len(records) >= 10000:
                self.log.warning('Article tracking capacity reached; discovery deferred')
                return None
            records[h] = {'status': 'discovered', 'attempts': 0,
                          'item': {k: item.get(k, '') for k in ('title', 'link', 'pub_date')},
                          'category': category, 'discovered_at': self._now().timestamp(),
                          'updated_at': self._now().timestamp(),
                          'next_attempt_at': 0}
            self._save_state()
        return records[h]

    def _failure(self, rec, exc):
        status = getattr(exc, 'status_code', None)
        permanent = isinstance(exc, source_http.BlockedURL) or (
            status is not None and 400 <= status < 500 and status not in (408, 425, 429))
        rec['status'] = 'ignored' if isinstance(exc, source_http.BlockedURL) else (
            'failed' if permanent or rec['attempts'] >= self.max_attempts else 'retryable')
        delay = min(self.retry_cap, self.retry_base * 2 ** (rec['attempts'] - 1))
        retry_after = getattr(exc, 'retry_after', None)
        if retry_after and status in (429, 503):
            try:
                delay = max(delay, int(retry_after) if retry_after.strip().isdigit() else
                            parsedate_to_datetime(retry_after).timestamp() - self._now().timestamp())
                if delay > 604800:
                    rec['status'] = 'failed'
            except (ValueError, TypeError, OverflowError):
                pass
        reason = ('http_' + str(status)) if status else (
            str(exc) if isinstance(exc, ArticleAttemptError) else type(exc).__name__)
        rec.update(reason=reason,
                   updated_at=self._now().timestamp(), next_attempt_at=self._now().timestamp() + delay)
        self._save_state()
        self.log.warning('Article attempt %d/%d: %s (%s)', rec['attempts'],
                         self.max_attempts, rec['status'], rec['reason'])

    def _commit_prepared(self, h, rec, seen, existing_hashes, articles):
        target = self.data_dir / (rec['target_day'] + '_articles.json')
        stored = articles if target == self._today_file() else read_json(target, list)
        already = any(valid_article(a) and self._hash(a['link']) == h for a in stored)
        if not already:
            updated = list(stored)
            match = next((i for i, a in enumerate(updated) if isinstance(a.get('link'), str) and self._hash(a['link']) == h), None)
            if match is None:
                updated.append(rec['prepared'])
            else:
                updated[match] = {**updated[match], **rec['prepared']}
                updated[match].pop('no_content', None)
                backup(target)
            if target == self._today_file():
                self._save_today(updated)
                articles[:] = updated
            else:
                backup(target)
                atomic_json(target, updated)
        self._committed_count = getattr(self, '_committed_count', 0) + 1
        rec.update(status='succeeded', updated_at=self._now().timestamp(), next_attempt_at=0)
        rec.pop('prepared', None)
        self._save_state()
        seen.add(h)
        existing_hashes.add(h)
        time.sleep(0.3)  # Preserve the existing pacing after a publication.
        return True  # Reconciled publications also request a public site rebuild.

    def _process_item(self, item, category, seen, existing_hashes, articles):
        h = self._hash(item['link'])
        if not hasattr(self, '_state'):
            self._state = self._load_state()
        rec = self._record(item, category)
        if rec is None or rec['status'] in ('succeeded', 'failed', 'ignored'):
            return False
        if 'prepared' in rec:
            return self._commit_prepared(h, rec, seen, existing_hashes, articles)
        if h in existing_hashes:
            return False
        if rec['next_attempt_at'] > self._now().timestamp():
            return False
        if rec['attempts'] >= self.max_attempts:
            rec.update(status='failed', reason='attempt_limit')
            self._save_state()
            return False
        rec.update(status='processing', attempts=rec['attempts'] + 1,
                   updated_at=self._now().timestamp(),
                   next_attempt_at=self._now().timestamp() + min(
                       self.retry_cap, self.retry_base * 2 ** rec['attempts']))
        self._save_state()
        try:
            content = self._fetch_content(item['link'], raise_errors=True)
            if not content.strip():
                raise ArticleAttemptError('empty_extraction')
            from modules.utils.helpers import clean_for_tts
            summary = clean_for_tts(self._summarize(item['title'], content, category) or '')
            if not summary.strip() or summary.lstrip().startswith('[') or len(summary) > 16000:
                raise ArticleAttemptError('invalid_summary')
            try:
                title = self._translate_title(item['title'], force=False) or item['title']
            except Exception:
                title = item['title']
            article = {'hash': h, 'timestamp': self._now().isoformat(), 'category': category,
                       'title': title, 'title_original': item['title'] if title != item['title'] else '',
                       'link': item['link'], 'source': urlparse(item['link']).netloc.replace('www.', ''),
                       'pub_date': item.get('pub_date', ''), 'summary': summary}
            if not valid_article(article):
                raise ArticleAttemptError('invalid_article')
        except Exception as exc:
            self._failure(rec, exc)
            return False
        rec.update(prepared=article, target_day=self._today_file().name[:8])
        self._save_state()
        return self._commit_prepared(h, rec, seen, existing_hashes, articles)

    def run(self):
        acquired = False
        try:
            with collection_lock(self.seen_file.with_name('article-integrity.lock')):
                acquired = True
                self._committed_count = 0
                self._active_day = self._now().strftime('%Y%m%d')
                try:
                    return self._run_cycle()
                finally:
                    self._active_day = None
        except BlockingIOError:
            self.log.warning('Collection already active; cycle skipped')
            return 0
        except (StorageError, OSError):
            self.log.error('Collection paused: article storage unavailable; originals preserved')
            return self._committed_count if acquired else 0

    def _run_cycle(self):
        seen = self._load_seen()
        articles = self._load_today()
        self._state = self._load_state()
        now = self._now()
        existing_hashes = set()
        for age in range(7):
            day = (now.date() - timedelta(days=age)).strftime('%Y%m%d')
            for article in read_json(self.data_dir / (day + '_articles.json'), list):
                if valid_article(article):
                    existing_hashes.add(self._hash(article['link']))
        for h, rec in list(self._state['records'].items()):
            age = now.timestamp() - rec['updated_at']
            if rec['status'] == 'succeeded' and age >= 7 * 86400:
                del self._state['records'][h]
            elif (rec['status'] in ('discovered', 'retryable', 'processing')
                  and now.timestamp() - rec.get('discovered_at', rec['updated_at']) > 30 * 86400
                  and 'prepared' not in rec):
                rec.update(status='failed', reason='retention_expired')
        self._save_state()
        for category, feeds in _load_rss_sources().items():
            for feed_url in feeds:
                for item in self._fetch_rss(feed_url):
                    if self._hash(item['link']) not in existing_hashes:
                        self._record(item, category)
        if self.retry_summaries:
            for item in articles:
                if not valid_article(item) and item.get('link') and item.get('title'):
                    self._record(item, item.get('category', 'general'))
        queues = {}
        for h, rec in self._state['records'].items():
            if h in existing_hashes and 'prepared' not in rec:
                if rec['status'] not in ('succeeded', 'failed', 'ignored'):
                    rec.update(status='succeeded', updated_at=now.timestamp())
                seen.add(h)
                continue
            if rec['status'] in ('discovered', 'processing', 'retryable') and (
                    'prepared' in rec or rec['next_attempt_at'] <= now.timestamp()):
                queue = queues.setdefault(rec['category'], [])
                if not self.max_per_category or len(queue) < self.max_per_category:
                    queue.append((rec['item'], rec['category']))
        self._save_state()
        count = 0
        while any(queues.values()):
            for queue in queues.values():
                if queue:
                    item, category = queue.pop(0)
                    count += bool(self._process_item(item, category, seen, existing_hashes, articles))
        self._save_seen(seen)
        self.log.info('Article cycle completed: %d publications or reconciliations', count)
        return count

    @staticmethod
    def _cap_queues(queues: dict, cap: int) -> dict:
        """
        Réduit chaque queue proportionnellement pour atteindre `cap` au total,
        en préservant au minimum 1 article par catégorie non vide.
        """
        non_empty = [c for c, v in queues.items() if v]
        if not non_empty or cap <= 0:
            return {}
        # 1 article garanti par catégorie, le reste au prorata
        per_cat_min = 1
        leftover = cap - per_cat_min * len(non_empty)
        if leftover < 0:
            # Cap trop petit pour garantir 1 par cat : on prend round-robin jusqu'à épuisement
            return {c: queues[c] for c in non_empty[:cap]}
        sizes = {c: len(queues[c]) for c in non_empty}
        total = sum(sizes.values())
        if total <= 0:
            return {c: queues[c] for c in non_empty}
        out = {}
        for c in non_empty:
            share = per_cat_min + int(round(leftover * sizes[c] / total))
            out[c] = queues[c][:share]
        # Ajustement final : si on a dépassé ou sous-estimé, on rectifie sur les + gros
        diff = cap - sum(len(v) for v in out.values())
        if diff != 0:
            order = sorted(non_empty, key=lambda c: -sizes[c])
            i = 0
            while diff != 0 and i < 1000:
                c = order[i % len(order)]
                if diff > 0 and len(out[c]) < sizes[c]:
                    out[c] = queues[c][:len(out[c]) + 1]
                    diff -= 1
                elif diff < 0 and len(out[c]) > per_cat_min:
                    out[c] = out[c][:-1]
                    diff += 1
                i += 1
        return out
