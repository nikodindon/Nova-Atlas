#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
modules/web/atlas_web.py — Nova-Atlas
Génère le site web statique + serveur Flask local.

Différences vs version standalone pblart/nova-media :
  - Les chemins (articles, reports, editions, site) sont lus depuis le dict config
    issu de config/config.yaml, via les deux fonctions publiques :
      run_server(config, host, port)
      generate_static_site(config, full)
  - L'objet Flask est instancié par create_app(), appelée par run_server(), pour que les
    chemins soient correctement résolus au moment de l'appel, pas au chargement
    du module.
  - load_atlas_config() / save_atlas_config() utilisent paths.data_dir
"""

import json
import os
import re
from markupsafe import Markup, escape
from modules.web.web_security import safe_url, render_markdown
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

# ─── CONSTANTES ÉDITORIALES ───────────────────────────────────────────────────

from modules.editorial.categories import CATEGORY_LABELS, CATEGORY_ICONS, registry

EDITION_CONFIG = {
    "matin": {"label": "Édition du Matin", "emoji": "🌅", "color": "#f59e0b"},
    "midi":  {"label": "Édition de Midi",  "emoji": "☀️", "color": "#3b82f6"},
    "soir":  {"label": "Édition du Soir",  "emoji": "🌙", "color": "#8b5cf6"},
}

AVAILABLE_MODELS = [
    {"id": "qwen3:8b",             "label": "qwen3:8b",             "desc": "Défaut Nova-Atlas"},
    {"id": "mistral-small:22b",    "label": "mistral-small:22b",    "desc": "Meilleure qualité (12Go RAM)"},
    {"id": "mistral:7b",           "label": "mistral:7b",           "desc": "Bon équilibre (4Go VRAM)"},
    {"id": "mistral:7b-instruct",  "label": "mistral:7b-instruct",  "desc": "Suit mieux les consignes"},
    {"id": "llama3.1:8b",          "label": "llama3.1:8b",          "desc": "Meta LLaMA — rapide"},
    {"id": "qwen2.5:7b",           "label": "qwen2.5:7b",           "desc": "Bon pour les faits"},
    {"id": "gemma2:9b",            "label": "gemma2:9b",            "desc": "Google Gemma"},
    {"id": "phi4:14b",             "label": "phi4:14b",             "desc": "Microsoft Phi4 — excellent"},
    {"id": "deepseek-r1:14b",      "label": "deepseek-r1:14b",      "desc": "DeepSeek — raisonnement"},
]

DEFAULT_ATLAS_CONFIG = {
    "ollama_model":      "qwen3:8b",
    "fetch_timeout":     240,
    "edition_timeout":   900,
    "max_articles_feed": 8,
    "live_window_hours": 20,
    "post_hours":        [7, 9, 11, 13, 15, 17, 19, 21],
    # Icecast pour le player web — synchronisé depuis config.yaml au démarrage
    "icecast_host":      "localhost",
    "icecast_port":      8000,
    "icecast_mount":     "/nova",
}

# ─── TRADUCTIONS UI (i18n) ────────────────────────────────────────────────────

_UI_STRINGS = {
    "fr": {
        "nav_home": "Accueil", "nav_live": "Direct", "nav_chronicle": "Chronique",
        "nav_archives": "Archives", "nav_config": "⚙",
        "live_title": "Fil en direct", "live_status": "EN DIRECT",
        "live_updated": "Mis à jour à",
        "breaking": "BREAKING",
        "archives_title": "Chronique du monde",
        "filter_all": "Tout",
        "read_more": "Lire", "reduce": "Réduire",
        "radio_live": "EN DIRECT",
    },
    "en": {
        "nav_home": "Home", "nav_live": "Live", "nav_chronicle": "Chronicle",
        "nav_archives": "Archives", "nav_config": "⚙",
        "live_title": "Live Feed", "live_status": "LIVE",
        "live_updated": "Updated at",
        "breaking": "BREAKING",
        "archives_title": "World Chronicle",
        "filter_all": "All",
        "read_more": "Read", "reduce": "Collapse",
        "radio_live": "ON AIR",
    },
    "de": {
        "nav_home": "Startseite", "nav_live": "Live", "nav_chronicle": "Chronik",
        "nav_archives": "Archiv", "nav_config": "⚙",
        "live_title": "Live-Feed", "live_status": "LIVE",
        "live_updated": "Aktualisiert um",
        "breaking": "EILMELDUNG",
        "archives_title": "Weltchronik",
        "filter_all": "Alle",
        "read_more": "Lesen", "reduce": "Einklappen",
        "radio_live": "ON AIR",
    },
    "es": {
        "nav_home": "Inicio", "nav_live": "Directo", "nav_chronicle": "Crónica",
        "nav_archives": "Archivos", "nav_config": "⚙",
        "live_title": "Noticias en vivo", "live_status": "EN VIVO",
        "live_updated": "Actualizado a las",
        "breaking": "ÚLTIMA HORA",
        "archives_title": "Crónica mundial",
        "filter_all": "Todo",
        "read_more": "Leer", "reduce": "Reducir",
        "radio_live": "EN DIRECTO",
    },
    "pt": {
        "nav_home": "Início", "nav_live": "Ao vivo", "nav_chronicle": "Crônica",
        "nav_archives": "Arquivos", "nav_config": "⚙",
        "live_title": "Feed ao vivo", "live_status": "AO VIVO",
        "live_updated": "Atualizado às",
        "breaking": "ÚLTIMA HORA",
        "archives_title": "Crônica mundial",
        "filter_all": "Tudo",
        "read_more": "Ler", "reduce": "Recolher",
        "radio_live": "AO VIVO",
    },
    "it": {
        "nav_home": "Home", "nav_live": "Diretta", "nav_chronicle": "Cronaca",
        "nav_archives": "Archivi", "nav_config": "⚙",
        "live_title": "Feed in diretta", "live_status": "IN DIRETTA",
        "live_updated": "Aggiornato alle",
        "breaking": "FLASH",
        "archives_title": "Cronaca mondiale",
        "filter_all": "Tutto",
        "read_more": "Leggi", "reduce": "Chiudi",
        "radio_live": "IN ONDA",
    },
    "nl": {
        "nav_home": "Startpagina", "nav_live": "Live", "nav_chronicle": "Kroniek",
        "nav_archives": "Archief", "nav_config": "⚙",
        "live_title": "Live nieuws", "live_status": "LIVE",
        "live_updated": "Bijgewerkt om",
        "breaking": "BREAKING",
        "archives_title": "Wereldkroniek",
        "filter_all": "Alles",
        "read_more": "Lezen", "reduce": "Inklappen",
        "radio_live": "LIVE",
    },
    "ru": {
        "nav_home": "Главная", "nav_live": "Прямой эфир", "nav_chronicle": "Хроника",
        "nav_archives": "Архивы", "nav_config": "⚙",
        "live_title": "Новостная лента", "live_status": "ПРЯМОЙ ЭФИР",
        "live_updated": "Обновлено в",
        "breaking": "СРОЧНО",
        "archives_title": "Мировая хроника",
        "filter_all": "Все",
        "read_more": "Читать", "reduce": "Свернуть",
        "radio_live": "В ЭФИРЕ",
    },
    "ar": {
        "nav_home": "الرئيسية", "nav_live": "مباشر", "nav_chronicle": "سجل",
        "nav_archives": "أرشيف", "nav_config": "⚙",
        "live_title": "البث المباشر", "live_status": "مباشر",
        "live_updated": "تحديث في",
        "breaking": "عاجل",
        "archives_title": "سجل العالم",
        "filter_all": "الكل",
        "read_more": "اقرأ", "reduce": "طي",
        "radio_live": "على الهواء",
    },
    "ja": {
        "nav_home": "ホーム", "nav_live": "ライブ", "nav_chronicle": "記録",
        "nav_archives": "アーカイブ", "nav_config": "⚙",
        "live_title": "ライブフィード", "live_status": "ライブ",
        "live_updated": "更新:",
        "breaking": "速報",
        "archives_title": "世界の記録",
        "filter_all": "すべて",
        "read_more": "読む", "reduce": "閉じる",
        "radio_live": "放送中",
    },
    "zh": {
        "nav_home": "首页", "nav_live": "直播", "nav_chronicle": "编年史",
        "nav_archives": "档案", "nav_config": "⚙",
        "live_title": "实时资讯", "live_status": "直播",
        "live_updated": "更新于",
        "breaking": "突发",
        "archives_title": "世界编年史",
        "filter_all": "全部",
        "read_more": "阅读", "reduce": "收起",
        "radio_live": "播出中",
    },
}

_CAT_LABELS_I18N = {
    "fr": {
        "geopolitique": "Géopolitique", "economie": "Économie", "crypto": "Crypto",
        "tech": "Tech & IA", "france": "France", "monde": "Monde",
        "science": "Science & Santé", "environnement": "Environnement",
        "societe": "Société & Droits", "culture": "Culture & Arts", "sport": "Sport",
        # Catégories ajoutées au sprint "plus de sources" (2026-07-06)
        "sante":             "Santé",
        "gaming":            "Gaming",
        "sciences_humaines": "Sciences Humaines",
        "auto":              "Auto",
        "regions":           "Régions",  # anciennement local_france, renommé 2026-07-07
        "asie":              "Asie",     # ajoutée 2026-07-27
        "monde_arabe":       "Monde Arabe",  # ajoutée 2026-07-27
        "eurasie":           "Eurasie",       # ajoutée 2026-07-27
        "europe":            "Europe",         # ajoutée 2026-07-28 (GN pays europeens)
        "afrique":           "Afrique",        # ajoutée 2026-07-28 (GN Nigeria, ZA)
        "amerique_latine":   "Amérique Latine",  # ajoutée 2026-07-28 (GN Brésil, Mexique)
    },
    "en": {
        "geopolitique": "Geopolitics", "economie": "Economy", "crypto": "Crypto",
        "tech": "Tech & AI", "france": "France", "monde": "World",
        "science": "Science & Health", "environnement": "Environment",
        "societe": "Society & Rights", "culture": "Culture & Arts", "sport": "Sport",
        "sante":             "Health",
        "gaming":            "Gaming",
        "sciences_humaines": "Human Sciences",
        "auto":              "Auto",
        "regions":           "Regions",
        "asie":              "Asia",
        "monde_arabe":       "Arab World",
        "eurasie":           "Eurasia",
        "europe":            "Europe",
        "afrique":           "Africa",
        "amerique_latine":   "Latin America",
    },
    "de": {
        "geopolitique": "Geopolitik", "economie": "Wirtschaft", "crypto": "Krypto",
        "tech": "Tech & KI", "france": "Frankreich", "monde": "Welt",
        "science": "Wissenschaft & Gesundheit", "environnement": "Umwelt",
        "societe": "Gesellschaft", "culture": "Kultur & Kunst", "sport": "Sport",
        "sante":             "Gesundheit",
        "gaming":            "Gaming",
        "sciences_humaines": "Geisteswissenschaften",
        "auto":              "Auto",
        "regions":           "Regionen",
        "asie":              "Asien",
        "monde_arabe":       "Arabische Welt",
        "eurasie":           "Eurasien",
        "europe":            "Europa",
        "afrique":           "Afrika",
        "amerique_latine":   "Lateinamerika",
    },
    "es": {
        "geopolitique": "Geopolítica", "economie": "Economía", "crypto": "Cripto",
        "tech": "Tech & IA", "france": "Francia", "monde": "Mundo",
        "science": "Ciencia & Salud", "environnement": "Medio ambiente",
        "societe": "Sociedad & Derechos", "culture": "Cultura & Arte", "sport": "Deporte",
        "sante":             "Salud",
        "gaming":            "Gaming",
        "sciences_humaines": "Ciencias Humanas",
        "auto":              "Auto",
        "regions":           "Regiones",
        "asie":              "Asia",
        "monde_arabe":       "Mundo Árabe",
        "eurasie":           "Eurasia",
        "europe":            "Europa",
        "afrique":           "África",
        "amerique_latine":   "América Latina",
    },
    "pt": {
        "geopolitique": "Geopolítica", "economie": "Economia", "crypto": "Cripto",
        "tech": "Tech & IA", "france": "França", "monde": "Mundo",
        "science": "Ciência & Saúde", "environnement": "Meio ambiente",
        "societe": "Sociedade & Direitos", "culture": "Cultura & Arte", "sport": "Esporte",
        "sante":             "Saúde",
        "gaming":            "Gaming",
        "sciences_humaines": "Ciências Humanas",
        "auto":              "Auto",
        "regions":           "Regiões",
        "asie":              "Ásia",
        "monde_arabe":       "Mundo Árabe",
        "eurasie":           "Eurásia",
        "europe":            "Europa",
        "afrique":           "África",
        "amerique_latine":   "América Latina",
    },
    "it": {
        "geopolitique": "Geopolitica", "economie": "Economia", "crypto": "Cripto",
        "tech": "Tech & IA", "france": "Francia", "monde": "Mondo",
        "science": "Scienza & Salute", "environnement": "Ambiente",
        "societe": "Società & Diritti", "culture": "Cultura & Arte", "sport": "Sport",
        "sante":             "Salute",
        "gaming":            "Gaming",
        "sciences_humaines": "Scienze Umane",
        "auto":              "Auto",
        "regions":           "Regioni",
        "asie":              "Asia",
        "monde_arabe":       "Mondo Arabo",
        "eurasie":           "Eurasia",
        "europe":            "Europa",
        "afrique":           "Africa",
        "amerique_latine":   "America Latina",
    },
    "ru": {
        "geopolitique": "Геополитика", "economie": "Экономика", "crypto": "Крипто",
        "tech": "Технологии", "france": "Франция", "monde": "Мир",
        "science": "Наука и здоровье", "environnement": "Экология",
        "societe": "Общество", "culture": "Культура", "sport": "Спорт",
        "sante":             "Здоровье",
        "gaming":            "Игры",
        "sciences_humaines": "Гуманитарные науки",
        "auto":              "Авто",
        "regions":           "Регионы",
        "asie":              "Азия",
        "monde_arabe":       "Арабский мир",
        "eurasie":           "Евразия",
        "europe":            "Европа",
        "afrique":           "Африка",
        "amerique_latine":   "Латинская Америка",
    },
    "zh": {
        "geopolitique": "地缘政治", "economie": "经济", "crypto": "加密货币",
        "tech": "科技与AI", "france": "法国", "monde": "世界",
        "science": "科学与健康", "environnement": "环境",
        "societe": "社会", "culture": "文化", "sport": "体育",
        "sante":             "健康",
        "gaming":            "游戏",
        "sciences_humaines": "人文学科",
        "auto":              "汽车",
        "regions":           "地区",
        "asie":              "亚洲",
        "monde_arabe":       "阿拉伯世界",
        "eurasie":           "欧亚大陆",
        "europe":            "欧洲",
        "afrique":           "非洲",
        "amerique_latine":   "拉丁美洲",
    },
    "ar": {
        "geopolitique": "الجيوسياسية", "economie": "الاقتصاد", "crypto": "عملات مشفرة",
        "tech": "التكنولوجيا", "france": "فرنسا", "monde": "العالم",
        "science": "العلوم والصحة", "environnement": "البيئة",
        "societe": "المجتمع", "culture": "الثقافة", "sport": "الرياضة",
        "sante":             "الصحة",
        "gaming":            "ألعاب",
        "sciences_humaines": "علوم إنسانية",
        "auto":              "سيارات",
        "regions":           "المناطق",
        "asie":              "آسيا",
        "monde_arabe":       "العالم العربي",
        "eurasie":           "أوراسيا",
        "europe":            "أوروبا",
        "afrique":           "أفريقيا",
        "amerique_latine":   "أمريكا اللاتينية",
    },
    "ja": {
        "geopolitique": "地政学", "economie": "経済", "crypto": "暗号資産",
        "tech": "テクノロジー", "france": "フランス", "monde": "世界",
        "science": "科学と健康", "environnement": "環境",
        "societe": "社会と権利", "culture": "文化と芸術", "sport": "スポーツ",
        "sante":             "健康",
        "gaming":            "ゲーム",
        "sciences_humaines": "人文科学",
        "auto":              "車",
        "regions":           "地域",
        "asie":              "アジア",
        "monde_arabe":       "アラブ世界",
        "eurasie":           "ユーラシア",
        "europe":            "ヨーロッパ",
        "afrique":           "アフリカ",
        "amerique_latine":   "ラテンアメリカ",
    },
}


def _get_ui(config: dict) -> dict:
    """Retourne le dict de traductions UI pour la langue configurée."""
    lang = config.get("service", {}).get("default_language", "fr")
    return _UI_STRINGS.get(lang, _UI_STRINGS["en"])


def _get_cat_labels(config: dict) -> dict:
    """Retourne les labels de catégories traduits."""
    lang = config.get("service", {}).get("default_language", "fr")
    return _CAT_LABELS_I18N.get(lang, _CAT_LABELS_I18N["fr"])


# ─── RÉSOLUTION DES CHEMINS ───────────────────────────────────────────────────

def _resolve_paths(config: dict, root: Path = None) -> dict:
    """Extrait les chemins absolus + branding + i18n depuis le dict config Nova-Atlas."""
    paths = config.get("paths", {})
    root  = Path(root) if root is not None else Path.cwd()
    svc   = config.get("service", {})
    name  = svc.get("name", "Nova Atlas")
    parts     = name.upper().rsplit(" ", 1)
    logo_main = parts[0]
    logo_sub  = "_" + parts[1] if len(parts) > 1 else ""
    return {
        "root":      root,    # base path (utilisé par BulletinGenerator, etc.)
        "articles": root / paths.get("articles_dir", "data/articles"),
        "reports":  root / paths.get("reports_dir",  "data/reports"),
        "editions": root / paths.get("editions_dir", "data/editions"),
        "site":     root / paths.get("site_dir",     "site"),
        "data":     root / paths.get("data_dir",     "data"),
        # Branding
        "brand_name":    name,
        "brand_tagline": svc.get("tagline", ""),
        "logo_main":     logo_main,
        "logo_sub":      logo_sub,
        # i18n — stocké pour être accessible dans tous les renderers
        "_config":       config,
        "ui":            _get_ui(config),
        "cat_labels":    _get_cat_labels(config),
    }

def _get_icecast_url(paths: dict) -> str:
    """Use the existing same-origin stream proxy on HTTP and HTTPS.

    The browser never needs Icecast credentials, a Host-derived URL or an
    additional media origin in CSP. Internal Icecast policy stays unchanged.
    """
    # Same-origin proxy for HTTP and HTTPS: no Host interpolation or browser credentials.
    from urllib.parse import quote
    mount = str(load_atlas_config(paths).get('icecast_mount', '/nova')).lstrip('/')
    return '/stream/' + quote(mount, safe='/')


def _category_icons(paths):
    return {c.id:c.icon for c in registry(paths.get('feeds_file') or paths['root']/'config/feeds.yaml')}


def _branding(paths: dict) -> dict:
    """Retourne les variables de branding + i18n à injecter dans les templates."""
    ui = paths.get("ui", _UI_STRINGS["fr"])
    return {
        **__import__("modules.media.view",fromlist=["helpers"]).helpers(paths),
        "brand_name":    paths["brand_name"],
        "brand_tagline": paths["brand_tagline"],
        "logo_main":     paths["logo_main"],
        "logo_sub":      paths["logo_sub"],
        "service_name":  paths["brand_name"],
        # UI strings traduits
        "ui":            ui,
        "cat_labels":    {**paths.get("cat_labels", CATEGORY_LABELS),**{c.id:c.name for c in registry(paths.get('feeds_file') or paths['root']/'config/feeds.yaml',labels=paths.get('cat_labels',{}))}},
    }

# ─── CONFIG ATLAS (JSON) ──────────────────────────────────────────────────────

def _config_file(paths: dict) -> Path:
    from modules.web.config_security import confined_file
    return confined_file(paths["data"], "atlas_config.json")

def load_atlas_config(paths: dict) -> dict:
    f = _config_file(paths)
    if f.exists():
        try:
            with open(f, "r", encoding="utf-8") as fp:
                cfg = json.load(fp)
            for k, v in DEFAULT_ATLAS_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception:
            pass
    return DEFAULT_ATLAS_CONFIG.copy()

def save_atlas_config(paths: dict, cfg: dict):
    f = _config_file(paths)
    f.parent.mkdir(parents=True, exist_ok=True)
    from modules.web.config_security import atomic_text
    atomic_text(f, json.dumps(cfg, ensure_ascii=False, indent=2))

# ─── HELPERS DATA ─────────────────────────────────────────────────────────────

def format_date_fr(day: str) -> str:
    months  = ["janvier","février","mars","avril","mai","juin",
                "juillet","août","septembre","octobre","novembre","décembre"]
    days_fr = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    d = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
    return f"{days_fr[d.weekday()]} {d.day} {months[d.month-1]} {d.year}"

def format_day_label(day: str) -> str:
    d = date(int(day[:4]), int(day[4:6]), int(day[6:8]))
    return d.strftime("%d/%m/%Y")

def load_articles_for_day(day: str, paths: dict) -> list:
    from modules.web.config_security import confined_file
    f = confined_file(paths["articles"], f"{day}_articles.json")
    if not f.exists():
        return []
    with open(f, "r", encoding="utf-8") as fp:
        return json.load(fp)

def load_report(day: str, paths: dict) -> str:
    from modules.web.config_security import confined_file
    f = confined_file(paths["reports"], f"{day}_report.md")
    if not f.exists():
        return ""
    with open(f, "r", encoding="utf-8") as fp:
        return fp.read()

def get_available_days(paths: dict) -> list:
    days = set()
    if paths["reports"].exists():
        for f in paths["reports"].iterdir():
            if f.name.endswith("_report.md"):
                days.add(f.name[:8])
    if paths["articles"].exists():
        for f in paths["articles"].iterdir():
            if f.name.endswith("_articles.json"):
                days.add(f.name[:8])
    return sorted(days, reverse=True)

# ─── BREAKING NEWS ────────────────────────────────────────────────────────────

def get_breaking_news(paths: dict, max_items: int = 6) -> list:
    from datetime import timezone
    from modules.editorial.index import EventIndex,IndexError
    from modules.editorial.events import Coverage,group_coverages
    cutoff=datetime.now(timezone.utc)-timedelta(hours=4)
    data=None
    if paths.get('data'):
        try:data=EventIndex(paths['articles'],paths['data']).load()
        except (IndexError,OSError,KeyError,TypeError):return []
    if data is not None:events=data['events']
    else:
        articles=[]
        for day in ((datetime.now()-timedelta(days=1)).strftime('%Y%m%d'),datetime.now().strftime('%Y%m%d')):
            articles.extend(load_articles_for_day(day,paths))
        coverages=[]
        for article in articles[:500]:
            try:
                if article.get('migrated'):continue
                coverage=Coverage.from_article(article)
                if coverage.published_at and datetime.fromisoformat(coverage.published_at)>=cutoff:coverages.append(coverage)
            except (ValueError,TypeError,AttributeError):continue
        coverages=sorted(coverages,key=lambda c:c.published_at or '',reverse=True)[:200]
        events,_=group_coverages(coverages)
        events=[e for e in events if e['article_count']>=2]
    selected=[]
    for event in events:
        published=event.get('latest_published_at')
        if not published:continue
        try:stamp=datetime.fromisoformat(published)
        except ValueError:continue
        if stamp<cutoff or stamp>datetime.now(timezone.utc)+timedelta(hours=1):continue
        representative=max(event['articles'],key=lambda a:len(a['summary']))
        selected.append({'title':event['title'][:80],'link':representative['link'] or '#','count':event['article_count'],
            'sources':', '.join(event['sources'][:3]),'cat':event['category'],'event_id':event['event_id'],
            'source_count':event['domain_count'],'article_count':event['article_count'],'independent_confirmation':False,
            'coverages':[{'title':a['title'],'link':a['link'],'source':a['source']} for a in event['articles']],
            'last_updated_at':event['last_updated_at'],'published_at':published})
    return sorted(selected,key=lambda e:(e['published_at'],e['event_id']),reverse=True)[:max_items]

def _parse_ts(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.min

def render_breaking_banner(paths: dict) -> str:
    items = get_breaking_news(paths)
    if not items:
        return ""
    parts = []
    for item in items:
        emoji = CATEGORY_ICONS.get(item["cat"], "🌐")
        badge = "🔥 " + str(item["count"]) + "  " if item["count"] >= 3 else "🔥 "
        title = escape(item['title'])
        link = escape(safe_url(item['link']))
        parts.append(
            f'<a href="{link}" target="_blank" rel="noopener" class="bb-item">'
            f'{badge}{emoji} {title}'
            f'</a><span class="bb-sep"> &nbsp;·&nbsp; </span>'
        )
    track = "".join(parts)
    # Le label "BREAKING" vient des traductions stockées dans paths
    ui = paths.get("ui", {})
    breaking_label = ui.get("breaking", "BREAKING")
    return Markup(
        '<div class="breaking-banner">'
        f'<span class="bb-label"><span class="live-dot"></span>{escape(breaking_label)}</span>'
        '<div class="bb-track-wrap">'
        f'<div class="bb-track">{track}{track}</div>'
        '</div></div>'
    )

# ─── RENDERERS ────────────────────────────────────────────────────────────────

def render_report_page(day: str, paths: dict) -> str:
    from modules.web.web_security import html_template as Template
    articles  = load_articles_for_day(day, paths)
    report_md = load_report(day, paths)

    if not report_md and not articles:
        return ""
    if not report_md:
        report_md = f"# {format_date_fr(day)}\n\n*Rapport en cours de génération...*"

    report_html = render_markdown(report_md)

    by_cat    = {}
    for a in articles:
        by_cat.setdefault(a.get("category","monde"), []).append(a)
    sources    = set(a.get("source","") for a in articles)
    categories = sorted([(c, len(a)) for c, a in by_cat.items()], key=lambda x: -x[1])

    stats = [
        ("Articles analysés",  str(len(articles))),
        ("Sources distinctes", str(len(sources))),
        ("Catégories",         str(len(by_cat))),
        ("Date",               format_day_label(day)),
    ]

    all_days = get_available_days(paths)
    archives = []
    for d in all_days[:6]:
        if d == day:
            continue
        arts = load_articles_for_day(d, paths)
        archives.append({
            "file":    f"{d}_report.html",
            "date_fr": format_day_label(d),
            "count":   str(len(arts)),
        })
    archives = archives[:5]

    recent           = sorted(articles, key=lambda a: a.get("timestamp",""), reverse=True)[:20]
    all_arts_sorted  = sorted(
        [a for a in articles if a.get("link")],
        key=lambda a: (a.get("category",""), a.get("timestamp",""))
    )
    today_str = datetime.now().strftime("%Y%m%d")

    return Template(REPORT_TEMPLATE).render(
        **_branding(paths),
        global_script=Markup(GLOBAL_SCRIPT),
        icecast_url=_get_icecast_url(paths),
        css=Markup(BASE_CSS),
        breaking_banner=render_breaking_banner(paths),
        title=format_date_fr(day),
        date_fr=format_date_fr(day),
        day_label=format_day_label(day),
        report_html=report_html,
        article_count=len(articles),
        source_count=len(sources),
        gen_time=datetime.now().strftime("%H:%M"),
        categories=categories,
        cat_icons=_category_icons(paths),
        flash_button_html=render_flash_button_html(_category_icons(paths), _get_cat_labels(load_atlas_config(paths))),
        flash_button_js=FLASH_BUTTON_JS,
        stats=stats,
        recent_articles=recent,
        archives=archives,
        all_articles=all_arts_sorted,
        today_file=f"{today_str}_report.html",
        model=load_atlas_config(paths).get("ollama_model","—"),
        year=datetime.now().year,
    )


def load_edition(day: str, edition_name: str, paths: dict) -> str:
    from modules.web.config_security import confined_file
    f = confined_file(paths["editions"], f"{day}_{edition_name}.md")
    if not f.exists():
        return ""
    with open(f, "r", encoding="utf-8") as fp:
        text = fp.read()
    # Supprime le footer "Atlas News — ..." généré par atlas_editions.py
    import re as _re
    text = _re.sub(r'\n---\n\n\*Atlas News[^\n]*\*\s*$', '', text.rstrip())
    text = _re.sub(r'\*Atlas News[^\n]*\n?', '', text)
    text = _re.sub(r'\n---\s*$', '', text.rstrip())
    return text.strip()

def get_editions_of_day(day: str, paths: dict) -> list:
    return [
        (name, cfg)
        for name, cfg in EDITION_CONFIG.items()
        if (paths["editions"] / f"{day}_{name}.md").exists()
    ]

def render_edition_page(day: str, edition_name: str, paths: dict) -> str:
    from modules.web.web_security import html_template as Template
    md_text = load_edition(day, edition_name, paths)
    if not md_text:
        return ""

    cfg        = EDITION_CONFIG.get(edition_name, EDITION_CONFIG["matin"])
    lines      = md_text.split("\n")
    body_lines = []
    art_title  = ""
    in_header  = past_sep = False

    for line in lines:
        s = line.strip()
        if s.startswith("# "):
            in_header = True
            continue
        if s.startswith("---") and in_header and not past_sep:
            past_sep = True
            continue
        if past_sep:
            if not art_title and s and not s.startswith("*"):
                art_title = s.rstrip(".")
                continue
            body_lines.append(line)

    body_html     = render_markdown("\n".join(body_lines).strip())
    articles      = load_articles_for_day(day, paths)
    article_count = len([a for a in articles if not a.get("migrated")])
    editions_day  = get_editions_of_day(day, paths)
    today_str     = datetime.now().strftime("%Y%m%d")
    now           = datetime.now()
    months        = ["janvier","février","mars","avril","mai","juin",
                     "juillet","août","septembre","octobre","novembre","décembre"]
    date_fr       = f"{int(day[6:8])} {months[int(day[4:6])-1]} {int(day[:4])}"

    return Template(EDITION_TEMPLATE).render(
        **_branding(paths),
        global_script=Markup(GLOBAL_SCRIPT),
        icecast_url=_get_icecast_url(paths),
        css=Markup(BASE_CSS),
        breaking_banner=render_breaking_banner(paths),
        cat_icons=_category_icons(paths),
        flash_button_html=render_flash_button_html(_category_icons(paths), _get_cat_labels(load_atlas_config(paths))),
        flash_button_js=FLASH_BUTTON_JS,
        edition_label=cfg["label"],
        edition_emoji=cfg["emoji"],
        edition_color=cfg["color"],
        current_edition=edition_name,
        article_title=art_title or cfg["label"],
        date_fr=date_fr,
        article_count=article_count,
        gen_time=now.strftime("%H:%M"),
        body_html=body_html,
        image_articles=articles[:3],
        editions_of_day=editions_day,
        edition_configs=EDITION_CONFIG,
        day=day,
        today_report=f"{today_str}_report.html",
        year=now.year,
    )


def build_index(paths: dict) -> str:
    from modules.web.web_security import html_template as Template
    all_days       = get_available_days(paths)
    today_str      = datetime.now().strftime("%Y%m%d")
    total_articles = 0
    day_cards      = []

    for day in all_days:
        articles   = load_articles_for_day(day, paths)
        report_md  = load_report(day, paths)
        total_articles += len(articles)
        lines = [
            l.strip() for l in report_md.split("\n")
            if l.strip() and not l.startswith("#") and not l.startswith("*") and l != "---"
        ] if report_md else []
        excerpt = " ".join(lines[:2])[:200] if lines else ""
        sources = set(a.get("source","") for a in articles)
        day_cards.append({
            "file":          f"{day}_report.html",
            "date_fr":       format_date_fr(day),
            "day_label":     format_day_label(day),
            "excerpt":       excerpt or "Rapport en cours de génération...",
            "article_count": len(articles),
            "source_count":  len(sources),
        })

    today_file = f"{today_str}_report.html" if today_str in all_days else None
    return Template(INDEX_TEMPLATE).render(
        **_branding(paths),
        global_script=Markup(GLOBAL_SCRIPT),
        icecast_url=_get_icecast_url(paths),
        css=Markup(BASE_CSS),
        breaking_banner=render_breaking_banner(paths),
        cat_icons=_category_icons(paths),
        flash_button_html=render_flash_button_html(_category_icons(paths), _get_cat_labels(load_atlas_config(paths))),
        flash_button_js=FLASH_BUTTON_JS,
        days=day_cards,
        today_file=today_file,
        total_days=len(all_days),
        total_articles=total_articles,
        year=datetime.now().year,
    )


def get_current_edition_name() -> str:
    h = datetime.now().hour
    if h < 13:  return "matin"
    elif h < 20: return "midi"
    return "soir"


def build_homepage(paths: dict) -> str:
    today        = datetime.now().strftime("%Y%m%d")
    edition_name = get_current_edition_name()
    for ed in [edition_name, "soir", "midi", "matin"]:
        md = load_edition(today, ed, paths)
        if md:
            return render_edition_page(today, ed, paths)
    return build_live_feed(paths)


def _live_events(paths):
    from modules.editorial.index import EventIndex,IndexError,public_event
    try:
        data=EventIndex(paths['articles'],paths['data']).load()
        if not data:return []
        return [public_event(e) for e in sorted(data['events'],key=lambda e:e.get('latest_published_at') or '',reverse=True)[:6]]
    except (IndexError,OSError,ValueError,TypeError,KeyError):return []


def build_live_feed(paths: dict) -> str:
    """Fil en direct — articles des dernières N heures."""
    from modules.web.web_security import html_template as Template
    cfg          = load_atlas_config(paths)
    window_hours = cfg.get("live_window_hours", 20)
    cutoff       = datetime.now() - timedelta(hours=window_hours)

    today_str     = datetime.now().strftime("%Y%m%d")
    yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    articles = []
    for d in [yesterday_str, today_str]:
        articles.extend(load_articles_for_day(d, paths))

    valid = [a for a in articles
             if not a.get("migrated")
             and a.get("summary") and not a["summary"].startswith("[")
             and _parse_ts(a.get("timestamp","")) >= cutoff]

    valid.sort(key=lambda a: a.get("timestamp",""), reverse=True)

    # ── Round-robin PAR CATÉGORIE puis fusion chronologique ──────────────────
    # Pour chaque catégorie, on alterne les sources (cointelegraph, decrypt,
    # theblock…) afin qu'en filtrant "crypto" on voie plusieurs sources.
    # Ensuite le fil global fusionne toutes les catégories chrono.
    from collections import defaultdict as _dd

    # 1. Grouper par catégorie
    by_cat_raw: dict = _dd(list)
    for a in valid:
        by_cat_raw[a.get("category", "monde")].append(a)

    # 2. Pour chaque catégorie : round-robin par source
    def _rr_by_source(arts):
        by_src = _dd(list)
        for a in arts:
            by_src[a.get("source", "?")].append(a)
        result = []
        queues = list(by_src.values())
        while any(queues):
            for q in queues:
                if q:
                    result.append(q.pop(0))
            queues = [q for q in queues if q]
        return result

    by_cat_rr = {cat: _rr_by_source(arts) for cat, arts in by_cat_raw.items()}

    # 3. Fusion : on garde l'ordre chronologique global mais en s'assurant
    #    que les articles d'une même catégorie sont déjà variés en sources.
    #    On re-trie le tout par timestamp desc (les articles sont déjà variés
    #    dans chaque catégorie, donc le filtre per-cat donnera de la diversité).
    recent = sorted(
        [a for arts in by_cat_rr.values() for a in arts],
        key=lambda a: a.get("timestamp", ""),
        reverse=True
    )

    # Compteurs par catégorie pour les boutons de filtre
    cat_counts: dict = {}
    for a in recent:
        cat = a.get("category", "monde")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    cat_counts_sorted = sorted(cat_counts.items(), key=lambda x: -x[1])

    return Template(LIVE_TEMPLATE).render(
        **_branding(paths),
        global_script=Markup(GLOBAL_SCRIPT),
        icecast_url=_get_icecast_url(paths),
        css=Markup(BASE_CSS + LIVE_CSS_EXTRA),
        breaking_banner=render_breaking_banner(paths),
        cat_icons=_category_icons(paths),
        flash_button_html=render_flash_button_html(_category_icons(paths), _get_cat_labels(load_atlas_config(paths))),
        flash_button_js=FLASH_BUTTON_JS,
        editorial_events=_live_events(paths),
        articles=recent,          # fil chronologique unique
        cat_counts=cat_counts_sorted,
        total=len(recent),
        window_hours=window_hours,
        today_file=f"{today_str}_report.html",
        updated=datetime.now().strftime("%H:%M"),
        year=datetime.now().year,
    )


def build_config_yaml_page(paths: dict, config: dict) -> str:
    """Page de configuration complète depuis config.yaml."""
    from modules.web.web_security import html_template as Template
    from modules.web.config_security import safe_view
    config = safe_view(config, escape_html=False)
    svc    = config.get("service",  {})
    llm    = config.get("llm",   {})
    radio  = config.get("radio",    {})
    rss    = config.get("rss",      {})
    web    = config.get("web",      {})
    posts  = config.get("posts",    {})
    ic     = config.get("icecast",  {})
    voices = radio.get("voices",    {})

    # Toutes les voix connues organisées par langue
    ALL_VOICES = {
        "fr": [
            "fr-FR-HenriNeural","fr-FR-DeniseNeural","fr-FR-RemyMultilingualNeural",
            "fr-FR-VivienneMultilingualNeural","fr-BE-CharlineNeural","fr-BE-GerardNeural",
            "fr-CA-AntoineNeural","fr-CA-SylvieNeural","fr-CA-ThierryNeural",
            "fr-CH-ArianeNeural","fr-CH-FabriceNeural",
        ],
        "en": [
            "en-US-AriaNeural","en-US-GuyNeural","en-US-JennyNeural","en-US-DavisNeural",
            "en-GB-SoniaNeural","en-GB-RyanNeural","en-GB-LibbyNeural",
            "en-AU-NatashaNeural","en-AU-WilliamNeural",
        ],
        "de": ["de-DE-KatjaNeural","de-DE-ConradNeural","de-DE-AmalaNeural",
               "de-AT-IngridNeural","de-CH-LeniNeural"],
        "es": ["es-ES-ElviraNeural","es-ES-AlvaroNeural","es-MX-DaliaNeural",
               "es-AR-ElenaNeural"],
        "pt": ["pt-BR-FranciscaNeural","pt-BR-AntonioNeural",
               "pt-PT-RaquelNeural","pt-PT-DuarteNeural"],
        "it": ["it-IT-ElsaNeural","it-IT-DiegoNeural"],
        "nl": ["nl-NL-ColetteNeural","nl-NL-MaartenNeural"],
        "ru": ["ru-RU-SvetlanaNeural","ru-RU-DmitryNeural"],
        "ar": ["ar-SA-ZariyahNeural","ar-SA-HamedNeural","ar-EG-SalmaNeural"],
        "ja": ["ja-JP-NanamiNeural","ja-JP-KeitaNeural"],
        "zh": ["zh-CN-XiaoxiaoNeural","zh-CN-YunxiNeural","zh-TW-HsiaoChenNeural"],
    }
    LANG_LABELS = {
        "fr":"Français","en":"English","de":"Deutsch","es":"Español",
        "pt":"Português","it":"Italiano","nl":"Nederlands",
        "ru":"Русский","ar":"العربية","ja":"日本語","zh":"中文",
    }

    return Template(CONFIG_YAML_TEMPLATE.replace("</body>", '<script defer src="/assets/config.js"></script></body>')).render(
        **_branding(paths),
        global_script=Markup(GLOBAL_SCRIPT),
        icecast_url=_get_icecast_url(paths),
        svc=svc, llm=llm, ollama=llm, radio=radio, rss=rss,
        web=web, posts=posts, ic=ic,
        active_voices=voices,
        all_voices=ALL_VOICES,
        lang_labels=LANG_LABELS,
        cat_icons=_category_icons(paths),
        flash_button_html=render_flash_button_html(_category_icons(paths), _get_cat_labels(load_atlas_config(paths))),
        flash_button_js=FLASH_BUTTON_JS,
        year=datetime.now().year,
    )


def build_config_page(paths: dict) -> str:
    from modules.web.web_security import html_template as Template
    from modules.web.config_security import safe_view
    cfg = safe_view(load_atlas_config(paths), escape_html=False)
    return Template(CONFIG_TEMPLATE.replace("</body>", '<script defer src="/assets/simple-config.js"></script></body>')).render(
        **_branding(paths),
        global_script=Markup(GLOBAL_SCRIPT),
        icecast_url=_get_icecast_url(paths),
        css=Markup(BASE_CSS),
        cfg=cfg,
        cat_icons=_category_icons(paths),
        flash_button_html=render_flash_button_html(_category_icons(paths), _get_cat_labels(load_atlas_config(paths))),
        flash_button_js=FLASH_BUTTON_JS,
        models=AVAILABLE_MODELS,
        today_file=f"{datetime.now().strftime('%Y%m%d')}_report.html",
        year=datetime.now().year,
    )


# ─── PAGE GESTION DES FLUX RSS ────────────────────────────────────────────────

FEEDS_PAGE_JS = """
<style>
  #discovery-results { overflow-wrap:anywhere; }
  #source-discovery input { min-width:0;max-width:100%; }
  .feeds-grid { display: grid; gap: 14px; margin-top: 16px; }
  .feed-cat { background: #1a1d23; border: 1px solid #2a2e36; border-radius: 8px;
              padding: 12px 14px; }
  .feed-cat h3 { margin: 0 0 8px 0; font-size: 14px; color: #6ea8fe;
                 text-transform: uppercase; letter-spacing: 0.5px; }
  .feed-item { display: flex; align-items: center; gap: 6px; padding: 4px 0;
               border-bottom: 1px solid #232830; }
  .feed-item { flex-wrap: wrap; }
  .feed-observation { width:100%; font-size:12px; overflow-wrap:anywhere; }
  .add-form { flex-wrap:wrap; }
  .feed-item:last-child { border-bottom: none; }
  .feed-url { flex: 1; font-family: monospace; font-size: 12px; color: #d4d4d4;
              word-break: break-all; }
  .feed-url:hover { color: #6ea8fe; cursor: pointer; }
  .feed-btn { background: #2a2e36; color: #d4d4d4; border: none; padding: 3px 8px;
              border-radius: 4px; cursor: pointer; font-size: 12px; }
  .feed-btn:hover { background: #3a3e46; }
  .feed-btn.danger:hover { background: #b04848; }
  .feed-btn:disabled { opacity: 0.3; cursor: not-allowed; }
  .add-form { display: flex; gap: 6px; margin-top: 8px; }
  .add-form input, .add-form select { background: #0d1014; color: #d4d4d4;
              border: 1px solid #2a2e36; padding: 4px 8px; border-radius: 4px;
              font-size: 12px; }
  .add-form input { flex: 1; }
  .toast { position: fixed; bottom: 20px; right: 20px; background: #2a2e36;
           color: #d4d4d4; padding: 10px 16px; border-radius: 6px;
           box-shadow: 0 4px 12px rgba(0,0,0,0.4); opacity: 0;
           transition: opacity 0.3s; pointer-events: none; z-index: 9999; }
  .toast.show { opacity: 1; }
  .toast.error { background: #b04848; }
  .stats-bar { display: flex; gap: 20px; padding: 8px 14px; background: #1a1d23;
               border-radius: 6px; margin-bottom: 12px; font-size: 13px; }
  .stats-bar span { color: #6ea8fe; font-weight: bold; }
</style>

<div class="stats-bar">
  <div>Catégories: <span id="stat-cats">–</span></div>
  <div>Flux actifs: <span id="stat-active">–</span></div>
  <div>Désactivés: <span id="stat-disabled">–</span></div>
  <div style="margin-left: auto;"><a href="/config/feeds" class="feed-btn">JSON brut</a></div>
</div>

<form id="source-filter" class="add-form">
  <input id="source-search" placeholder="Rechercher un nom ou une URL" aria-label="Recherche">
  <select id="source-category" aria-label="Catégorie"><option value="">Toutes les catégories</option></select>
  <select id="source-health" aria-label="Santé"><option value="">Tous les états</option>
  <option>healthy</option><option>unchanged</option><option>empty</option><option>degraded</option><option>temporarily_unavailable</option><option>permanently_unavailable</option><option>blocked_by_security</option><option>never_checked</option><option>disabled</option></select>
  <button type="submit" class="feed-btn">Filtrer</button>
</form>
<div id="feeds-container" class="feeds-grid">Chargement…</div>
<div class="add-form"><button id="source-prev" class="feed-btn">Précédent</button><span id="source-pagination"></span><button id="source-next" class="feed-btn">Suivant</button></div>
<form id="source-test-form" class="add-form"><input id="source-test-url" placeholder="https://source.example/rss" required aria-label="URL à tester"><button type="submit" class="feed-btn">Tester sans ajouter</button></form>
<pre id="source-test-result" aria-live="polite" style="white-space:pre-wrap;overflow-wrap:anywhere;"></pre>
<details><summary>Import / export</summary>
<p><a href="/api/admin/sources/export?format=yaml">Exporter YAML</a> · <a href="/api/admin/sources/export?format=opml">Exporter OPML</a></p>
<form id="source-import-form"><select id="source-import-format" aria-label="Format"><option value="yaml">YAML</option><option value="opml">OPML</option></select>
<textarea id="source-import-content" maxlength="1048576" rows="5" aria-label="Liste à importer" style="width:100%;"></textarea>
<button type="submit" class="feed-btn">Prévisualiser</button><button id="source-import-apply" type="button" class="feed-btn" disabled>Appliquer les ajouts</button></form>
<pre id="source-import-result" style="white-space:pre-wrap;"></pre></details>

<form id="new-cat-form" class="add-form" style="margin-top: 20px;">
  <input id="new-cat-name" placeholder="Nouvelle catégorie…" required>
  <button class="feed-btn" type="submit">+ Catégorie</button>
</form>

<section id="source-discovery" style="margin-top:24px;">
<h2>Découvrir des sources</h2>
<p>Exploration statique bornée, sans ajout automatique. Recherche thématique disponible uniquement avec SearXNG configuré.</p>
<form id="discovery-form" class="add-form">
<select id="discovery-mode" aria-label="Mode de découverte"><option value="site">Site</option><option value="search">Sujet (SearXNG)</option></select>
<input id="discovery-input" placeholder="https://example.org" required aria-label="URL ou thème">
<button id="discovery-start" type="submit" class="feed-btn">Découvrir</button>
<button id="discovery-cancel" type="button" class="feed-btn" disabled>Annuler</button></form>
<p id="discovery-progress" aria-live="polite"></p>
<label>Résultats <select id="discovery-filter"><option value="">Tous</option><option value="verified">Vérifiés</option><option value="new">Nouveaux vérifiés</option></select></label>
<div id="discovery-results" class="feeds-grid"></div><pre id="discovery-preview" style="white-space:pre-wrap;overflow-wrap:anywhere;"></pre>
</section>
<p><a href="/radio-studio">Radio Studio — calendrier, jobs et canaux</a></p><section id="media-manager"><h2>Illustrations et droits</h2><p>Images locales seulement, sous approbation. Une licence déclarée ne garantit pas l'absence de droits de tiers. Illustrations génériques uniquement.</p>
<p id="media-state" aria-live="polite"></p>
<form id="media-search-form" class="add-form"><select id="media-target" aria-label="Article ou événement"></select><input id="media-query" placeholder="Sujet pour Commons" maxlength="120" required><button type="submit" class="feed-btn">Rechercher Commons</button><button id="media-rss" type="button" class="feed-btn">Candidats RSS en cache</button><button id="media-html" type="button" class="feed-btn">Inspecter la page</button><button id="media-cancel" type="button" class="feed-btn">Annuler</button></form>
<div id="media-records" class="feeds-grid"></div><button id="media-prev" class="feed-btn">Précédent</button><button id="media-next" class="feed-btn">Suivant</button></section>
<div class="toast" id="toast"></div>


"""


def build_feeds_page(paths: dict) -> str:
    """Source Manager in the existing persistent radio/navigation shell."""
    from modules.web.web_security import html_template as Template
    start=INDEX_TEMPLATE.index('<div class="page">')
    end=INDEX_TEMPLATE.index('<footer>')
    template=INDEX_TEMPLATE[:start].replace('— Archives</title>','— Sources</title>')+'<div class="page"><div id="source-manager"><h1>Sources et découverte RSS/Atom</h1>'+FEEDS_PAGE_JS+'</div></div>'+INDEX_TEMPLATE[end:]
    return Template(template).render(**_branding(paths),global_script=Markup(GLOBAL_SCRIPT),
        icecast_url=_get_icecast_url(paths),css=Markup(BASE_CSS),breaking_banner='',
        flash_button_html='',flash_button_js='',today_file=None,year=datetime.now().year)


# ─── PAGE PRÉFÉRENCES (filtre par catégorie) ─────────────────────────────────

PREFS_PAGE_JS = """
<style>
  .prefs-grid { display: grid; grid-template-columns: repeat(auto-fill,
              minmax(200px, 1fr)); gap: 10px; margin: 16px 0; }
  .pref-card { background: #1a1d23; border: 1px solid #2a2e36;
               border-radius: 8px; padding: 12px 14px; cursor: pointer;
               transition: border-color 0.15s, background 0.15s;
               user-select: none; }
  .pref-card:hover { border-color: #6ea8fe; }
  .pref-card.hidden { background: #0d1014; opacity: 0.55; }
  .pref-card input { margin-right: 8px; cursor: pointer; }
  .pref-card .cat-name { font-weight: bold; font-size: 14px;
                         text-transform: capitalize; color: #d4d4d4; }
  .pref-card.hidden .cat-name { text-decoration: line-through;
                                color: #888; }
  .pref-bar { display: flex; gap: 12px; align-items: center;
              padding: 8px 14px; background: #1a1d23; border-radius: 6px;
              margin-bottom: 16px; }
  .pref-bar .badge { background: #2a2e36; padding: 2px 8px;
                     border-radius: 4px; font-size: 12px; }
  .toast { position: fixed; bottom: 20px; right: 20px; background: #2a2e36;
           color: #d4d4d4; padding: 10px 16px; border-radius: 6px;
           opacity: 0; transition: opacity 0.3s; pointer-events: none;
           z-index: 9999; }
  .toast.show { opacity: 1; }
  .pref-info { background: #1a1d23; border-left: 3px solid #6ea8fe;
               padding: 10px 14px; margin-bottom: 14px; font-size: 13px;
               color: #aaa; }
</style>

<div class="pref-info">
  💡 <b>Fonctionnement :</b> coche ou décoche une catégorie pour la
  montrer/cacher dans le site. Le filtrage est appliqué en temps réel
  sur l'accueil et le live feed. Le backend continue de tout fetcher,
  seules les catégories cochées apparaissent à l'écran.
</div>

<div class="pref-bar">
  <span>Affichées : <span id="visible-count" class="badge">–</span> / <span id="total-count" class="badge">–</span></span>
  <button class="feed-btn" id="reset-btn" style="margin-left:auto;">Tout afficher</button>
</div>

<div id="prefs-container" class="prefs-grid">Chargement…</div>

<div class="toast" id="toast"></div>


"""


def build_preferences_page(paths: dict) -> str:
    """Page HTML de préférences (filtre par catégorie, MVP single-user)."""
    from modules.web.web_security import html_template as Template
    return Template("""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>{{ brand }} — Préférences</title>
  <link rel="stylesheet" href="/style.css">
  <style>{{ css }}</style>
</head>
<body>
  <header style="display:flex;justify-content:space-between;align-items:center;
                 padding:12px 20px;background:#0d1014;border-bottom:1px solid #2a2e36;">
    <h1 style="margin:0;font-size:18px;">{{ brand }} — Préférences</h1>
    <nav>
      <a href="/" style="color:#6ea8fe;text-decoration:none;margin-right:14px;">← Accueil</a>
      <a href="/config/feeds/page" style="color:#6ea8fe;text-decoration:none;margin-right:14px;">Flux</a>
      <a href="/config/yaml" style="color:#6ea8fe;text-decoration:none;">Config YAML</a>
    </nav>
  </header>
  <main style="max-width:1100px;margin:0 auto;padding:20px;">
    <h2 style="font-size:22px;margin:0 0 12px 0;">Catégories de news</h2>
    <p style="color:#888;font-size:13px;margin:0 0 16px 0;">
      Sélectionne les catégories que tu veux voir dans ton flux.
    </p>
    {{ extra_js }}
  </main>
</body>
</html>""").render(
        **_branding(paths),
        global_script="",
        icecast_url="",
        css="",
        extra_js=Markup(PREFS_PAGE_JS + '<script defer src="/assets/preferences.js"></script>'),
        cat_icons=_category_icons(paths),
        flash_button_html=render_flash_button_html(_category_icons(paths), _get_cat_labels(load_atlas_config(paths))),
        flash_button_js=FLASH_BUTTON_JS,
    )

# ─── STATIC SITE GENERATOR ────────────────────────────────────────────────────

def generate_static_site(config: dict, full: bool = False, *, root: Path = None) -> str:
    paths = _resolve_paths(config, root=root)
    paths["site"].mkdir(parents=True, exist_ok=True)

    import shutil
    shutil.copytree(Path(__file__).with_name('assets'), paths['site'] / 'assets', dirs_exist_ok=True)
    from modules.media.export import export_images
    from modules.media.service import MediaService
    export_images(MediaService(paths['articles'],paths['data'],config.get('media',{})),paths['site'])
    all_days  = get_available_days(paths)
    today     = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

    days_to_build = all_days if full else [d for d in all_days if d in (today, yesterday)]

    generated = 0
    for day in days_to_build:
        html = render_report_page(day, paths)
        if html:
            out = paths["site"] / f"{day}_report.html"
            out.write_text(html, encoding="utf-8")
            generated += 1

    (paths["site"] / "index.html").write_text(build_index(paths), encoding="utf-8")

    mode = "complet" if full else "incrémental"
    print(f"Site {mode} : {generated} rapport(s) → {paths['site']}")
    return str(paths["site"])

# ─── FLASK APP ────────────────────────────────────────────────────────────────

def _sync_icecast_to_atlas_config(config: dict, paths: dict):
    """Copie les paramètres Icecast du config.yaml vers atlas_config.json."""
    try:
        ic  = config.get("icecast", {})
        cfg = load_atlas_config(paths)
        cfg["icecast_host"]  = ic.get("host",  "localhost")
        cfg["icecast_port"]  = ic.get("port",  8000)
        cfg["icecast_mount"] = ic.get("mount", "/nova")
        save_atlas_config(paths, cfg)
    except Exception:
        pass


def create_app(config: dict, *, root: Path = None):
    """
    Build the Flask application without starting services or writing configuration.
    config is supplied by the caller; root fixes all configuration lookups.
    """
    from flask import Flask, abort, request, jsonify, send_from_directory

    # Explicit root removes fallbacks to the real checkout in tests/deployments.
    from modules.web.config_security import update_yaml, simple_update, confined_file, public_feeds
    from modules.web.admin_security import install_admin_security
    if root is None:
        root = Path.cwd()
    root = Path(root).resolve()
    paths = _resolve_paths(config, root=root)
    for key, setting, default in (
        ('articles', 'articles_dir', 'data/articles'), ('reports', 'reports_dir', 'data/reports'),
        ('editions', 'editions_dir', 'data/editions'), ('site', 'site_dir', 'site'),
        ('data', 'data_dir', 'data')):
        paths[key] = (root / config.get('paths', {}).get(setting, default)).resolve()
    paths['root'] = root
    paths['config_file'] = confined_file(root / 'config', 'config.yaml')
    paths['feeds_file'] = confined_file(root / 'config', 'feeds.yaml')
    # Factory has no disk writes; startup sync stays in run_server.
    app = Flask(__name__, static_folder=None)
    from modules.web.web_security import install_web_security
    install_web_security(app)
    install_admin_security(app)
    app.extensions["atlas_paths"] = paths
    app.extensions["atlas_background_jobs"] = []
    @app.get('/assets/<name>')
    def web_asset(name):
        allowed = {'web.js','flash.js','csrf.js','config.js','simple-config.js','feeds.js','preferences.js','media.js','studio.js','news-fallback.svg'}
        if name not in allowed:
            abort(404)
        return send_from_directory(Path(__file__).with_name('assets'), name)

    # ── Proxy stream Icecast → HTTPS (mixed-content fix) ──────────────
    # Quand le site est servi en HTTPS (reverse proxy), le browser refuse
    # de charger le stream HTTP (mixed content). Cette route Flask sert
    # de proxy : le browser charge /stream/nova (même origine HTTPS) et
    # Flask stream le contenu depuis Icecast en interne.
    from modules.http.icecast import open_stream
    from flask import Response as _Resp
    @app.route("/stream/<path:mount>")
    def stream_proxy(mount: str):
        cfg  = load_atlas_config(paths)
        host  = cfg.get("icecast_host",  "localhost")
        port  = cfg.get("icecast_port",  8000)
        allowed_mounts = {cfg.get('icecast_mount', '/nova'),
                          config.get('icecast', {}).get('android_mount', '/nova-android')}
        try:
            req, upstream_session = open_stream(host, port, '/' + mount, allowed_mounts)
            closed = False
            def close_upstream():
                nonlocal closed
                if not closed:
                    closed = True
                    try:
                        req.close()
                    finally:
                        upstream_session.close()
            def audio_chunks():
                try:
                    for chunk in req.iter_content(chunk_size=4096):
                        event=app.extensions.get('atlas_stop_event')
                        if event is not None and event.is_set():
                            break
                        yield chunk
                finally:
                    close_upstream()
            result = _Resp(audio_chunks(), content_type="audio/mpeg", status=req.status_code)
            result.call_on_close(close_upstream)  # Also close HEAD/unstarted iterators.
            return result
        except Exception as e:
            return "Stream indisponible", 502

    @app.route("/")
    def homepage():
        return build_homepage(paths)

    @app.route("/archives")
    @app.route("/index.html")
    def index():
        return build_index(paths)

    @app.route("/live")
    @app.route("/live.html")
    def live():
        return build_live_feed(paths)

    @app.route("/config")
    def config_page():
        # Redirige vers la config YAML complète
        from flask import redirect
        return redirect("/config/yaml")

    @app.route("/config/simple")
    def config_simple_page():
        return build_config_page(paths)

    @app.route("/config/save", methods=["POST"])
    def config_save():
        try:
            simple_update(paths, request.get_json(), load_atlas_config, save_atlas_config)
            return jsonify({"status": "ok"})
        except ValueError:
            return jsonify(status="error", msg="Configuration invalide"), 400
        except Exception:
            return jsonify(status="error", msg="Écriture impossible"), 500

    @app.route("/config/yaml")
    def config_yaml_page():
        # Recharge config depuis le disque pour afficher les valeurs actuelles
        from modules.web.config_security import read_config as _load_cfg
        cfg_path = paths["config_file"]
        try:
            live_config = _load_cfg(str(cfg_path))
        except Exception:
            live_config = config
        return build_config_yaml_page(paths, live_config)

    @app.route("/config/restart", methods=["POST"])
    def config_restart():
        """
        Recharge la config dans tous les modules à chaud.
        Crée data/.reload_config — détecté par run_news_engine() à la prochaine boucle.
        Rebuild aussi le site statique avec la nouvelle config (langue, nom, etc.)
        """
        try:
            flag = confined_file(paths["data"], ".reload_config")
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch()
            # Recharge la config depuis le disque et rebuild le site
            from modules.web.config_security import read_config as _load_cfg
            cfg_path = paths["config_file"]
            try:
                new_cfg  = _load_cfg(str(cfg_path))
                generate_static_site(new_cfg, full=True, root=root)
            except Exception as e:
                pass  # Le rebuild est best-effort
            return jsonify({"status": "ok", "msg": "Rechargement config + rebuild site demandés"})
        except Exception as e:
            return jsonify({"status": "error", "msg": "Opération impossible"}), 500

    @app.route("/config/yaml/save", methods=["POST"])
    def config_yaml_save():
        try:
            data = request.get_json()
            if not isinstance(data, dict) or set(data) != {'section', 'key', 'value'}:
                return jsonify(status="error", msg="Champ unique requis"), 400
            update_yaml(paths["config_file"], data['section'], data['key'], data['value'])
            return jsonify(status="ok")
        except ValueError:
            return jsonify(status="error", msg="Configuration invalide"), 400
        except Exception:
            return jsonify(status="error", msg="Écriture impossible"), 500

    # ─── GESTION DES FLUX RSS ────────────────────────────────────────────────
    # GET  /config/feeds          → liste les flux depuis config/feeds.yaml
    # POST /config/feeds/add      → ajoute un flux (body: category, url)
    # POST /config/feeds/remove   → retire un flux (body: category, url)
    # POST /config/feeds/reset    → recharge depuis le disque (force)

    from modules.web.source_api import install_source_api
    install_source_api(app, paths)
    from modules.web.discovery_api import install_discovery_api
    install_discovery_api(app, paths, config)
    from modules.web.events_api import install_events_api
    install_events_api(app, paths)
    from modules.web.media_api import install_media_api
    install_media_api(app, paths, config)
    from modules.web.studio_api import install_studio_api
    install_studio_api(app,paths,config)
    from modules.web.public_api import install_public_api
    install_public_api(app,paths,config)

    @app.route("/config/feeds/page", methods=["GET"])
    def feeds_page():
        """Page HTML de gestion des flux (UI niveau 1)."""
        return build_feeds_page(paths)

    # ─── PRÉFÉRENCES UTILISATEUR (filtre par catégorie) ────────────────────
    # GET  /api/preferences          → lit les prefs (hidden_categories)
    # POST /api/preferences/toggle   → {category: "tech"} toggle
    # POST /api/preferences/reset    → reset (tout visible)
    # GET  /preferences              → page HTML avec checkboxes

    @app.route("/api/preferences", methods=["GET"])
    def api_get_preferences():
        from modules.core.preferences import load_prefs
        prefs = load_prefs(confined_file(paths["data"], "preferences.json"))
        return jsonify({
            "status": "ok",
            "preferences": {'hidden_categories': prefs.get('hidden_categories', []),
                            'updated_at': prefs.get('updated_at')},
        })

    @app.route("/api/preferences/toggle", methods=["POST"])
    def api_toggle_preference():
        try:
            data = request.get_json(force=True)
            category = (data.get("category") or "").strip()
            if not category:
                return jsonify({"status": "error",
                                "msg": "category requise"}), 400
            from modules.core.preferences import (
                load_prefs, save_prefs, toggle_category
            )
            prefs_path = confined_file(paths["data"], "preferences.json")
            from copy import deepcopy
            prefs = deepcopy(load_prefs(prefs_path))
            new_state = toggle_category(prefs, category)
            save_prefs(prefs_path, prefs)
            return jsonify({
                "status": "ok",
                "category": category,
                "hidden": new_state,
            })
        except Exception as e:
            return jsonify({"status": "error", "msg": "Opération impossible"}), 500

    @app.route("/api/preferences/reset", methods=["POST"])
    def api_reset_preferences():
        from modules.core.preferences import save_prefs, DEFAULT_PREFS
        save_prefs(confined_file(paths["data"], "preferences.json"), dict(DEFAULT_PREFS))
        return jsonify({"status": "ok", "msg": "préférences réinitialisées"})

    # ──────────────────────────────────────────────────────────────────
    #  Flash : bulletin court spécialisé par catégorie, à la demande
    # ──────────────────────────────────────────────────────────────────
    # POST /api/flash {category: "geopolitique"}
    #   → {"status":"ok", "job_id": "abc123", "msg": "..."}
    # GET  /api/flash/status/<job_id>
    #   → {"status":"running"|"done"|"error", "progress": 0-100, "mp3_path": "..."}
    # GET  /audio/flash_<job_id>.mp3
    #   → sert le fichier mp3 généré

    import threading
    import uuid as _uuid
    from datetime import datetime as _dt

    _flash_jobs: dict = {}  # bounded to 32 jobs; one active generation
    _flash_guard = threading.Lock()

    @app.route("/api/subscription/status", methods=["GET"])
    def api_subscription_status():
        """Verifie le statut premium d'un device_id (cote serveur, anti-triche).

        Query params:
            device_id : identifiant unique de l'app (fourni par l'app Android)

        Reponse:
            {status, device_id, is_premium, expires_at, source}

        Note: pour le moment, AUCUN device n'est premium (compte Google Play
        pas encore cree). Quand Play Billing sera branche, l'app enverra
        les receipts ici, on les validera avec Google, et on stockera
        device_id -> {is_premium: true, expires_at: ...} dans un fichier
        data/subscriptions.json (gitignore).
        """
        try:
            from pathlib import Path as _Path
            import json as _json
            device_id = (request.args.get("device_id") or "").strip()
            if not device_id:
                return jsonify({"status": "error", "msg": "device_id requis"}), 400

            subs_path = confined_file(root / "data", "subscriptions.json")
            subs = {}
            if subs_path.exists():
                try:
                    subs = _json.loads(subs_path.read_text())
                except Exception:
                    subs = {}

            sub = subs.get(device_id, {})
            is_premium = bool(sub.get("is_premium", False))
            expires_at = sub.get("expires_at")

            # Si expires_at est dans le passe, plus premium
            if is_premium and expires_at:
                from datetime import datetime as _dt
                try:
                    if _dt.fromisoformat(expires_at) < _dt.now():
                        is_premium = False
                except ValueError:
                    pass

            return jsonify({
                "status": "ok",
                "device_id": device_id,
                "is_premium": is_premium,
                "expires_at": expires_at,
                "source": "nova-atlas",
            })
        except Exception as e:
            return jsonify({"status": "error", "msg": "Opération impossible"}), 500

    @app.route("/api/articles", methods=["GET"])
    def api_articles():
        """Retourne la liste des articles du jour (ou d'une date donnée en query param).

        Query params:
            date : YYYYMMDD (optionnel, défaut = aujourd'hui)
            category : filtre catégorie (optionnel)
            limit : nombre max d'articles (optionnel, défaut = 200)

        Réponse:
            {"status": "ok", "date": "20260709", "count": N, "articles": [...]}
        """
        try:
            from datetime import datetime as _dt
            day = request.args.get("date") or _dt.now().strftime("%Y%m%d")
            category = request.args.get("category")
            try:
                datetime.strptime(day, '%Y%m%d')
                if not re.fullmatch(r'[0-9]{8}', day):
                    raise ValueError
            except ValueError:
                return jsonify(status='error', msg='Date invalide'), 400
            try:
                limit = int(request.args.get("limit", "200"))
            except ValueError:
                limit = 200

            articles = load_articles_for_day(day, paths)

            if category:
                articles = [a for a in articles if a.get("category") == category]

            # Tri du plus récent au plus ancien
            articles.sort(key=lambda a: a.get("timestamp", ""), reverse=True)

            # Cap
            articles = articles[:limit]

            return jsonify({
                "status": "ok",
                "date": day,
                "count": len(articles),
                "articles": articles,
            })
        except Exception as e:
            return jsonify({"status": "error", "msg": "Opération impossible"}), 500

    @app.route("/api/flash", methods=["POST"])
    def api_create_flash():
        try:
            data = request.get_json(force=True)
            category = (data.get("category") or "").strip()
            if not category:
                return jsonify({"status": "error", "msg": "category requise"}), 400
            # Validation catégorie
            if category not in {c.id for c in registry(paths['feeds_file'])}:
                return jsonify({"status": "error", "msg": f"category inconnue: {category}"}), 400

            if not _flash_guard.acquire(blocking=False):
                return jsonify(status='error', msg='Génération déjà en cours'), 429
            if len(_flash_jobs) >= 32:
                _flash_jobs.pop(next(iter(_flash_jobs)))
            job_id = _uuid.uuid4().hex[:12]
            _flash_jobs[job_id] = {
                "status": "running",
                "progress": 0,
                "mp3_path": None,
                "error": None,
                "category": category,
                "started_at": _dt.now().isoformat(),
            }

            def _run_flash():
                studio_job=None;studio_lease=None
                studio_journal=app.extensions['atlas_radio_studio']
                try:
                    if studio_journal.configured:
                        from modules.radio.studio import lease
                        candidate_lease=lease(studio_journal.root/'generation.lock');candidate_lease.__enter__();studio_lease=candidate_lease
                        studio_job=studio_journal.admit('manual:'+_dt.now().strftime('%Y%m%d_%H%M%S')+':'+job_id,kind='flash')
                        if not studio_job or studio_job['status']!='admitted':raise ValueError('flash_not_admitted')
                        studio_journal.start(studio_job['id'])
                    from modules.radio.bulletin_generator import (
                        get_flash_articles, generate_flash_script, BulletinGenerator
                    )
                    import yaml
                    # 1. Charge la config globale
                    cfg = yaml.safe_load(paths["config_file"].read_text())

                    # 2. Récupère les articles de la catégorie depuis minuit
                    _flash_jobs[job_id]["progress"] = 10
                    articles = get_flash_articles(paths, category, since_minute_of_day=0)
                    _flash_jobs[job_id]["articles_count"] = len(articles)
                    if studio_job:studio_journal.references(studio_job['id'],articles)

                    if len(articles) < 1:
                        _flash_jobs[job_id]["status"] = "error"
                        _flash_jobs[job_id]["error"] = "Aucun article dans cette catégorie aujourd'hui"
                        return

                    # 3. Génère le script via le prompt spécialisé Flash
                    # (qui dit "depuis minuit" et non "30 dernières minutes").
                    _flash_jobs[job_id]["progress"] = 20
                    cat_label = _get_cat_labels(cfg).get(category, category)
                    # Charge intros/outros
                    msgs_path = paths["root"] / "config" / "messages.yaml"
                    msgs = yaml.safe_load(msgs_path.read_text()) if msgs_path.exists() else {}
                    intros = [i for i in msgs.get("intros", []) if not str(i).startswith("#")]
                    outros = [o for o in msgs.get("outros", []) if not str(o).startswith("#")]
                    # Lance un heartbeat qui update le progress toutes les 3s
                    # pendant l'appel LLM (qui peut prendre 1-10 min pour les
                    # gros prompts). Sans ça, la barre reste bloquée à 20%.
                    import threading, time as _time
                    _stop_hb = threading.Event()
                    def _heartbeat():
                        # Couvre toutes les phases de génération (20% → 90%)
                        # pour que la barre de progression avance en continu
                        # même pendant le LLM (qui peut prendre 1-10 min) et
                        # le TTS (qui peut prendre 30s-1min). Sans ça, la
                        # barre reste figée entre les paliers backend.
                        p = 20
                        while not _stop_hb.is_set():
                            _time.sleep(2)
                            if _stop_hb.is_set(): break
                            # Ralentit quand on approche des paliers backend
                            # pour ne pas dépasser avant que le backend ne suive
                            if p < 49:        # avant LLM fini (palier 50%)
                                p += 1
                            elif p < 59:      # avant TTS (palier 60%)
                                p += 1
                            elif p < 89:      # avant mix (palier 90%)
                                p += 1
                            else:
                                break  # proche de la fin, laisse le backend finir
                            _flash_jobs[job_id]["progress"] = p
                    _hb_thread = threading.Thread(target=_heartbeat, daemon=True, name=f"FlashHB_{job_id}")
                    _hb_thread.start()
                    try:
                        script = generate_flash_script(
                            articles, category, cat_label, cfg, intros, outros
                        )
                    finally:
                        _stop_hb.set()
                        _hb_thread.join(timeout=2)
                    if not script:
                        _flash_jobs[job_id]["status"] = "error"
                        _flash_jobs[job_id]["error"] = "LLM a renvoyé un script flash vide"
                        return
                    _flash_jobs[job_id]["progress"] = 50

                    # 4. TTS + mix via BulletinGenerator avec le script pré-généré
                    # (pas de double appel LLM).
                    gen = BulletinGenerator(cfg, paths)
                    _flash_jobs[job_id]["progress"] = 60
                    studio_args={}
                    if studio_job:
                        studio_args={'publication_name':studio_job['publication_name'],'observer':lambda phase,**values:(studio_journal.tts_event if phase in ('tts_bridge_selected','tts_voice_planned') else studio_journal.metrics)(studio_job['id'],phase,**values),'cancel_event':app.extensions.get('atlas_stop_event')}
                    out_path = gen.build(articles, script=script, profile='flash',**studio_args)
                    _flash_jobs[job_id]["progress"] = 90
                    from modules.radio.audio_integrity import validated_publication
                    if not out_path or not validated_publication(out_path):
                        _flash_jobs[job_id]["status"] = "error"
                        _flash_jobs[job_id]["error"] = "Échec TTS/mix"
                        return

                    if studio_job:studio_journal.attach(studio_job['id'],out_path)
                    # 5. Done (generation only, never a broadcast acknowledgement).
                    _flash_jobs[job_id]["status"] = "done"
                    _flash_jobs[job_id]["progress"] = 100
                    _flash_jobs[job_id]["mp3_path"] = str(out_path)
                except Exception as e:
                    _flash_jobs[job_id]["status"] = "error"
                    _flash_jobs[job_id]["error"] = "Échec de génération"
                finally:
                    if studio_job:
                        try:
                            if studio_journal.load()[0]['jobs'][studio_job['id']]['status']=='generating':studio_journal.fail(studio_job['id'])
                        except (ValueError,OSError):pass
                    if studio_lease:studio_lease.__exit__(None,None,None)
                    _flash_guard.release()

            try:
                jobs=app.extensions['atlas_background_jobs']
                jobs[:]=[job for job in jobs if job.is_alive()]
                job_thread=threading.Thread(target=_run_flash, daemon=True, name=f"FlashJob_{job_id}")
                jobs.append(job_thread)
                job_thread.start()
            except Exception:
                _flash_guard.release()
                _flash_jobs[job_id]['status'] = 'error'
                raise
            return jsonify({"status": "ok", "job_id": job_id, "category": category})
        except Exception as e:
            return jsonify({"status": "error", "msg": "Opération impossible"}), 500

    @app.route("/api/flash/status/<job_id>", methods=["GET"])
    def api_flash_status(job_id):
        job = _flash_jobs.get(job_id)
        if not job:
            return jsonify({"status": "error", "msg": "job inconnu"}), 404
        return jsonify({
            "status": job["status"],
            "progress": job.get("progress", 0),
            "mp3_path": (f"/audio/flash_{job_id}.mp3" if job.get("status") == "done" else None),
            "error": job.get("error"),
            "articles_count": job.get("articles_count", 0),
            "category": job.get("category"),
        })

    @app.route("/audio/flash_<job_id>.mp3", methods=["GET"])
    def serve_flash_mp3(job_id):
        job = _flash_jobs.get(job_id)
        if not job or job.get("status") != "done" or not job.get("mp3_path"):
            return jsonify({"status": "error", "msg": "mp3 non disponible"}), 404
        from flask import send_file
        audio_root = (root / config.get('paths', {}).get('audio_queue', 'audio_queue')).resolve()
        audio_path = Path(job['mp3_path']).resolve()
        from modules.radio.audio_integrity import validated_publication
        if not audio_path.is_relative_to(audio_root) or not validated_publication(audio_path):
            abort(404)
        return send_file(audio_path, mimetype="audio/mpeg", as_attachment=False)

    @app.route("/preferences", methods=["GET"])
    def preferences_page():
        return build_preferences_page(paths)

    @app.route("/editions/<path:filename>")
    def serve_edition(filename):
        if re.fullmatch(r"[0-9]{8}_(matin|midi|soir)\.html", filename):
            parts = filename[:-5].split("_")
            if len(parts) == 2:
                html = render_edition_page(parts[0], parts[1], paths)
                if html:
                    return html
        abort(404)

    @app.route("/<path:filename>")
    def serve_page(filename):
        if re.fullmatch(r"[0-9]{8}_report\.html", filename):
            html = render_report_page(filename[:8], paths)
            if html:
                return html
            abort(404)
        # Generated HTML is rendered through audited routes, never served from arbitrary files.
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.(css|png|jpg|jpeg|webp|ico|woff2)", filename):
            abort(404)
        target = (paths['site'] / filename).resolve()
        if not target.is_relative_to(paths['site']):
            abort(404)
        try:
            return send_from_directory(str(paths["site"]), filename)
        except Exception:
            abort(404)

    return app


def run_server(config: dict, host: str = "0.0.0.0", port: int = 5055,
               debug: bool = False, *, stop_event=None):
    app = create_app(config)
    _sync_icecast_to_atlas_config(config, app.extensions['atlas_paths'])
    # Access logs can include credential-bearing query strings; do not enable them.
    import logging
    logging.getLogger('werkzeug').setLevel(logging.CRITICAL)
    # The interactive debugger must never expose configuration or execution access.
    if stop_event is None:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    else:
        from modules.web.lifecycle import serve
        serve(app,host,port,stop_event)

# ─── TEMPLATES HTML ───────────────────────────────────────────────────────────
# (identiques à la version originale — seules les références aux chemins ont
#  été supprimées du code Python, les templates HTML ne changent pas)

BASE_CSS = """
[hidden]{display:none!important;}



/* ── DARK MODE (défaut) ── */
:root {
  --bg:#0a0a0f; --bg2:#111118; --bg3:#1a1a24; --border:#2a2a3a;
  --accent:#e8612a; --accent2:#c44d1e; --gold:#c9a84c;
  --text:#e2e2e8; --text-dim:#8888a0; --text-muted:#555568;
  --radius:8px; --shadow:0 4px 24px rgba(0,0,0,0.5);
  --topbar-bg:rgba(10,10,15,0.92);
  --player-bg:#111118; --player-border:#2a2a3a;
}

/* ── LIGHT MODE ── */
body.light {
  --bg:#f4f6fb; --bg2:#ffffff; --bg3:#edf0f7; --border:#d0d7e8;
  --accent:#3b55e8; --accent2:#2d44cc; --gold:#7c6930;
  --text:#1a1d2e; --text-dim:#4a5068; --text-muted:#8a90a8;
  --shadow:0 4px 24px rgba(0,0,0,0.10);
  --topbar-bg:rgba(244,246,251,0.94);
  --player-bg:#ffffff; --player-border:#d0d7e8;
}
/* Light mode : textes en gras pour meilleure lisibilité */
body.light .topbar-nav a{font-weight:600;}
body.light .bb-item{font-weight:600;color:#1a1d2e;}
body.light .bb-sep{color:#3b55e8;opacity:.5;}
body.light .filter-btn{font-weight:600;}
body.light .live-item-title{font-weight:600;color:#1a1d2e;}
body.light .live-cat-label{font-weight:700;}
body.light .live-source-tag{font-weight:700;}
body.light .card-title{font-weight:700;}
body.light .hero-title{font-weight:800;}
body.light .report-body h2{font-weight:800;}

*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'Source Sans 3',sans-serif;font-weight:300;line-height:1.7;min-height:100vh;transition:background .25s,color .25s;}

/* ── TOPBAR (avec player intégré) ── */
.topbar{position:sticky;top:0;z-index:100;background:var(--topbar-bg);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);padding:0 1.5rem;display:flex;align-items:center;justify-content:space-between;height:56px;gap:1rem;}
.topbar-logo{font-family:'JetBrains Mono',monospace;font-size:1.1rem;font-weight:500;color:var(--accent);letter-spacing:.15em;text-decoration:none;flex-shrink:0;}
.topbar-logo span{color:var(--text-dim);}
.topbar-nav{display:flex;gap:1.5rem;align-items:center;}
.topbar-nav a{color:var(--text-dim);text-decoration:none;font-size:.85rem;letter-spacing:.05em;transition:color .2s;}
.topbar-nav a:hover{color:var(--text);}

/* ── RADIO PLAYER (dans la topbar) ── */
.radio-player{
  display:flex;align-items:center;gap:.6rem;
  background:var(--player-bg);
  border:1px solid var(--player-border);
  border-radius:20px;
  padding:.25rem .75rem .25rem .5rem;
  flex-shrink:0;
}
.radio-btn{
  width:28px;height:28px;border-radius:50%;border:none;cursor:pointer;
  background:var(--accent);color:#fff;
  display:flex;align-items:center;justify-content:center;
  font-size:.75rem;transition:background .2s,transform .15s;
  flex-shrink:0;
}
.radio-btn:hover{background:var(--accent2);transform:scale(1.08);}
.radio-label{
  font-family:'JetBrains Mono',monospace;font-size:.68rem;
  color:var(--text-dim);letter-spacing:.05em;white-space:nowrap;
}
.radio-wave{
  display:flex;align-items:center;gap:2px;height:14px;
  opacity:0;transition:opacity .3s;
}

/* ── FLASH BUTTON (a droite du radio-player) ── */
.flash-wrap{position:relative;flex-shrink:0;}
.flash-btn{
  display:flex;align-items:center;gap:.4rem;
  background:linear-gradient(135deg,#f59e0b 0%,#ef4444 100%);
  color:#fff;border:none;cursor:pointer;
  border-radius:20px;padding:.35rem .9rem;
  font-family:'JetBrains Mono',monospace;font-size:.7rem;font-weight:600;
  letter-spacing:.05em;transition:transform .15s,box-shadow .2s;
  box-shadow:0 2px 8px rgba(245,158,11,0.3);
}
.flash-btn:hover{transform:scale(1.04);box-shadow:0 4px 12px rgba(245,158,11,0.5);}
.flash-btn:disabled{opacity:0.6;cursor:wait;transform:none;}
.flash-icon{font-size:.9rem;animation:flash-pulse 2s ease-in-out infinite;}
@keyframes flash-pulse{0%,100%{opacity:1;}50%{opacity:.5;}}
.flash-dropdown{
  display:none;position:absolute;top:calc(100% + .5rem);right:0;
  background:var(--player-bg);border:1px solid var(--player-border);
  border-radius:12px;padding:.5rem;min-width:240px;max-height:400px;overflow-y:auto;
  box-shadow:0 8px 24px rgba(0,0,0,0.4);z-index:200;
  flex-direction:column;gap:.25rem;
}
.flash-dropdown.open{display:flex;}
.flash-cat{
  display:flex;align-items:center;gap:.6rem;
  padding:.5rem .7rem;border-radius:8px;cursor:pointer;
  font-size:.85rem;color:var(--text);transition:background .15s;
  text-align:left;background:transparent;border:none;width:100%;
}
.flash-cat:hover{background:var(--accent);color:#fff;}
.flash-cat-icon{font-size:1rem;flex-shrink:0;}
.flash-cat-label{flex:1;font-family:'JetBrains Mono',monospace;font-size:.75rem;}
.flash-cat-count{
  font-size:.65rem;color:var(--text-dim);
  background:var(--bg-secondary);padding:.1rem .4rem;border-radius:8px;
}
.flash-progress{
  display:none;position:absolute;top:calc(100% + .5rem);right:0;
  background:var(--player-bg);border:1px solid var(--player-border);
  border-radius:12px;padding:1rem;min-width:280px;z-index:200;
  box-shadow:0 8px 24px rgba(0,0,0,0.4);
  flex-direction:column;gap:.5rem;
}
.flash-progress.open{display:flex;}
.flash-progress-title{
  display:flex;align-items:center;gap:.5rem;
  font-size:.8rem;font-weight:600;color:var(--text);
  font-family:'JetBrains Mono',monospace;
}
.flash-progress-bar{
  width:100%;height:6px;background:var(--bg-secondary);
  border-radius:3px;overflow:hidden;
}
.flash-progress-fill{
  height:100%;background:linear-gradient(90deg,#f59e0b,#ef4444);
  border-radius:3px;transition:width 1.4s linear;width:0;
  box-shadow:0 0 10px rgba(245,158,11,.5);
}
.flash-progress-msg{font-size:.7rem;color:var(--text-dim);font-family:'JetBrains Mono',monospace;}
.radio-wave.playing{opacity:1;}
.radio-wave span{
  display:block;width:2px;background:var(--accent);border-radius:2px;
  animation:wave-bar 1s ease-in-out infinite;
}
.radio-wave span:nth-child(1){height:4px;animation-delay:0s;}
.radio-wave span:nth-child(2){height:10px;animation-delay:.15s;}
.radio-wave span:nth-child(3){height:7px;animation-delay:.3s;}
.radio-wave span:nth-child(4){height:12px;animation-delay:.1s;}
.radio-wave span:nth-child(5){height:5px;animation-delay:.25s;}
@keyframes wave-bar{0%,100%{transform:scaleY(1);}50%{transform:scaleY(1.8);}}

/* ── THEME SWITCHER ── */
.theme-btn{
  background:none;border:1px solid var(--border);border-radius:50%;
  width:28px;height:28px;cursor:pointer;font-size:.85rem;
  display:flex;align-items:center;justify-content:center;
  color:var(--text-dim);transition:all .2s;flex-shrink:0;
}
.theme-btn:hover{border-color:var(--accent);color:var(--accent);}
.page{max-width:1240px;margin:0 auto;padding:2rem 1.5rem;}
.hero{padding:3rem 0 2rem;border-bottom:1px solid var(--border);margin-bottom:2.5rem;}
.hero-date{font-family:'JetBrains Mono',monospace;font-size:.75rem;color:var(--accent);letter-spacing:.2em;text-transform:uppercase;margin-bottom:.75rem;}
.hero-title{font-family:'Playfair Display',serif;font-size:clamp(2rem,5vw,3.5rem);font-weight:700;line-height:1.15;color:var(--text);margin-bottom:1rem;}
.hero-meta{color:var(--text-muted);font-size:.85rem;display:flex;gap:1.5rem;flex-wrap:wrap;}
.hero-meta span{display:flex;align-items:center;gap:.3rem;}
.content-grid{display:grid;grid-template-columns:1fr 340px;gap:2.5rem;align-items:start;min-width:0;}
.content-grid>*{min-width:0;}
@media(max-width:900px){.content-grid{grid-template-columns:1fr;}.sidebar{order:-1;}}
.report-body{font-family:'Source Sans 3',sans-serif;font-weight:300;font-size:1.05rem;line-height:1.8;color:var(--text);}
.report-body h2{font-family:'Playfair Display',serif;font-size:1.4rem;font-weight:700;color:var(--text);margin:2.5rem 0 1rem;padding-bottom:.5rem;border-bottom:2px solid var(--accent);}
.report-body p{margin-bottom:1.2rem;text-align:justify;overflow-wrap:break-word;word-break:break-word;}
.report-body hr{border:none;border-top:1px solid var(--border);margin:2rem 0;}
.report-body em{color:var(--text-muted);font-size:.85rem;}
.sidebar{position:sticky;top:104px;}
.sidebar-block{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:1.25rem;margin-bottom:1.5rem;}
.sidebar-block h3{font-family:'JetBrains Mono',monospace;font-size:.7rem;color:var(--accent);letter-spacing:.15em;text-transform:uppercase;margin-bottom:1rem;padding-bottom:.5rem;border-bottom:1px solid var(--border);}
.cat-pills{display:flex;flex-wrap:wrap;gap:.4rem;}
.cat-pill{display:inline-flex;align-items:center;gap:.3rem;background:var(--bg3);border:1px solid var(--border);border-radius:20px;padding:.25rem .7rem;font-size:.78rem;color:var(--text-dim);text-decoration:none;transition:all .2s;}
.cat-count{background:var(--bg);border-radius:10px;padding:0 .35rem;font-size:.65rem;color:var(--text-muted);}
.article-item{padding:.75rem 0;border-bottom:1px solid var(--border);}
.article-item:last-child{border-bottom:none;}
.article-item a{color:var(--text);text-decoration:none;font-size:.88rem;line-height:1.4;display:block;transition:color .2s;}
.article-item a:hover{color:var(--accent);}
.article-source{font-family:'JetBrains Mono',monospace;font-size:.65rem;color:var(--text-muted);margin-top:.2rem;text-transform:uppercase;letter-spacing:.05em;}
.stat-row{display:flex;justify-content:space-between;align-items:center;padding:.4rem 0;font-size:.85rem;}
.stat-label{color:var(--text-dim);}
.stat-value{font-family:'JetBrains Mono',monospace;color:var(--accent);font-weight:500;}
.archive-list{list-style:none;}
.archive-list li{padding:.4rem 0;border-bottom:1px solid var(--border);}
.archive-list li:last-child{border-bottom:none;}
.archive-list a{color:var(--text-dim);text-decoration:none;font-size:.85rem;display:flex;justify-content:space-between;align-items:center;transition:color .2s;}
.archive-list a:hover{color:var(--text);}
.archive-date{font-family:'JetBrains Mono',monospace;font-size:.72rem;}
.archive-badge{background:var(--bg3);border-radius:10px;padding:.1rem .5rem;font-size:.65rem;color:var(--text-muted);font-family:'JetBrains Mono',monospace;}
.cards-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1.5rem;margin-top:2rem;}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:1.5rem;text-decoration:none;display:block;transition:all .2s;position:relative;overflow:hidden;}
.card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:var(--shadow);}
.card-date{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--accent);letter-spacing:.15em;margin-bottom:.5rem;}
.card-title{font-family:'Playfair Display',serif;font-size:1.15rem;font-weight:700;color:var(--text);line-height:1.3;margin-bottom:.75rem;}
.card-excerpt{font-size:.85rem;color:var(--text-dim);line-height:1.6;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;}
.card-meta{margin-top:1rem;padding-top:.75rem;border-top:1px solid var(--border);display:flex;gap:.75rem;font-size:.75rem;color:var(--text-muted);}
.live-dot{display:inline-block;width:7px;height:7px;background:#22c55e;border-radius:50%;margin-right:.4rem;animation:pulse 1.5s infinite;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.3;}}
footer{margin-top:4rem;padding:2rem;border-top:1px solid var(--border);text-align:center;color:var(--text-muted);font-size:.8rem;font-family:'JetBrains Mono',monospace;letter-spacing:.05em;}
footer strong{color:var(--accent);}
.sources-section{margin-top:3rem;border-top:1px solid var(--border);padding-top:1.5rem;}
.sources-toggle{display:flex;align-items:center;gap:.75rem;cursor:pointer;background:none;border:none;color:var(--text-dim);font-family:'JetBrains Mono',monospace;font-size:.8rem;letter-spacing:.1em;text-transform:uppercase;padding:.5rem 0;transition:color .2s;width:100%;text-align:left;}
.sources-toggle:hover{color:var(--accent);}
.sources-toggle .toggle-icon{transition:transform .25s;font-style:normal;display:inline-block;}
.sources-toggle.open .toggle-icon{transform:rotate(90deg);}
.sources-count{background:var(--bg3);border:1px solid var(--border);border-radius:12px;padding:.1rem .5rem;font-size:.7rem;color:var(--text-muted);}
.sources-list{display:none;margin-top:1rem;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;}
.sources-list.open{display:block;}
.source-row{display:grid;grid-template-columns:120px 1fr;gap:1rem;align-items:center;padding:.65rem 1rem;border-bottom:1px solid var(--border);transition:background .15s;}
.source-row:last-child{border-bottom:none;}
.source-row:hover{background:var(--bg2);}
.source-domain{font-family:'JetBrains Mono',monospace;font-size:.68rem;color:var(--accent);text-transform:uppercase;letter-spacing:.05em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.source-title{font-size:.85rem;color:var(--text-dim);text-decoration:none;line-height:1.4;transition:color .2s;}
.source-title:hover{color:var(--text);}
.breaking-banner{width:100%;background:var(--bg2);border-top:1px solid var(--border);border-bottom:2px solid rgba(var(--accent-rgb,.35));display:flex;align-items:center;height:56px;overflow:hidden;position:sticky;top:56px;z-index:95;backdrop-filter:blur(8px);}
.bb-label{flex-shrink:0;display:flex;align-items:center;gap:.5rem;background:var(--accent);color:white;font-family:'JetBrains Mono',monospace;font-size:.75rem;font-weight:700;letter-spacing:.15em;padding:0 1.4rem;height:100%;white-space:nowrap;text-transform:uppercase;}
.bb-track-wrap{flex:1;overflow:hidden;height:100%;display:flex;align-items:center;mask-image:linear-gradient(to right,transparent 0%,black 2%,black 98%,transparent 100%);}
.bb-track{display:flex;align-items:center;white-space:nowrap;animation:bb-scroll 70s linear infinite;padding-left:2rem;}
.bb-track:hover{animation-play-state:paused;}
@keyframes bb-scroll{0%{transform:translateX(0);}100%{transform:translateX(-50%);}}
.bb-item{display:inline-flex;align-items:center;color:var(--text);text-decoration:none;font-size:.92rem;padding:0 1.4rem;transition:color .15s;white-space:nowrap;}
.bb-item:hover{color:var(--accent);text-decoration:underline;}
.bb-sep{color:var(--accent);opacity:.45;font-size:1rem;}
.edition-hero{padding:2.5rem 0 1.5rem;border-bottom:1px solid var(--border);margin-bottom:2rem;}
.edition-badge{display:inline-flex;align-items:center;gap:.5rem;font-family:'JetBrains Mono',monospace;font-size:.75rem;letter-spacing:.15em;text-transform:uppercase;color:var(--accent);margin-bottom:.75rem;}
.edition-title{font-family:'Playfair Display',serif;font-size:clamp(1.6rem,4vw,2.8rem);font-weight:700;line-height:1.2;color:var(--text);margin-bottom:1rem;}
.edition-nav{display:flex;gap:.75rem;flex-wrap:wrap;margin-top:1.5rem;}
.edition-nav a{display:inline-flex;align-items:center;gap:.3rem;padding:.35rem .9rem;border-radius:20px;font-size:.82rem;text-decoration:none;border:1px solid var(--border);color:var(--text-dim);transition:all .2s;}
.edition-nav a.active,.edition-nav a:hover{border-color:var(--accent);color:var(--text);}
.edition-body{font-family:'Source Sans 3',sans-serif;font-weight:300;font-size:1.05rem;line-height:1.85;color:var(--text);max-width:760px;margin:0 auto;}
.edition-body p{margin-bottom:1.4rem;text-align:justify;}
.edition-body hr{border:none;border-top:1px solid var(--border);margin:2rem 0;}
.edition-body em{color:var(--text-muted);font-size:.85rem;}


"""

LIVE_CSS_EXTRA = """
.live-header{padding:2.5rem 0 1.5rem;border-bottom:1px solid var(--border);margin-bottom:0;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:1rem;}
.live-title{font-family:'Playfair Display',serif;font-size:clamp(1.8rem,4vw,2.8rem);font-weight:700;color:var(--text);}
.live-status{display:flex;align-items:center;gap:.5rem;font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--text-muted);letter-spacing:.1em;}
.live-updated{font-family:'JetBrains Mono',monospace;font-size:.7rem;color:var(--text-muted);margin-top:.3rem;}
.filter-bar{display:flex;flex-wrap:wrap;gap:.5rem;padding:1rem 0;border-bottom:1px solid var(--border);margin-bottom:1.5rem;position:sticky;top:96px;z-index:50;background:var(--topbar-bg);backdrop-filter:blur(8px);}
.filter-btn{display:inline-flex;align-items:center;gap:.3rem;background:var(--bg2);border:1px solid var(--border);border-radius:20px;padding:.3rem .8rem;font-size:.78rem;color:var(--text-dim);cursor:pointer;transition:all .15s;font-family:'Source Sans 3',sans-serif;user-select:none;}
.filter-btn:hover{border-color:var(--accent);color:var(--text);}
.filter-btn.active{background:var(--accent);border-color:var(--accent);color:white;font-weight:600;}
.filter-count{background:rgba(0,0,0,.25);border-radius:10px;padding:0 .35rem;font-size:.65rem;}

/* ── Sections catégories ── */
.live-cat-section{border-bottom:1px solid var(--border);padding:1.25rem 0;}
.live-cat-section.hidden{display:none;}
.live-cat-header{display:flex;align-items:center;gap:.5rem;margin-bottom:.75rem;}
.live-cat-label{font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:500;color:var(--accent);letter-spacing:.15em;text-transform:uppercase;}
.live-cat-count{font-family:'JetBrains Mono',monospace;font-size:.62rem;color:var(--text-muted);background:var(--bg3);border-radius:10px;padding:.05rem .4rem;}

/* ── Variables couleur par catégorie ── */
:root {
  --cat-color-geopolitique: #e8612a;
  --cat-color-economie:     #3b82f6;
  --cat-color-crypto:       #f59e0b;
  --cat-color-tech:         #8b5cf6;
  --cat-color-france:       #ef4444;
  --cat-color-monde:        #22c55e;
  --cat-color-science:      #06b6d4;
  --cat-color-environnement:#84cc16;
  --cat-color-societe:      #f97316;
  --cat-color-culture:      #ec4899;
  --cat-color-sport:        #14b8a6;
}

/* ── Item du fil chrono ── */
.live-item{padding:.7rem 0;border-bottom:1px solid rgba(42,42,58,.5);transition:background .15s;}
.live-item:last-child{border-bottom:none;}
.live-item:hover{background:rgba(255,255,255,.025);}

/* ── Ligne titre : bande couleur inline + icône + lien ── */
.live-item-row{
  display:flex;align-items:flex-start;gap:.6rem;
  padding-left:.1rem;
  border-radius:0 4px 4px 0;
}
.live-cat-icon{font-size:1rem;line-height:1.4;flex-shrink:0;margin-top:.05rem;}
.live-item-title{color:var(--text);text-decoration:none;font-size:.92rem;line-height:1.4;transition:color .2s;}
.live-item-title:hover{color:var(--accent);}
.live-item-meta{display:flex;gap:.75rem;align-items:center;font-size:.7rem;color:var(--text-muted);font-family:'JetBrains Mono',monospace;margin-top:.25rem;padding-left:1.55rem;flex-wrap:wrap;}
.live-source-tag{color:var(--accent);font-weight:500;}
.live-cat-badge{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:.05rem .45rem;font-size:.62rem;color:var(--text-muted);}

/* ── Résumé extensible ── */
.live-summary-wrap{margin-top:.35rem;padding-left:1.55rem;}
.live-summary-text{
  font-size:.83rem;color:var(--text-dim);line-height:1.55;
  overflow:hidden;
  max-height:2.6em;           /* ~2 lignes */
  transition:max-height .3s ease;
}
.live-summary-text.expanded{max-height:40em;}
.live-summary-toggle{
  display:inline-flex;align-items:center;gap:.25rem;
  margin-top:.2rem;
  font-size:.72rem;font-family:'JetBrains Mono',monospace;
  color:var(--accent);cursor:pointer;
  background:none;border:none;padding:0;
  transition:opacity .15s;
}
.live-summary-toggle:hover{opacity:.75;}
.live-summary-toggle .tgl-icon{transition:transform .25s;display:inline-block;}
.live-summary-toggle.open .tgl-icon{transform:rotate(90deg);}
"""


# ─── SCRIPT GLOBAL (player radio + theme + live refresh) ─────────────────────
# Injecté dans chaque template juste avant </body>

GLOBAL_SCRIPT = '<script src="/assets/csrf.js"></script><script defer src="/assets/media.js"></script><script defer src="/assets/studio.js"></script><script defer src="/assets/feeds.js"></script><script defer src="/assets/web.js"></script><script defer src="/assets/flash.js"></script>'

REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{ title }} — {{ brand_name }}</title>
<style>{{ css }}</style></head><body data-radio-label="{{ brand_name }}">
<nav class="topbar">
  <a href="/" class="topbar-logo">{{ logo_main }}<span>{{ logo_sub }}</span></a>
  <!-- Player radio + Theme switcher -->
  <div class="radio-player" id="radio-player">
    <button class="radio-btn" id="radio-btn" aria-label="Écouter la radio"
            data-action="radio-toggle" data-stream="{{ icecast_url }}">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  {{ flash_button_html }}
  <button class="theme-btn" data-action="theme" title="Changer le thème">🌙</button>
  <div class="topbar-nav">
    <a href="/">{{ ui.nav_home }}</a>
    <a href="/live" style="color:var(--accent)"><span class="live-dot"></span>{{ ui.nav_live }}</a>
    <a href="/{{ today_file }}">{{ ui.nav_chronicle }}</a>
    <a href="/archives">{{ ui.nav_archives }}</a>
    <a href="/config/feeds/page">Sources</a><a href="/config">⚙</a>
  </div>
</nav>
{{ breaking_banner }}
<div class="page">
  <div class="hero">
    <div class="hero-date">{{ day_label }}</div>
    <h1 class="hero-title">Chronique du {{ date_fr }}</h1>
    <div class="hero-meta">
      <span>📰 {{ article_count }} articles analysés</span>
      <span>🏛️ {{ source_count }} sources</span>
      <span>⏱️ Généré à {{ gen_time }}</span>
    </div>
  </div>
  <div class="content-grid">
    <main>
      <div class="report-body">{{ report_html }}</div>
    </main>
    <aside class="sidebar">
      <div class="sidebar-block">
        <h3>Catégories</h3>
        <div class="cat-pills">
          {% for cat, count in categories %}
          <span class="cat-pill">{{ cat_icons.get(cat,'') }} {{ cat_labels.get(cat,cat) }}
            <span class="cat-count">{{ count }}</span></span>
          {% endfor %}
        </div>
      </div>
      <div class="sidebar-block">
        <h3>Statistiques</h3>
        {% for label, value in stats %}
        <div class="stat-row">
          <span class="stat-label">{{ label }}</span>
          <span class="stat-value">{{ value }}</span>
        </div>
        {% endfor %}
      </div>
      <div class="sidebar-block">
        <h3>Articles récents</h3>
        <div style="max-height:380px;overflow-y:auto">
        {% for a in recent_articles %}
        <div class="article-item" data-category="{{ a.category }}">
          <a href="{{ a.link|safe_url }}" target="_blank" rel="noopener">
            {{ a.title[:80] }}{% if a.title|length > 80 %}…{% endif %}
          </a>
          <div class="article-source">{{ a.source }} · {{ a.category }}</div>
        </div>
        {% endfor %}
        </div>
      </div>
      {% if archives %}
      <div class="sidebar-block">
        <h3>Archives récentes</h3>
        <ul class="archive-list">
          {% for arch in archives %}
          <li><a href="/{{ arch.file }}">
            <span class="archive-date">{{ arch.date_fr }}</span>
            <span class="archive-badge">{{ arch.count }}</span>
          </a></li>
          {% endfor %}
        </ul>
      </div>
      {% endif %}
    </aside>
  </div>
  {% if all_articles %}
  <div class="sources-section">
    <button class="sources-toggle" data-action="sources" aria-expanded="false">
      <i class="toggle-icon">▶</i> Sources consultées
      <span class="sources-count">{{ all_articles|length }} liens</span>
    </button>
    <div class="sources-list" id="sources-list">
      {% for a in all_articles %}{% if a.link %}
      <div class="source-row">
        <div>
          <div class="source-domain">{{ a.source }}</div>
          <div class="source-cat" style="font-size:.65rem;color:var(--text-muted);font-family:'JetBrains Mono',monospace;">{{ cat_labels.get(a.category,a.category) }}</div>
        </div>
        <a href="{{ a.link|safe_url }}" target="_blank" rel="noopener" class="source-title">{{ a.title }}</a>
      </div>
      {% endif %}{% endfor %}
    </div>
  </div>
  {% endif %}
</div>
<footer><strong>{{ brand_name }}</strong> — {{ brand_tagline }}<br>Modèle : {{ model }} · © {{ year }}</footer>

{{ flash_button_js }}
{{ global_script }}
</body></html>"""

INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{ brand_name }} — Archives</title>
<style>{{ css }}</style></head><body data-radio-label="{{ brand_name }}">
<nav class="topbar">
  <a href="/" class="topbar-logo">{{ logo_main }}<span>{{ logo_sub }}</span></a>
  <!-- Player radio + Theme switcher -->
  <div class="radio-player" id="radio-player">
    <button class="radio-btn" id="radio-btn" aria-label="Écouter la radio"
            data-action="radio-toggle" data-stream="{{ icecast_url }}">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  {{ flash_button_html }}
  <button class="theme-btn" data-action="theme" title="Changer le thème">🌙</button>
  <div class="topbar-nav">
    <a href="/">{{ ui.nav_home }}</a>
    <a href="/live" style="color:var(--accent)"><span class="live-dot"></span>{{ ui.nav_live }}</a>
    {% if today_file %}<a href="/{{ today_file }}">{{ ui.nav_chronicle }}</a>{% endif %}
    <a href="/archives">{{ ui.nav_archives }}</a>
    <a href="/config/feeds/page">Sources</a><a href="/config">⚙</a>
  </div>
</nav>
{{ breaking_banner }}
<div class="page">
  <div class="hero">
    <div class="hero-date">Archives</div>
    <h1 class="hero-title">{{ ui.archives_title }}</h1>
    <div class="hero-meta">
      <span>📅 {{ total_days }} journées archivées</span>
      <span>📰 {{ total_articles }} articles traités</span>
    </div>
  </div>
  <div class="cards-grid">
    {% for day in days %}
    <a href="/{{ day.file }}" class="card">
      <div class="card-date">{{ day.day_label }}</div>
      <div class="card-title">{{ day.date_fr }}</div>
      <div class="card-excerpt">{{ day.excerpt }}</div>
      <div class="card-meta">
        <span>📰 {{ day.article_count }} articles</span>
        <span>🏛️ {{ day.source_count }} sources</span>
      </div>
    </a>
    {% endfor %}
  </div>
</div>
<footer><strong>{{ brand_name }}</strong> — {{ brand_tagline }}<br>© {{ year }}</footer>
{{ flash_button_js }}
{{ global_script }}
</body></html>"""

EDITION_TEMPLATE = """<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{ edition_label }} — {{ brand_name }}</title>
<style>{{ css }}</style></head><body data-radio-label="{{ brand_name }}">
<nav class="topbar">
  <a href="/" class="topbar-logo">{{ logo_main }}<span>{{ logo_sub }}</span></a>
  <!-- Player radio + Theme switcher -->
  <div class="radio-player" id="radio-player">
    <button class="radio-btn" id="radio-btn" aria-label="Écouter la radio"
            data-action="radio-toggle" data-stream="{{ icecast_url }}">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  {{ flash_button_html }}
  <button class="theme-btn" data-action="theme" title="Changer le thème">🌙</button>
  <div class="topbar-nav">
    <a href="/">{{ ui.nav_home }}</a>
    <a href="/live" style="color:var(--accent)"><span class="live-dot"></span>{{ ui.nav_live }}</a>
    <a href="/{{ today_report }}">{{ ui.nav_chronicle }}</a>
    <a href="/archives">{{ ui.nav_archives }}</a>
    <a href="/config/feeds/page">Sources</a><a href="/config">⚙</a>
  </div>
</nav>
{{ breaking_banner }}
<div class="page">
  <div class="edition-hero">
    <div class="edition-badge">{{ edition_emoji }} {{ edition_label }}</div>
    <h1 class="edition-title">{{ article_title }}</h1>
    <div class="hero-meta">
      <span>📅 {{ date_fr }}</span>
      <span>📰 {{ article_count }} articles analysés</span>
      <span>⏱️ {{ gen_time }}</span>
    </div>
    <div class="edition-nav">
      {% for ed_name, ed_cfg in editions_of_day %}
      <a href="/editions/{{ day }}_{{ ed_name }}.html"
         class="{{ 'active' if ed_name == current_edition else '' }}">
        {{ ed_cfg.emoji }} {{ ed_cfg.label }}
      </a>
      {% endfor %}
      <a href="/{{ today_report }}">📖 Chronique</a>
    </div>
  </div>
  <div class="edition-body">{{ body_html }}</div>
</div>
<footer><strong>{{ brand_name }}</strong> — {{ edition_label }} · {{ date_fr }}<br>© {{ year }}</footer>
{{ flash_button_js }}
{{ global_script }}
</body></html>"""

# ─────────────────────────────────────────────────────────────────────────────
#  Flash button : bouton dans la topbar pour générer un bulletin à la demande
# ─────────────────────────────────────────────────────────────────────────────

FLASH_BUTTON_HTML = """
<div class="flash-wrap" id="flash-wrap">
  <button class="flash-btn" id="flash-btn" data-action="flash-toggle" title="Générer un flash spécialisé par catégorie">
    <span class="flash-icon">⚡</span>
    <span>Flash</span>
  </button>
  <div class="flash-dropdown" id="flash-dropdown">
    {% for cat in cats %}
    <button class="flash-cat" data-cat="{{ cat }}" data-action="flash-start">
      <span class="flash-cat-icon">{{ cat_icons.get(cat,'') }}</span>
      <span class="flash-cat-label">{{ cat_labels.get(cat, cat) }}</span>
      <span class="flash-cat-count" id="flash-count-{{ cat }}"></span>
    </button>
    {% endfor %}
  </div>
  <div class="flash-progress" id="flash-progress">
    <div class="flash-progress-title">
      <span class="flash-icon">⚡</span>
      <span id="flash-progress-label">Flash en cours...</span>
    </div>
    <div class="flash-progress-bar">
      <div class="flash-progress-fill" id="flash-progress-fill"></div>
    </div>
    <div class="flash-progress-msg" id="flash-progress-msg">Initialisation...</div>
  </div>
</div>
"""

FLASH_BUTTON_JS = """

"""


def render_flash_button_html(cat_icons: dict, cat_labels: dict) -> str:
    """Retourne le HTML du bouton Flash avec dropdown rempli."""
    from modules.web.web_security import html_template as Template
    return Markup(Template(FLASH_BUTTON_HTML).render(
        cats=list(cat_icons.keys()),
        cat_icons=cat_icons,
        cat_labels=cat_labels,
    ))

LIVE_TEMPLATE = """<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{ brand_name }} — Fil en direct</title>
<style>{{ css }}</style></head><body data-page="live" data-radio-label="{{ brand_name }}">
<nav class="topbar">
  <a href="/" class="topbar-logo">{{ logo_main }}<span>{{ logo_sub }}</span></a>
  <!-- Player radio + Theme switcher -->
  <div class="radio-player" id="radio-player">
    <button class="radio-btn" id="radio-btn" aria-label="Écouter la radio"
            data-action="radio-toggle" data-stream="{{ icecast_url }}">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  {{ flash_button_html }}
  <button class="theme-btn" data-action="theme" title="Changer le thème">🌙</button>
  <div class="topbar-nav">
    <a href="/">{{ ui.nav_home }}</a>
    <a href="/live" style="color:var(--accent)"><span class="live-dot"></span>{{ ui.nav_live }}</a>
    {% if today_file %}<a href="/{{ today_file }}">{{ ui.nav_chronicle }}</a>{% endif %}
    <a href="/archives">{{ ui.nav_archives }}</a>
    <a href="/config/feeds/page">Sources</a><a href="/config">⚙</a>
  </div>
</nav>
{{ breaking_banner }}
<div class="page">
  <div class="live-header">
    <div>
      <h1 class="live-title">{{ ui.live_title }}</h1>
      <div class="live-updated">
        <span id="live-updated">{{ ui.live_updated }} {{ updated }} · {{ total }} articles · {{ window_hours }}h d'historique</span>
      </div>
    </div>
    <div class="live-status"><span class="live-dot"></span> {{ ui.live_status }}</div>
  </div>

  <!-- Filtres par catégorie — multi-sélection cumulative, client-side -->
  <!-- Click sur "Tout" clear la sélection. Click sur une cat toggle. -->
  <div class="filter-bar" id="filter-bar">
    <button class="filter-btn active" data-cat="__all" aria-pressed="true">
      {{ ui.filter_all }} <span class="filter-count">{{ total }}</span>
    </button>
    {% for cat, count in cat_counts %}
    <button class="filter-btn" data-cat="{{ cat }}" aria-pressed="false">
      {{ cat_icons.get(cat,'') }} {{ cat_labels.get(cat,cat) }}
      <span class="filter-count">{{ count }}</span>
    </button>
    {% endfor %}
  </div>

  <!-- Fil chronologique unique — chaque item porte data-cat pour le filtre JS -->
  <section id="editorial-events">
{% if editorial_events %}<details><summary>Couvertures rapprochées de mêmes événements</summary>
<p>Associations éditoriales proposées. Confirmation factuelle indépendante non établie.</p>
{% for event in editorial_events %}<article class="live-card"><h3>{{ event.title }}</h3>
<p>{{ event.article_count }} articles · {{ event.domain_count }} domaines · {{ event.status }} · Index événement actualisé : {{ event.last_updated_at }}</p>
{% for coverage in event.articles %}<p><a href="{{ coverage.link|safe_url }}" target="_blank" rel="noopener noreferrer">{{ coverage.source or coverage.domain or 'Source inconnue' }} — {{ coverage.title }}</a> · {{ coverage.category }} · {{ coverage.published_raw or 'publication inconnue' }} ({{ coverage.publication_status }})</p>{% endfor %}
</article>{% endfor %}</details>{% endif %}
</section>
<div class="live-grid" id="live-grid">
    {% for a in articles %}
    {% set cat = a.get('category', 'monde') %}
    <div class="live-item" data-category="{{ cat }}" data-cat="{{ cat }}">

      <!-- Bande couleur + icône + titre -->
      <div class="live-item-row" style="border-left:3px solid var(--cat-color-{{ cat }},var(--accent))">
        <span class="live-cat-icon">{{ cat_icons.get(cat,'🌐') }}</span>
        <a href="{{ a.link|safe_url }}" target="_blank" rel="noopener" class="live-item-title">{{ a.title }}</a>
      </div>

      <!-- Source · heure -->
      <div class="live-item-meta">
        <span class="live-source-tag">{{ a.source }}</span>
        <span>{{ a.timestamp[11:16] }}</span>
        <span class="live-cat-badge">{{ cat_labels.get(cat, cat) }}</span>
      </div>

      <!-- Résumé extensible -->
      {% if a.summary %}
      <div class="live-summary-wrap">
        <div class="live-summary-text" id="sum-{{ loop.index0 }}">{{ a.summary }}</div>
        <button class="live-summary-toggle"
                data-action="summary" data-summary="sum-{{ loop.index0 }}" data-more="{{ ui.read_more }}" data-less="{{ ui.reduce }}"
                aria-expanded="false">
          <span class="tgl-icon">▶</span> <span class="tgl-label">{{ ui.read_more }}</span>
        </button>
      </div>
      {% endif %}

    </div>
    {% endfor %}
  </div>
</div>
<footer><strong>{{ brand_name }}</strong> — Fil en direct · © {{ year }}</footer>

{{ flash_button_js }}
{{ global_script }}
</body></html>"""

CONFIG_TEMPLATE = """<!DOCTYPE html>
<html lang="fr"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{{ brand_name }} — Configuration</title>
<style>{{ css }}
.config-page{max-width:720px;margin:0 auto;padding:2rem 1.5rem;}
.config-hero{padding:2rem 0 1.5rem;border-bottom:1px solid var(--border);margin-bottom:2rem;}
.config-section{background:var(--bg2);border:1px solid var(--border);border-radius:var(--radius);padding:1.5rem;margin-bottom:1.5rem;}
.config-section h2{font-family:'JetBrains Mono',monospace;font-size:.75rem;color:var(--accent);letter-spacing:.15em;text-transform:uppercase;margin-bottom:1.25rem;padding-bottom:.5rem;border-bottom:1px solid var(--border);}
.config-row{display:flex;align-items:flex-start;justify-content:space-between;gap:1.5rem;padding:.75rem 0;border-bottom:1px solid var(--border);}
.config-row:last-child{border-bottom:none;}
.config-label{font-size:.9rem;color:var(--text);font-weight:400;}
.config-desc{font-size:.75rem;color:var(--text-muted);margin-top:.2rem;}
.config-control{flex-shrink:0;}
select,input[type=number]{background:var(--bg3);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-family:'JetBrains Mono',monospace;font-size:.82rem;padding:.35rem .7rem;min-width:200px;transition:border-color .2s;}
select:focus,input[type=number]:focus{outline:none;border-color:var(--accent);}
.btn-save{background:var(--accent);border:none;border-radius:var(--radius);color:white;font-size:.95rem;font-weight:600;padding:.75rem 2rem;cursor:pointer;transition:background .2s;width:100%;margin-top:1rem;}
.btn-save:hover{background:var(--accent2);}
.save-msg{display:none;text-align:center;padding:.75rem;background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);border-radius:var(--radius);color:#22c55e;font-family:'JetBrains Mono',monospace;font-size:.8rem;margin-top:1rem;}
.current-tag{display:inline-flex;align-items:center;background:rgba(232,97,42,.15);border:1px solid rgba(232,97,42,.3);border-radius:10px;padding:.1rem .5rem;font-size:.65rem;color:var(--accent);font-family:'JetBrains Mono',monospace;margin-left:.5rem;}
</style></head><body data-radio-label="{{ brand_name }}">
<nav class="topbar">
  <a href="/" class="topbar-logo">{{ logo_main }}<span>{{ logo_sub }}</span></a>
  <!-- Player radio + Theme switcher -->
  <div class="radio-player" id="radio-player">
    <button class="radio-btn" id="radio-btn" aria-label="Écouter la radio"
            data-action="radio-toggle" data-stream="{{ icecast_url }}">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  {{ flash_button_html }}
  <button class="theme-btn" data-action="theme" title="Changer le thème">🌙</button>
  <div class="topbar-nav">
    <a href="/">{{ ui.nav_home }}</a>
    <a href="/live" style="color:var(--accent)"><span class="live-dot"></span>{{ ui.nav_live }}</a>
    <a href="/{{ today_file }}">{{ ui.nav_chronicle }}</a>
    <a href="/archives">{{ ui.nav_archives }}</a>
    <a href="/config" style="color:var(--text)">⚙</a>
  </div>
</nav>
<div class="config-page">
  <div class="config-hero">
    <div class="hero-date">Paramètres</div>
    <h1 class="hero-title" style="font-family:'Playfair Display',serif;font-size:2rem;">Configuration {{ brand_name }}</h1>
  </div>
  <form id="configForm">
    <div class="config-section">
      <h2>Modèle Ollama</h2>
      <div class="config-row">
        <div>
          <div class="config-label">Modèle actif <span class="current-tag">{{ cfg.ollama_model }}</span></div>
          <div class="config-desc">Installé via <code>ollama pull &lt;modèle&gt;</code></div>
        </div>
        <div class="config-control">
          <select name="ollama_model">
            {% for m in models %}
            <option value="{{ m.id }}" {{ 'selected' if m.id == cfg.ollama_model else '' }}>{{ m.label }}</option>
            {% endfor %}
          </select>
        </div>
      </div>
      <div class="config-row">
        <div>
          <div class="config-label">Timeout résumé article</div>
          <div class="config-desc">Secondes max pour résumer un article</div>
        </div>
        <div class="config-control">
          <input type="number" name="fetch_timeout" value="{{ cfg.fetch_timeout }}" min="60" max="600" step="30">
        </div>
      </div>
      <div class="config-row">
        <div>
          <div class="config-label">Timeout édition</div>
          <div class="config-desc">Secondes max pour générer une édition</div>
        </div>
        <div class="config-control">
          <input type="number" name="edition_timeout" value="{{ cfg.edition_timeout }}" min="120" max="1800" step="60">
        </div>
      </div>
    </div>
    <div class="config-section">
      <h2>Collecte RSS</h2>
      <div class="config-row">
        <div>
          <div class="config-label">Articles max par flux RSS</div>
          <div class="config-desc">Par source à chaque cycle</div>
        </div>
        <div class="config-control">
          <input type="number" name="max_articles_feed" value="{{ cfg.max_articles_feed }}" min="3" max="20">
        </div>
      </div>
    </div>
    <div class="config-section">
      <h2>Fil en direct</h2>
      <div class="config-row">
        <div>
          <div class="config-label">Fenêtre d'affichage</div>
          <div class="config-desc">Heures d'historique dans le fil en direct</div>
        </div>
        <div class="config-control">
          <input type="number" name="live_window_hours" value="{{ cfg.live_window_hours }}" min="6" max="48">
        </div>
      </div>
    </div>
    <button type="submit" class="btn-save">Enregistrer la configuration</button>
    <div class="save-msg" id="saveMsg">✓ Configuration enregistrée — redémarre Atlas pour appliquer</div>
  </form>
</div>
<footer><strong>{{ brand_name }}</strong> — Configuration · © {{ year }}</footer>

{{ flash_button_js }}
{{ global_script }}
</body></html>"""


CONFIG_YAML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ brand_name }} — Configuration</title>


<style>
/* ── Reset & base ── */
*{box-sizing:border-box;margin:0;padding:0;}
:root{
  --bg:     #0d0d14;
  --bg2:    #13131e;
  --bg3:    #1c1c2e;
  --border: #2a2a40;
  --accent: #e8612a;
  --accent2:#c44d1e;
  --text:   #e2e2e8;
  --text-dim:#8888a0;
  --text-muted:#555568;
  --green:  #22c55e;
  --radius: 10px;
  --shadow: 0 4px 32px rgba(0,0,0,.6);
  --sidebar-w: 240px;
}
html,body{min-height:100vh;background:var(--bg);color:var(--text);}
body{font-family:'Inter',sans-serif;font-weight:400;line-height:1.6;display:flex;flex-direction:column;}

/* ── Topbar ── */
.topbar{
  position:sticky;top:0;z-index:100;
  height:56px;
  background:rgba(13,13,20,.96);
  backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;
  padding:0 1.5rem;gap:1rem;
}
.topbar-logo{
  font-family:'JetBrains Mono',monospace;
  font-size:1rem;font-weight:500;
  color:var(--accent);letter-spacing:.12em;
  text-decoration:none;flex-shrink:0;
}
.topbar-logo span{color:var(--text-muted);}
.topbar-logo-btn{
  background:none;border:none;cursor:pointer;padding:0;flex-shrink:0;
}
.topbar-right{display:flex;align-items:center;gap:.75rem;}
.topbar-back{
  display:inline-flex;align-items:center;gap:.4rem;
  background:var(--bg3);border:1px solid var(--border);
  border-radius:8px;padding:.3rem .8rem;
  color:var(--text-dim);text-decoration:none;font-size:.82rem;
  transition:all .2s;
}
.topbar-back:hover{border-color:var(--accent);color:var(--text);}

/* ── Layout principal : sidebar + contenu ── */
.cfg-layout{display:flex;flex:1;min-height:calc(100vh - 56px);}

/* ── Sidebar ── */
.cfg-sidebar{
  width:var(--sidebar-w);flex-shrink:0;
  background:var(--bg2);
  border-right:1px solid var(--border);
  padding:1.5rem 0;
  position:sticky;top:56px;height:calc(100vh - 56px);
  overflow-y:auto;
}
.cfg-sidebar-title{
  font-family:'JetBrains Mono',monospace;
  font-size:.65rem;color:var(--text-muted);
  letter-spacing:.15em;text-transform:uppercase;
  padding:.25rem 1.25rem .75rem;
}
.cfg-nav-btn{
  display:flex;align-items:center;gap:.6rem;
  padding:.6rem 1.25rem;width:100%;
  font-size:.88rem;color:var(--text-dim);
  background:none;border:none;border-left:2px solid transparent;
  text-align:left;cursor:pointer;
  transition:all .15s;font-family:'Inter',sans-serif;
}
.cfg-nav-btn:hover{color:var(--text);background:var(--bg3);}
.cfg-nav-btn.active{
  color:var(--accent);
  border-left-color:var(--accent);
  background:rgba(232,97,42,.07);
  font-weight:500;
}
.cfg-nav-btn .cfg-nav-icon{font-size:1rem;width:1.2rem;text-align:center;}

/* ── Zone de contenu ── */
.cfg-content{flex:1;padding:2rem 2.5rem;max-width:780px;}

/* ── En-tête section ── */
.cfg-section-header{
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:1.5rem;padding-bottom:1rem;
  border-bottom:1px solid var(--border);
}
.cfg-section-title{
  font-size:1.1rem;font-weight:600;color:var(--text);
  display:flex;align-items:center;gap:.6rem;
}
.cfg-section-title .ico{font-size:1.2rem;}

/* ── Bloc de paramètres ── */
.cfg-card{
  background:var(--bg2);
  border:1px solid var(--border);
  border-radius:var(--radius);
  margin-bottom:1.25rem;
  overflow:hidden;
}
.cfg-row{
  display:grid;grid-template-columns:1fr 260px;
  align-items:center;gap:1.5rem;
  padding:.9rem 1.25rem;
  border-bottom:1px solid var(--border);
  transition:background .15s;
}
.cfg-row:last-child{border-bottom:none;}
.cfg-row:hover{background:rgba(255,255,255,.02);}
.cfg-row-label{font-size:.88rem;color:var(--text);font-weight:500;}
.cfg-row-desc{font-size:.75rem;color:var(--text-muted);margin-top:.2rem;line-height:1.4;}

/* ── Contrôles ── */
input[type=text],input[type=number],select,textarea{
  width:100%;
  background:var(--bg3);
  border:1px solid var(--border);
  border-radius:8px;
  color:var(--text);
  font-family:'Inter',sans-serif;
  font-size:.85rem;
  padding:.45rem .75rem;
  transition:border-color .2s,box-shadow .2s;
  outline:none;
}
input:focus,select:focus,textarea:focus{
  border-color:var(--accent);
  box-shadow:0 0 0 3px rgba(232,97,42,.15);
}
select{cursor:pointer;appearance:auto;background-image:none;background-repeat:no-repeat;background-position:right .75rem center;padding-right:2rem;}
input[type=range]{width:100%;accent-color:var(--accent);}

/* ── Boutons de sauvegarde ── */
.cfg-save-bar{
  display:flex;align-items:center;justify-content:flex-end;gap:.75rem;
  padding:.75rem 1.25rem;
  background:var(--bg3);
  border-top:1px solid var(--border);
}
.btn-save{
  display:inline-flex;align-items:center;gap:.4rem;
  background:var(--accent);border:none;border-radius:8px;
  color:white;font-size:.85rem;font-weight:600;
  padding:.5rem 1.1rem;cursor:pointer;
  transition:background .2s,transform .1s;
}
.btn-save:hover{background:var(--accent2);}
.btn-save:active{transform:scale(.97);}
.btn-save.secondary{
  background:var(--bg2);border:1px solid var(--border);
  color:var(--text-dim);
}
.btn-save.secondary:hover{border-color:var(--accent);color:var(--text);}
.btn-save.danger{background:#dc2626;}
.btn-save.danger:hover{background:#b91c1c;}
.save-indicator{
  font-size:.78rem;font-family:'JetBrains Mono',monospace;
  color:var(--green);opacity:0;transition:opacity .3s;
}
.save-indicator.show{opacity:1;}

/* ── Toast global ── */
.save-toast{
  display:none;position:fixed;bottom:1.5rem;right:1.5rem;
  background:var(--bg3);border:1px solid var(--green);
  color:var(--green);
  font-family:'JetBrains Mono',monospace;font-size:.8rem;
  padding:.65rem 1.2rem;border-radius:8px;
  box-shadow:var(--shadow);z-index:9999;
  animation:none;
}
.save-toast.show{display:flex;align-items:center;gap:.5rem;animation:slideUp .25s ease;}
@keyframes slideUp{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:translateY(0);}}

/* ── Voix TTS ── */
.lang-block{margin-bottom:1.5rem;}
.lang-header{
  display:flex;align-items:center;gap:.75rem;
  margin-bottom:.6rem;
  padding:.4rem .75rem;
  background:var(--bg3);border-radius:8px;
}
.lang-toggle{
  display:inline-flex;align-items:center;gap:.35rem;
  background:none;border:1px solid var(--border);
  border-radius:6px;padding:.25rem .6rem;
  font-size:.75rem;color:var(--text-muted);
  cursor:pointer;transition:all .15s;
}
.lang-toggle.on{background:rgba(34,197,94,.15);border-color:var(--green);color:var(--green);}
.lang-toggle.off{border-color:var(--border);}
.lang-name{font-weight:500;font-size:.88rem;color:var(--text);}
.voice-grid{display:flex;flex-wrap:wrap;gap:.4rem;padding:.25rem .25rem .5rem;}
.voice-chip{
  display:inline-flex;align-items:center;gap:.3rem;
  background:var(--bg);border:1px solid var(--border);
  border-radius:8px;padding:.3rem .7rem;
  font-size:.75rem;color:var(--text-dim);
  cursor:pointer;transition:all .15s;user-select:none;
}
.voice-chip:hover{border-color:var(--accent);color:var(--text);}
.voice-chip.on{
  background:rgba(232,97,42,.12);
  border-color:var(--accent);color:var(--text);
  font-weight:500;
}
.voice-chip.on::before{content:'✓ ';}
.voice-grid.disabled{opacity:.35;pointer-events:none;}

/* ── Sections cachées/affichées via sidebar ── */
.cfg-section{display:none;}
.cfg-section.active{display:block;}

/* ── Restart button ── */
.restart-card{
  display:flex;align-items:center;justify-content:space-between;
  gap:1rem;
  background:rgba(220,38,38,.07);
  border:1px solid rgba(220,38,38,.3);
  border-radius:var(--radius);
  padding:1rem 1.25rem;
  margin-bottom:1.5rem;
}
.restart-info{font-size:.85rem;color:var(--text-dim);}
.restart-info strong{color:var(--text);display:block;margin-bottom:.2rem;}
</style>
</head>
<body data-radio-label="{{ brand_name }}">

<!-- Topbar -->
<header class="topbar">
  <button class="topbar-logo-btn" data-action="home">
    <span class="topbar-logo">{{ logo_main }}<span>{{ logo_sub }}</span></span>
  </button>
  <div class="topbar-right">
    <button class="topbar-back" data-action="home">← Retour au site</button>
  </div>
</header>

<div class="cfg-layout">

  <!-- ── Sidebar navigation ── -->
  <nav class="cfg-sidebar">
    <div class="cfg-sidebar-title">Configuration</div>
    <div class="cfg-nav">
      <button class="cfg-nav-btn active" data-action="showSection" data-section="service">
        <span class="cfg-nav-icon">🏷</span> Identité du média
      </button>
      <button class="cfg-nav-btn" data-action="showSection" data-section="ollama">
        <span class="cfg-nav-icon">🤖</span> Modèle Ollama
      </button>
      <button class="cfg-nav-btn" data-action="showSection" data-section="rss">
        <span class="cfg-nav-icon">📡</span> Collecte RSS
      </button>
      <button class="cfg-nav-btn" data-action="showSection" data-section="radio">
        <span class="cfg-nav-icon">📻</span> Radio & Audio
      </button>
      <button class="cfg-nav-btn" data-action="showSection" data-section="voices">
        <span class="cfg-nav-icon">🎙</span> Voix TTS
      </button>
      <button class="cfg-nav-btn" data-action="showSection" data-section="web">
        <span class="cfg-nav-icon">🌐</span> Serveur web
      </button>
      <button class="cfg-nav-btn" data-action="showSection" data-section="icecast">
        <span class="cfg-nav-icon">📺</span> Icecast
      </button>
      <button class="cfg-nav-btn" data-action="showSection" data-section="system">
        <span class="cfg-nav-icon">⚙️</span> Système
      </button>
    </div>
  </nav>

  <!-- ── Contenu ── -->
  <main class="cfg-content">

    <!-- ────────────────── IDENTITÉ ────────────────── -->
    <section class="cfg-section active" id="sec-service">
      <div class="cfg-section-header">
        <div class="cfg-section-title"><span class="ico">🏷</span> Identité du média</div>
      </div>
      <div class="cfg-card">
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Nom du média</div>
            <div class="cfg-row-desc">Affiché dans le logo, les logs et les rapports</div>
          </div>
          <input type="text" id="svc-name" value="{{ svc.get('name','Nova Media') }}">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Tagline</div>
            <div class="cfg-row-desc">Sous-titre affiché en pied de page</div>
          </div>
          <input type="text" id="svc-tagline" value="{{ svc.get('tagline','') }}">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Langue par défaut</div>
            <div class="cfg-row-desc">Langue des résumés Ollama et du journal radio</div>
          </div>
          <select id="svc-lang">
            {% for code, label in lang_labels.items() %}
            <option value="{{ code }}" {{ 'selected' if svc.get('default_language','fr')==code }}>{{ label }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="cfg-save-bar">
          <span class="save-indicator" id="ind-service">✓ Sauvegardé</span>
          <button class="btn-save" data-action="saveService">💾 Enregistrer</button>
        </div>
      </div>
    </section>

    <!-- ────────────────── OLLAMA ────────────────── -->
    <section class="cfg-section" id="sec-ollama">
      <div class="cfg-section-header">
        <div class="cfg-section-title"><span class="ico">🤖</span> Modèle LLM</div>
      </div>
      <div class="cfg-card">
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Provider</div>
            <div class="cfg-row-desc">Ollama CLI, llama-server ou passerelle LiteLLM</div>
          </div>
          <select id="llm-provider">
            <option value="ollama" {{ 'selected' if llm.get('provider') not in ('llama-server','litellm') }}>ollama (CLI)</option>
            <option value="litellm" {{ 'selected' if llm.get('provider')=='litellm' }}>LiteLLM (passerelle)</option>
            <option value="llama-server" {{ 'selected' if llm.get('provider')=='llama-server' }}>llama-server (HTTP)</option>
          </select>
        </div>
        <div class="cfg-row" id="row-llm-baseurl" style="{{'display:none' if llm.get('provider') not in ('llama-server','litellm') }}">
          <div>
            <div class="cfg-row-label">Base URL HTTP</div>
            <div class="cfg-row-desc">URL du serveur LLM ou de la passerelle interne</div>
          </div>
          <input type="text" id="llm-baseurl" value="{{ llm.get('base_url','http://localhost:8080') }}" style="max-width:220px">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Modèle actif</div>
            {% if llm.get('provider')=='litellm' %}
            <div class="cfg-row-desc">Les tâches utilisent leurs alias configurés ; les modèles réels se règlent dans le déploiement de la passerelle.</div>
            {% else %}
            <div class="cfg-row-desc">Installez via <code style="background:var(--bg3);padding:.1rem .35rem;border-radius:4px;font-size:.8rem;">ollama pull &lt;modèle&gt;</code> ou téléchargez un GGUF</div>
            {% endif %}
          </div>
          <select id="ollama-model">
            {% if llm.get('provider')=='litellm' %}
            {% for alias in ['atlas-fast','atlas-translate','atlas-editorial','atlas-radio-script','atlas-verify'] %}
            <option value="{{ alias }}" {{ 'selected' if llm.get('model')==alias }}>{{ alias }}</option>
            {% endfor %}
            {% else %}
            {% for mid, mlabel in [('qwen3:8b','qwen3:8b'),('mistral-small:22b','mistral-small:22b'),('mistral:7b','mistral:7b'),('phi4:14b','phi4:14b'),('llama3.1:8b','llama3.1:8b'),('deepseek-r1:14b','deepseek-r1:14b'),('gemma2:9b','gemma2:9b')] %}
            <option value="{{ mid }}" {{ 'selected' if llm.get('model')==mid }}>{{ mlabel }}</option>
            {% endfor %}
            {% endif %}
          </select>
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Timeout résumé article (s)</div>
            <div class="cfg-row-desc">Secondes max pour résumer un article</div>
          </div>
          <input type="number" id="ollama-tfetch" value="{{ llm.get('timeout_fetch',240) }}" min="60" max="600" step="30">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Timeout rapport (s)</div>
            <div class="cfg-row-desc">Secondes max pour générer le rapport quotidien</div>
          </div>
          <input type="number" id="ollama-treport" value="{{ llm.get('timeout_report',600) }}" min="120" max="1800" step="60">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Timeout édition (s)</div>
            <div class="cfg-row-desc">Secondes max pour générer une édition</div>
          </div>
          <input type="number" id="ollama-tedition" value="{{ llm.get('timeout_edition',900) }}" min="300" max="3600" step="60">
        </div>
        <div class="cfg-save-bar">
          <span class="save-indicator" id="ind-ollama">✓ Sauvegardé</span>
          <button class="btn-save" data-action="saveOllama">💾 Enregistrer</button>
        </div>
      </div>

    </section>

    <!-- ────────────────── RSS ────────────────── -->
    <section class="cfg-section" id="sec-rss">
      <div class="cfg-section-header">
        <div class="cfg-section-title"><span class="ico">📡</span> Collecte RSS</div>
      </div>
      <div class="cfg-card">
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Articles max par flux</div>
            <div class="cfg-row-desc">Articles récupérés par source à chaque cycle</div>
          </div>
          <input type="number" id="rss-maxfeed" value="{{ rss.get('max_articles_per_feed',8) }}" min="3" max="30">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Fenêtre fil direct (heures)</div>
            <div class="cfg-row-desc">Historique affiché dans le fil en direct</div>
          </div>
          <input type="number" id="rss-window" value="{{ rss.get('live_window_hours',20) }}" min="4" max="72">
        </div>
        <div class="cfg-save-bar">
          <span class="save-indicator" id="ind-rss">✓ Sauvegardé</span>
          <button class="btn-save" data-action="saveRss">💾 Enregistrer</button>
        </div>
      </div>
    </section>

    <!-- ────────────────── RADIO ────────────────── -->
    <section class="cfg-section" id="sec-radio">
      <div class="cfg-section-header">
        <div class="cfg-section-title"><span class="ico">📻</span> Radio & Audio</div>
      </div>
      <div class="cfg-card">
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Volume musique de fond</div>
            <div class="cfg-row-desc">0.0 = silence · 1.0 = plein volume (défaut 0.30)</div>
          </div>
          <div style="display:flex;align-items:center;gap:.75rem;">
            <input type="range" id="radio-bgvol" min="0" max="1" step="0.05"
                   value="{{ radio.get('background_volume',0.30) }}"
                   >
            <span id="bgvol-val" style="font-family:'JetBrains Mono',monospace;font-size:.8rem;color:var(--accent);min-width:2.5rem;">
              {{ '%.2f'|format(radio.get('background_volume',0.30)|float) }}
            </span>
          </div>
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Bitrate audio</div>
            <div class="cfg-row-desc">Qualité du flux MP3</div>
          </div>
          <select id="radio-bitrate">
            {% for br in ['64k','96k','128k','192k','256k','320k'] %}
            <option value="{{ br }}" {{ 'selected' if radio.get('bitrate','128k')==br }}>{{ br }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="cfg-save-bar">
          <span class="save-indicator" id="ind-radio">✓ Sauvegardé</span>
          <button class="btn-save" data-action="saveRadio">💾 Enregistrer</button>
        </div>
      </div>
    </section>

    <!-- ────────────────── VOIX TTS ────────────────── -->
    <section class="cfg-section" id="sec-voices">
      <div class="cfg-section-header">
        <div class="cfg-section-title"><span class="ico">🎙</span> Voix TTS</div>
      </div>
      <p style="font-size:.83rem;color:var(--text-muted);margin-bottom:1.25rem;line-height:1.5;">
        Activez une langue puis cochez les voix à utiliser.
        Une voix aléatoire est choisie à chaque bulletin.<br>
        <span style="color:var(--accent);">Les voix s'appliquent au prochain bulletin sans redémarrage.</span>
      </p>
      <div id="voices-container">
        {% for lang_code, lang_voices in all_voices.items() %}
        {% set active_lang_voices = active_voices.get(lang_code, []) %}
        {% set lang_on = active_lang_voices|length > 0 %}
        <div class="lang-block" data-lang="{{ lang_code }}">
          <div class="lang-header">
            <button class="lang-toggle {{ 'on' if lang_on else 'off' }}"
                    data-action="toggleLang" data-lang="{{ lang_code }}">
              {{ '● ON' if lang_on else '○ OFF' }}
            </button>
            <span class="lang-name">{{ lang_labels.get(lang_code,lang_code) }}</span>
            <span style="font-size:.75rem;color:var(--text-muted);margin-left:auto;">
              {{ active_lang_voices|length }} voix active{{ 's' if active_lang_voices|length > 1 else '' }}
            </span>
          </div>
          <div class="voice-grid {{ '' if lang_on else 'disabled' }}" id="vg-{{ lang_code }}">
            {% for v in lang_voices %}
            {% set is_on = v in active_lang_voices %}
            <div class="voice-chip {{ 'on' if is_on else '' }}"
                 data-voice="{{ v }}" data-lang="{{ lang_code }}"
                 data-action="toggleVoice">
              {{ v.split('-')[2] if v.split('-')|length > 2 else v }}
            </div>
            {% endfor %}
          </div>
        </div>
        {% endfor %}
      </div>
      <div style="display:flex;justify-content:flex-end;margin-top:1rem;">
        <span class="save-indicator" id="ind-voices" style="margin-right:.75rem;">✓ Sauvegardé</span>
        <button class="btn-save" data-action="saveVoices">💾 Enregistrer les voix</button>
      </div>
    </section>

    <!-- ────────────────── WEB ────────────────── -->
    <section class="cfg-section" id="sec-web">
      <div class="cfg-section-header">
        <div class="cfg-section-title"><span class="ico">🌐</span> Serveur web</div>
      </div>
      <div class="cfg-card">
        <div class="cfg-row">
          <div><div class="cfg-row-label">Port</div>
            <div class="cfg-row-desc">Port d'écoute du serveur Flask</div></div>
          <input type="number" id="web-port" value="{{ web.get('port',5055) }}" min="1024" max="65535">
        </div>
        <div class="cfg-save-bar">
          <span class="save-indicator" id="ind-web">✓ Sauvegardé</span>
          <button class="btn-save" data-action="saveWeb">💾 Enregistrer</button>
        </div>
      </div>
    </section>

    <!-- ────────────────── ICECAST ────────────────── -->
    <section class="cfg-section" id="sec-icecast">
      <div class="cfg-section-header">
        <div class="cfg-section-title"><span class="ico">📺</span> Icecast</div>
      </div>
      <div class="cfg-card">
        <div class="cfg-row">
          <div><div class="cfg-row-label">Host</div></div>
          <input type="text" id="ic-host" value="{{ ic.get('host','localhost') }}">
        </div>
        <div class="cfg-row">
          <div><div class="cfg-row-label">Port</div></div>
          <input type="number" id="ic-port" value="{{ ic.get('port',8000) }}">
        </div>
        <div class="cfg-row">
          <div><div class="cfg-row-label">Mount point</div></div>
          <input type="text" id="ic-mount" value="{{ ic.get('mount','/nova') }}">
        </div>
        <div class="cfg-row">
          <div><div class="cfg-row-label">Mot de passe source</div></div>
          <input type="password" id="ic-password" value="" placeholder="Inchangé si vide" autocomplete="new-password">
        </div>
        <div class="cfg-save-bar">
          <span class="save-indicator" id="ind-icecast">✓ Sauvegardé</span>
          <button class="btn-save" data-action="saveIcecast">💾 Enregistrer</button>
        </div>
      </div>
    </section>

    <!-- ────────────────── SYSTÈME ────────────────── -->
    <section class="cfg-section" id="sec-system">
      <div class="cfg-section-header">
        <div class="cfg-section-title"><span class="ico">⚙️</span> Système</div>
      </div>

      <div class="restart-card">
        <div class="restart-info">
          <strong>Redémarrer le moteur de news</strong>
          Nécessaire après modification du modèle Ollama, des flux RSS ou de la langue.
          La radio et le serveur web continuent de tourner.
        </div>
        <button class="btn-save" id="btn-restart" data-action="restartEngine">
          ↺ Redémarrer
        </button>
      </div>

      <div class="cfg-card">
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Posts réseaux sociaux / cycle</div>
            <div class="cfg-row-desc">Nombre de posts générés par créneau horaire</div>
          </div>
          <input type="number" id="posts-nb" value="{{ posts.get('nb_posts_per_cycle',4) }}" min="1" max="10">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Fenêtre analyse posts (h)</div>
            <div class="cfg-row-desc">Heures d'articles analysés pour les posts</div>
          </div>
          <input type="number" id="posts-window" value="{{ posts.get('window_hours',2) }}" min="1" max="12">
        </div>
        <div class="cfg-save-bar">
          <span class="save-indicator" id="ind-posts">✓ Sauvegardé</span>
          <button class="btn-save" data-action="savePosts">💾 Enregistrer</button>
        </div>
      </div>
    </section>

  </main>
</div>

<!-- Toast global -->
<div class="save-toast" id="save-toast">✓ Sauvegardé dans config.yaml</div>



{{ flash_button_js }}
{{ global_script }}
</body></html>"""

# Local-only visual projection; no network in templates.
from modules.media.view import FIGURE as _MEDIA_FIGURE
LIVE_TEMPLATE=LIVE_TEMPLATE.replace('<!-- Bande couleur + icône + titre -->',_MEDIA_FIGURE+'<!-- Bande couleur + icône + titre -->')
LIVE_TEMPLATE=LIVE_TEMPLATE.replace('<article class="live-card"><h3>{{ event.title }}</h3>','<article class="live-card">'+_MEDIA_FIGURE.replace('media_for_article(a)','media_for_event(event)')+'<h3>{{ event.title }}</h3>')
REPORT_TEMPLATE=REPORT_TEMPLATE.replace('<div class="article-item" data-category="{{ a.category }}">','<div class="article-item" data-category="{{ a.category }}">'+_MEDIA_FIGURE)
BASE_CSS+='\n.news-image{margin:8px 0;max-width:320px}.news-image img{display:block;max-width:100%;height:auto;border-radius:8px}.news-image figcaption{font-size:11px;overflow-wrap:anywhere;color:var(--text-muted,#9aa)}\n'

EDITION_TEMPLATE=EDITION_TEMPLATE.replace('<div class="edition-body">','<div class="edition-illustrations">{% for a in image_articles %}'+_MEDIA_FIGURE+'{% endfor %}</div><div class="edition-body">')
