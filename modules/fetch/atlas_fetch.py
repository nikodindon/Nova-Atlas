#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/fetch/atlas_fetch.py — Nova-Atlas
Collecte RSS, fetch contenu articles, résumé court via Ollama.
Stockage incrémental dans data/articles/YYYYMMDD_articles.json

Refactorisé depuis atlas_fetch.py (pblart/nova-media) :
  - Enveloppé dans la classe ArticleFetcher(config)
  - Toute la logique interne (RSS_SOURCES, round-robin, verrou seen_hashes,
    retry_pending_summaries, etc.) est conservée à l'identique
  - Les chemins viennent de config.paths.*
  - Ollama passe par modules.core.llm_client
"""

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from modules.core.llm_client import init_ollama, ollama_call, get_language, get_fetch_timeout

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

    def __init__(self, config: dict):
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

        # retry_summaries : False par défaut — désactivé pour garder un flux continu
        fetch_cfg = config.get("fetch", {})
        self.retry_summaries = fetch_cfg.get("retry_summaries", False)

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

    def _load_seen(self) -> set:
        if not self.seen_file.exists():
            return set()
        with open(self.seen_file, encoding="utf-8") as f:
            data = json.load(f)
        today_int = int(datetime.now().strftime("%Y%m%d"))
        return {h for h, d in data.items() if today_int - int(d) <= 7}

    def _save_seen(self, seen: set):
        today = datetime.now().strftime("%Y%m%d")
        existing = {}
        if self.seen_file.exists():
            with open(self.seen_file, encoding="utf-8") as f:
                existing = json.load(f)
        for h in seen:
            if h not in existing:
                existing[h] = today
        with open(self.seen_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False)

    @staticmethod
    def _hash(url: str) -> str:
        return hashlib.md5(url.strip().encode()).hexdigest()

    # ── Fichier articles du jour ───────────────────────────────────────────────

    def _today_file(self) -> Path:
        return self.data_dir / f"{datetime.now().strftime('%Y%m%d')}_articles.json"

    def _load_today(self) -> list:
        f = self._today_file()
        if not f.exists():
            return []
        try:
            with open(f, encoding="utf-8") as fp:
                return json.load(fp)
        except json.JSONDecodeError:
            import shutil
            shutil.copy(f, str(f) + ".corrupted")
            self.log.warning(f"JSON corrompu — sauvegardé : {f}.corrupted")
            return []

    def _save_today(self, articles: list):
        target = self._today_file()
        tmp    = Path(str(target) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(articles, fp, ensure_ascii=False, indent=2)
        for attempt in range(5):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                time.sleep(0.3 * (attempt + 1))
        import shutil
        shutil.copy2(tmp, target)
        tmp.unlink(missing_ok=True)

    # ── RSS fetch ─────────────────────────────────────────────────────────────

    def _fetch_rss(self, url: str) -> list:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; AtlasNews/2.0)"}
        try:
            r = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT_HTTP)
            r.raise_for_status()
            soup  = BeautifulSoup(r.content, "xml")
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
            self.log.debug(f"RSS fail [{url[:60]}]: {e}")
            return []

    # ── Article content ───────────────────────────────────────────────────────

    @staticmethod
    def _fetch_content(url: str) -> str:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        try:
            r = requests.get(url, headers=headers, timeout=FETCH_TIMEOUT_HTTP)
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
            self.log.warning(f"Ollama vide/timeout : {title[:60]}")
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
        translated = ollama_call(prompt, timeout=30, caller="fetch")
        if translated and len(translated) > 3 and not translated.startswith("["):
            cleaned = translated.strip().strip('"').strip("'")
            # Filet de securite multi-langues : detecte si la traduction
            # a ete faite dans une autre langue que la cible.
            if self._is_wrong_language(cleaned, lang):
                self.log.warning(
                    f"Traduction vers la mauvaise langue (cible={lang}), "
                    f"fallback sur l'original : {title[:60]}"
                )
                return title
            return cleaned
        return title  # fallback sur l'original si echec

    def _retry_pending(self, articles: list) -> tuple:
        """
        Re-résume les articles sans résumé (Ollama était occupé au cycle précédent).
        Désactivé par défaut (fetch.retry_summaries: false dans config.yaml)
        pour garantir un flux continu sans bloquer sur les timeouts.
        """
        if not self.retry_summaries:
            pending_count = sum(
                1 for a in articles
                if not a.get("summary") or a["summary"].startswith("[Timeout]")
            )
            if pending_count:
                self.log.debug(
                    f"[RETRY] {pending_count} articles sans résumé ignorés "
                    f"(retry_summaries désactivé)"
                )
            return articles, 0

        pending = [a for a in articles
                   if not a.get("summary") or a["summary"].startswith("[Timeout]")]
        if not pending:
            return articles, 0
        self.log.info(f"Re-résumé de {len(pending)} articles en attente...")
        count = 0
        for a in pending:
            content = self._fetch_content(a.get("link", ""))
            summary = self._summarize(a["title"], content, a.get("category", "monde"))
            if summary and not summary.startswith("["):
                a["summary"] = summary
                count += 1
        if count:
            self.log.info(f"Re-résumé : {count}/{len(pending)} récupérés")
        return articles, count

    # ── Traitement d'un article ───────────────────────────────────────────────

    def _process_item(self, item: dict, category: str,
                      seen: set, existing_hashes: set,
                      articles: list) -> bool:
        h = self._hash(item["link"])
        if h in seen or h in existing_hashes:
            return False

        title   = item["title"].strip()
        content = self._fetch_content(item["link"])
        try:
            domain = urlparse(item["link"]).netloc.replace("www.", "")
        except Exception:
            domain = "source inconnue"

        if not content.strip():
            if len(title) < MIN_TITLE_LEN:
                self.log.debug(f"  Skip (titre court + pas de contenu): {title[:50]}")
                seen.add(h)
                existing_hashes.add(h)
                return False
            # Contenu inaccessible (paywall, anti-scraping, lien mort…) → on ignore
            # Le titre seul sans résumé ne sert ni aux éditions, ni à la radio,
            # ni au fil live. Marquer comme vu pour ne pas retenter.
            self.log.debug(
                f"  [{category[:4].upper()}] Ignoré (contenu inaccessible): {title[:65]}"
            )
            seen.add(h)
            existing_hashes.add(h)
            return False
        else:
            self.log.info(f"  [{category[:4].upper()}] {title[:65]}…")
            summary = self._summarize(title, content, category)
            # Nettoie le résumé des artefacts markdown (filet de sécurité
            # au cas où le LLM en mettrait encore malgré le prompt).
            # Sans ça, le site affiche "**bold**" et la radio dit "astérisque".
            from modules.utils.helpers import clean_for_tts
            summary = clean_for_tts(summary)
            # Traduction du titre dans la langue cible.
            # On ne traduit QUE si le titre contient des caracteres non-latins
            # (= pas lisible par un francophone). Force=True ignore l'heuristique.
            translated_title = self._translate_title(title, force=False)
            article = {
                "hash":          h,
                "timestamp":     datetime.now().isoformat(),
                "category":      category,
                "title":         translated_title,
                "title_original": title if translated_title != title else "",
                "link":          item["link"],
                "source":        domain,
                "pub_date":      item.get("pub_date", ""),
                "summary":       summary,
            }

        articles.append(article)
        existing_hashes.add(h)
        seen.add(h)
        self._save_today(articles)
        time.sleep(0.3 if content.strip() else 0.1)
        return True

    # ── Nettoyage du JSON du jour ─────────────────────────────────────────────

    def cleanup(self, dry_run: bool = False) -> int:
        """
        Supprime du JSON du jour toutes les entrées inutilisables :
          - summary vide ou None
          - summary commençant par [ (Timeout, Contenu inaccessible, etc.)
          - no_content=True (articles stockés avant ce patch)

        dry_run=True : affiche ce qui serait supprimé sans écrire.
        Retourne le nombre d'articles supprimés.

        Note : les hashes restent dans seen_hashes — on ne retente pas
        les articles sans contenu, ils resteront inaccessibles.
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
        for a in bad[:10]:
            s = (a.get("summary") or "")[:50] or "(vide)"
            self.log.info(
                f"  - [{a.get('category','?')}] "
                f"{a.get('title','')[:55]}  →  {s}"
            )
        if len(bad) > 10:
            self.log.info(f"  ... et {len(bad) - 10} autres")

        if not dry_run:
            self._save_today(good)
            self.log.info(
                f"[CLEANUP] ✅ {len(bad)} supprimés → {len(good)} articles conservés."
            )
        else:
            self.log.info("[CLEANUP] (dry-run — rien écrit)")

        return len(bad)

    # ── Point d'entrée principal ──────────────────────────────────────────────

    def run(self) -> int:
        """
        Cycle de collecte en round-robin par catégorie.
        Retourne le nombre de nouveaux articles ajoutés.
        Équivalent direct de run_fetch_cycle() dans atlas_fetch.py.
        """
        self.log.info("═" * 50)
        self.log.info("Début cycle de collecte (round-robin)")
        self.log.info("═" * 50)

        seen            = self._load_seen()
        articles        = self._load_today()
        existing_hashes = {a["hash"] for a in articles}
        new_count       = 0

        # Phase 0 — Retry résumés en attente
        articles, retried = self._retry_pending(articles)
        if retried:
            self._save_today(articles)
            self.log.info(f"Phase 0 : {retried} résumés récupérés")

        # Phase 1 — Collecte RSS
        # On recharge les flux depuis feeds.yaml (cached via mtime). Si le
        # fichier a été modifié via /config/feeds ou /config/restart, la
        # cache est invalidée (voir reload_config()).
        rss_sources = _load_rss_sources()
        self.log.info("Collecte des flux RSS...")
        queues: dict = {}
        for category, feeds in rss_sources.items():
            pending = []
            for feed_url in feeds:
                items = self._fetch_rss(feed_url)
                for item in items:
                    h = self._hash(item["link"])
                    if h not in seen and h not in existing_hashes:
                        pending.append((item, category))
            if pending:
                queues[category] = pending
                self.log.info(f"  {category:15} : {len(pending)} nouveaux")

        # Log explicite des catégories sans nouvel article (pour qu'on sache
        # qu'elles existent et qu'elles sont juste calmes dans la fenêtre).
        all_cats = list(rss_sources.keys())
        empty_cats = [c for c in all_cats if c not in queues]
        if empty_cats:
            self.log.info(f"  (calmes: {', '.join(empty_cats)})")

        total_pending = sum(len(v) for v in queues.values())

        # Cap par catégorie (rss.max_per_category). On garantit le même
        # quota à chaque catégorie pour préserver la diversité, plutôt
        # qu'un cap global qui favorise les premières catégories remplies.
        max_per_cat = self.max_per_category
        if max_per_cat:
            for cat in queues:
                if len(queues[cat]) > max_per_cat:
                    queues[cat] = queues[cat][:max_per_cat]
            total_pending = sum(len(v) for v in queues.values())
            self.log.info(f"Cap par catégorie : {max_per_cat} articles/cat (total: {total_pending})")

        self.log.info(f"Total à traiter : {total_pending} articles")

        if not total_pending:
            self.log.info("Aucun nouvel article — cycle terminé")
            self._save_seen(seen)
            return 0

        # Phase 2 — Round-robin
        cat_keys = list(queues.keys())
        while any(queues.values()):
            for cat in cat_keys:
                if not queues.get(cat):
                    continue
                item, category = queues[cat].pop(0)
                added = self._process_item(item, category, seen, existing_hashes, articles)
                if added:
                    new_count += 1

        self._save_seen(seen)
        self.log.info(
            f"Cycle terminé : {new_count} nouveaux articles "
            f"({len(articles)} total aujourd'hui)"
        )
        return new_count

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
