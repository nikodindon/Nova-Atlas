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
  - L'objet Flask est instancié à l'intérieur de run_server() pour que les
    chemins soient correctement résolus au moment de l'appel, pas au chargement
    du module.
  - load_atlas_config() / save_atlas_config() utilisent paths.data_dir
"""

import json
import os
import re
import markdown
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

# ─── CONSTANTES ÉDITORIALES ───────────────────────────────────────────────────

CATEGORY_LABELS = {
    "geopolitique":  "Géopolitique",
    "economie":      "Économie",
    "crypto":        "Crypto",
    "tech":          "Tech & IA",
    "france":        "France",
    "monde":         "Monde",
    "science":       "Science & Santé",
    "environnement": "Environnement",
    "societe":       "Société & Droits",
    "culture":       "Culture & Arts",
    "sport":         "Sport",
}

CATEGORY_ICONS = {
    "geopolitique":  "🌍",
    "economie":      "📈",
    "crypto":        "₿",
    "tech":          "⚡",
    "france":        "🗼",
    "monde":         "🌐",
    "science":       "🔬",
    "environnement": "🌿",
    "societe":       "✊",
    "culture":       "🎭",
    "sport":         "⚽",
}

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
    },
    "en": {
        "geopolitique": "Geopolitics", "economie": "Economy", "crypto": "Crypto",
        "tech": "Tech & AI", "france": "France", "monde": "World",
        "science": "Science & Health", "environnement": "Environment",
        "societe": "Society & Rights", "culture": "Culture & Arts", "sport": "Sport",
    },
    "de": {
        "geopolitique": "Geopolitik", "economie": "Wirtschaft", "crypto": "Krypto",
        "tech": "Tech & KI", "france": "Frankreich", "monde": "Welt",
        "science": "Wissenschaft & Gesundheit", "environnement": "Umwelt",
        "societe": "Gesellschaft", "culture": "Kultur & Kunst", "sport": "Sport",
    },
    "es": {
        "geopolitique": "Geopolítica", "economie": "Economía", "crypto": "Cripto",
        "tech": "Tech & IA", "france": "Francia", "monde": "Mundo",
        "science": "Ciencia & Salud", "environnement": "Medio ambiente",
        "societe": "Sociedad & Derechos", "culture": "Cultura & Arte", "sport": "Deporte",
    },
    "pt": {
        "geopolitique": "Geopolítica", "economie": "Economia", "crypto": "Cripto",
        "tech": "Tech & IA", "france": "França", "monde": "Mundo",
        "science": "Ciência & Saúde", "environnement": "Meio ambiente",
        "societe": "Sociedade & Direitos", "culture": "Cultura & Arte", "sport": "Esporte",
    },
    "it": {
        "geopolitique": "Geopolitica", "economie": "Economia", "crypto": "Cripto",
        "tech": "Tech & IA", "france": "Francia", "monde": "Mondo",
        "science": "Scienza & Salute", "environnement": "Ambiente",
        "societe": "Società & Diritti", "culture": "Cultura & Arte", "sport": "Sport",
    },
    "ru": {
        "geopolitique": "Геополитика", "economie": "Экономика", "crypto": "Крипто",
        "tech": "Технологии", "france": "Франция", "monde": "Мир",
        "science": "Наука и здоровье", "environnement": "Экология",
        "societe": "Общество", "culture": "Культура", "sport": "Спорт",
    },
    "zh": {
        "geopolitique": "地缘政治", "economie": "经济", "crypto": "加密货币",
        "tech": "科技与AI", "france": "法国", "monde": "世界",
        "science": "科学与健康", "environnement": "环境",
        "societe": "社会", "culture": "文化", "sport": "体育",
    },
    "ar": {
        "geopolitique": "الجيوسياسية", "economie": "الاقتصاد", "crypto": "عملات مشفرة",
        "tech": "التكنولوجيا", "france": "فرنسا", "monde": "العالم",
        "science": "العلوم والصحة", "environnement": "البيئة",
        "societe": "المجتمع", "culture": "الثقافة", "sport": "الرياضة",
    },
    "ja": {
        "geopolitique": "地政学", "economie": "経済", "crypto": "暗号資産",
        "tech": "テクノロジー", "france": "フランス", "monde": "世界",
        "science": "科学と健康", "environnement": "環境",
        "societe": "社会と権利", "culture": "文化と芸術", "sport": "スポーツ",
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

def _resolve_paths(config: dict) -> dict:
    """Extrait les chemins absolus + branding + i18n depuis le dict config Nova-Atlas."""
    paths = config.get("paths", {})
    root  = Path.cwd()
    svc   = config.get("service", {})
    name  = svc.get("name", "Nova Atlas")
    parts     = name.upper().rsplit(" ", 1)
    logo_main = parts[0]
    logo_sub  = "_" + parts[1] if len(parts) > 1 else ""
    return {
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
    """Construit l'URL du flux Icecast depuis atlas_config.json ou défaut."""
    cfg = load_atlas_config(paths)
    host  = cfg.get("icecast_host",  "localhost")
    port  = cfg.get("icecast_port",  8000)
    mount = cfg.get("icecast_mount", "/nova")
    #return "https://radio.nikodindon.dpdns.org"
    return f"http://{host}:{port}{mount}"
    #return "https://radio.nikodindon.dpdns.org"


def _branding(paths: dict) -> dict:
    """Retourne les variables de branding + i18n à injecter dans les templates."""
    ui = paths.get("ui", _UI_STRINGS["fr"])
    return {
        "brand_name":    paths["brand_name"],
        "brand_tagline": paths["brand_tagline"],
        "logo_main":     paths["logo_main"],
        "logo_sub":      paths["logo_sub"],
        "service_name":  paths["brand_name"],
        # UI strings traduits
        "ui":            ui,
        "cat_labels":    paths.get("cat_labels", CATEGORY_LABELS),
    }

# ─── CONFIG ATLAS (JSON) ──────────────────────────────────────────────────────

def _config_file(paths: dict) -> Path:
    return paths["data"] / "atlas_config.json"

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
    with open(f, "w", encoding="utf-8") as fp:
        json.dump(cfg, fp, ensure_ascii=False, indent=2)

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
    f = paths["articles"] / f"{day}_articles.json"
    if not f.exists():
        return []
    with open(f, "r", encoding="utf-8") as fp:
        return json.load(fp)

def load_report(day: str, paths: dict) -> str:
    f = paths["reports"] / f"{day}_report.md"
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
    stopwords = {
        "le","la","les","de","du","des","en","et","à","pour","sur","par",
        "dans","avec","un","une","au","aux","que","qui","est","sont","a",
        "the","of","in","to","and","is","are","for","on","at","an","that",
        "this","it","as","was","be","by","or","from","has","have","new",
        "says","said","after","will","can","more","its","but","not","their",
    }

    def kw(title):
        words = re.findall(r'\b[A-Za-zÀ-ÿ]{4,}\b', title.lower())
        return frozenset(w for w in words if w not in stopwords)

    today     = datetime.now().strftime("%Y%m%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
    cutoff    = datetime.now() - timedelta(hours=4)

    articles = []
    for day in [yesterday, today]:
        f = paths["articles"] / f"{day}_articles.json"
        if f.exists():
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    articles.extend(json.load(fp))
            except Exception:
                pass

    recent = [
        a for a in articles
        if not a.get("migrated") and a.get("title") and a.get("link")
        and _parse_ts(a.get("timestamp","")) >= cutoff
    ]
    if not recent:
        return []

    groups, grouped = [], set()
    for i, a in enumerate(recent):
        if i in grouped:
            continue
        kw_i = kw(a.get("title", ""))
        group = [a]
        grouped.add(i)
        for j, b in enumerate(recent):
            if j <= i or j in grouped:
                continue
            if len(kw_i & kw(b.get("title",""))) >= 2:
                group.append(b)
                grouped.add(j)
        groups.append(group)

    groups.sort(key=lambda g: -len(g))
    breaking = []
    for group in groups:
        if len(group) < 2:
            continue
        best    = max(group, key=lambda x: len(x.get("summary","")))
        sources = list({a.get("source","") for a in group if a.get("source")})[:3]
        breaking.append({
            "title":   best.get("title","")[:80],
            "link":    best.get("link",""),
            "count":   len(group),
            "sources": ", ".join(sources),
            "cat":     best.get("category","monde"),
        })
        if len(breaking) >= max_items:
            break
    return breaking

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
        title = item["title"].replace('"','&quot;').replace('<','&lt;').replace('>','&gt;')
        parts.append(
            f'<a href="{item["link"]}" target="_blank" rel="noopener" class="bb-item">'
            f'{badge}{emoji} {title}'
            f'</a><span class="bb-sep"> &nbsp;·&nbsp; </span>'
        )
    track = "".join(parts)
    # Le label "BREAKING" vient des traductions stockées dans paths
    ui = paths.get("ui", {})
    breaking_label = ui.get("breaking", "BREAKING")
    return (
        '<div class="breaking-banner">'
        f'<span class="bb-label"><span class="live-dot"></span>{breaking_label}</span>'
        '<div class="bb-track-wrap">'
        f'<div class="bb-track">{track}{track}</div>'
        '</div></div>'
    )

# ─── RENDERERS ────────────────────────────────────────────────────────────────

def render_report_page(day: str, paths: dict) -> str:
    from jinja2 import Template
    articles  = load_articles_for_day(day, paths)
    report_md = load_report(day, paths)

    if not report_md and not articles:
        return ""
    if not report_md:
        report_md = f"# {format_date_fr(day)}\n\n*Rapport en cours de génération...*"

    report_html = markdown.markdown(report_md, extensions=["extra"])

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
        global_script=GLOBAL_SCRIPT,
        icecast_url=_get_icecast_url(paths),
        css=BASE_CSS,
        breaking_banner=render_breaking_banner(paths),
        title=format_date_fr(day),
        date_fr=format_date_fr(day),
        day_label=format_day_label(day),
        report_html=report_html,
        article_count=len(articles),
        source_count=len(sources),
        gen_time=datetime.now().strftime("%H:%M"),
        categories=categories,
        cat_icons=CATEGORY_ICONS,
        stats=stats,
        recent_articles=recent,
        archives=archives,
        all_articles=all_arts_sorted,
        today_file=f"{today_str}_report.html",
        model=load_atlas_config(paths).get("ollama_model","—"),
        year=datetime.now().year,
    )


def load_edition(day: str, edition_name: str, paths: dict) -> str:
    f = paths["editions"] / f"{day}_{edition_name}.md"
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
    from jinja2 import Template
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

    body_html     = markdown.markdown("\n".join(body_lines).strip(), extensions=["extra"])
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
        global_script=GLOBAL_SCRIPT,
        icecast_url=_get_icecast_url(paths),
        css=BASE_CSS,
        breaking_banner=render_breaking_banner(paths),
        edition_label=cfg["label"],
        edition_emoji=cfg["emoji"],
        edition_color=cfg["color"],
        current_edition=edition_name,
        article_title=art_title or cfg["label"],
        date_fr=date_fr,
        article_count=article_count,
        gen_time=now.strftime("%H:%M"),
        body_html=body_html,
        editions_of_day=editions_day,
        edition_configs=EDITION_CONFIG,
        day=day,
        today_report=f"{today_str}_report.html",
        year=now.year,
    )


def build_index(paths: dict) -> str:
    from jinja2 import Template
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
        global_script=GLOBAL_SCRIPT,
        icecast_url=_get_icecast_url(paths),
        css=BASE_CSS,
        breaking_banner=render_breaking_banner(paths),
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


def build_live_feed(paths: dict) -> str:
    """Fil en direct — articles des dernières N heures."""
    from jinja2 import Template
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
        global_script=GLOBAL_SCRIPT,
        icecast_url=_get_icecast_url(paths),
        css=BASE_CSS + LIVE_CSS_EXTRA,
        breaking_banner=render_breaking_banner(paths),
        articles=recent,          # fil chronologique unique
        cat_counts=cat_counts_sorted,
        cat_icons=CATEGORY_ICONS,
        total=len(recent),
        window_hours=window_hours,
        today_file=f"{today_str}_report.html",
        updated=datetime.now().strftime("%H:%M"),
        year=datetime.now().year,
    )


def build_config_yaml_page(paths: dict, config: dict) -> str:
    """Page de configuration complète depuis config.yaml."""
    from jinja2 import Template
    svc    = config.get("service",  {})
    ollama = config.get("ollama",   {})
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

    return Template(CONFIG_YAML_TEMPLATE).render(
        **_branding(paths),
        global_script=GLOBAL_SCRIPT,
        icecast_url=_get_icecast_url(paths),
        svc=svc, ollama=ollama, radio=radio, rss=rss,
        web=web, posts=posts, ic=ic,
        active_voices=voices,
        all_voices=ALL_VOICES,
        lang_labels=LANG_LABELS,
        year=datetime.now().year,
    )


def build_config_page(paths: dict) -> str:
    from jinja2 import Template
    cfg = load_atlas_config(paths)
    return Template(CONFIG_TEMPLATE).render(
        **_branding(paths),
        global_script=GLOBAL_SCRIPT,
        icecast_url=_get_icecast_url(paths),
        css=BASE_CSS,
        cfg=cfg,
        models=AVAILABLE_MODELS,
        today_file=f"{datetime.now().strftime('%Y%m%d')}_report.html",
        year=datetime.now().year,
    )

# ─── STATIC SITE GENERATOR ────────────────────────────────────────────────────

def generate_static_site(config: dict, full: bool = False) -> str:
    paths = _resolve_paths(config)
    paths["site"].mkdir(parents=True, exist_ok=True)

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


def run_server(config: dict, host: str = "0.0.0.0", port: int = 5055,
               debug: bool = False):
    """
    Point d'entrée du serveur Flask.
    Toujours appelé avec le dict config complet issu de config.yaml.
    """
    from flask import Flask, abort, request, jsonify, send_from_directory

    paths = _resolve_paths(config)
    # Sync icecast config → atlas_config.json pour que le player web la lise
    _sync_icecast_to_atlas_config(config, paths)
    app   = Flask(__name__)

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
            data    = request.get_json()
            cfg     = load_atlas_config(paths)
            allowed = ["ollama_model","fetch_timeout","edition_timeout",
                       "max_articles_feed","live_window_hours"]
            for k in allowed:
                if k in data:
                    cfg[k] = data[k]
            save_atlas_config(paths, cfg)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"status": "error", "msg": str(e)}), 500

    @app.route("/config/yaml")
    def config_yaml_page():
        # Recharge config depuis le disque pour afficher les valeurs actuelles
        from modules.core.config import load_config as _load_cfg
        import pathlib as _pl
        cfg_path = _pl.Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
        if not cfg_path.exists():
            cfg_path = _pl.Path("config/config.yaml")
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
            flag = paths["data"] / ".reload_config"
            flag.touch()
            # Recharge la config depuis le disque et rebuild le site
            from modules.core.config import load_config as _load_cfg
            import pathlib as _pl
            cfg_path = _pl.Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
            if not cfg_path.exists():
                cfg_path = _pl.Path("config/config.yaml")
            try:
                new_cfg  = _load_cfg(str(cfg_path))
                new_paths = _resolve_paths(new_cfg)
                generate_static_site(new_cfg, full=True)
            except Exception as e:
                pass  # Le rebuild est best-effort
            return jsonify({"status": "ok", "msg": "Rechargement config + rebuild site demandés"})
        except Exception as e:
            return jsonify({"status": "error", "msg": str(e)}), 500

    @app.route("/config/yaml/save", methods=["POST"])
    def config_yaml_save():
        import yaml as _yaml
        try:
            data     = request.get_json()
            section  = data.get("section")
            key      = data.get("key")
            value    = data.get("value")
            # Chemin absolu : remonte depuis modules/web/ → racine du projet
            cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / "config.yaml"
            if not cfg_path.exists():
                # Fallback sur le CWD (local)
                cfg_path = Path("config/config.yaml")
            with open(cfg_path, "r", encoding="utf-8") as f:
                yml = _yaml.safe_load(f)
            # Navigation dans le dict imbriqué
            if section and key:
                if section not in yml:
                    yml[section] = {}
                yml[section][key] = value
            elif section:
                yml[section] = value
            with open(cfg_path, "w", encoding="utf-8") as f:
                _yaml.dump(yml, f, allow_unicode=True,
                           default_flow_style=False, sort_keys=False)
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"status": "error", "msg": str(e)}), 500

    @app.route("/editions/<path:filename>")
    def serve_edition(filename):
        if filename.endswith(".html"):
            parts = filename[:-5].split("_")
            if len(parts) == 2:
                html = render_edition_page(parts[0], parts[1], paths)
                if html:
                    return html
        abort(404)

    @app.route("/<path:filename>")
    def serve_page(filename):
        if filename.endswith("_report.html"):
            html = render_report_page(filename[:8], paths)
            if html:
                return html
            abort(404)
        try:
            return send_from_directory(str(paths["site"]), filename)
        except Exception:
            abort(404)

    print(f"\n✅ {paths["brand_name"]} → http://localhost:{port}/\n")
    app.run(host=host, port=port, debug=debug)

# ─── TEMPLATES HTML ───────────────────────────────────────────────────────────
# (identiques à la version originale — seules les références aux chemins ont
#  été supprimées du code Python, les templates HTML ne changent pas)

BASE_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=Source+Sans+3:wght@300;400;600&family=JetBrains+Mono:wght@400;500&display=swap');

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

GLOBAL_SCRIPT = """
<!-- Iframe audio persistante : survit aux navigations (montée dans localStorage) -->
<iframe id="nova-radio-frame" src="about:blank"
  style="display:none;width:0;height:0;border:0;" allow="autoplay"></iframe>

<script>
// ── Theme : appliqué immédiatement (avant rendu pour éviter le flash) ─────────
(function(){
  var t = localStorage.getItem('nova-theme');
  if(t==='light') document.body.classList.add('light');
  var btn = document.querySelector('.theme-btn');
  if(btn) btn.textContent = t==='light' ? '☀️' : '🌙';
})();

function toggleTheme(){
  var light = document.body.classList.toggle('light');
  localStorage.setItem('nova-theme', light ? 'light' : 'dark');
  var btn = document.querySelector('.theme-btn');
  if(btn) btn.textContent = light ? '☀️' : '🌙';
}

// ── Player radio via iframe persistante ──────────────────────────────────────
// L'astuce : on stocke l'URL dans localStorage et on met l'audio dans une
// iframe dont le src pointe vers /radio-player (une route Flask légère).
// L'iframe est masquée et ne se recharge pas quand on navigue.
// Mais pour une vraie persistance cross-page SANS SPA, la meilleure solution
// simple est : injecter l'audio dans le document parent et le garder en vie
// en interceptant les clics de navigation pour faire des fetch() + pushState.
// On utilise ici l'approche la plus robuste sans réécrire l'appli en SPA :
// stocker l'état dans localStorage + reprendre l'audio si on revient sur la page.

var _radioPlaying = localStorage.getItem('nova-radio-playing') === '1';
var _radioUrl     = localStorage.getItem('nova-radio-url') || '';

// Nœud audio global — créé une seule fois par onglet
if(!window._novaAudio){
  window._novaAudio = new Audio();
  window._novaAudio.preload = 'none';
  // Reconnexion automatique si le flux coupe
  window._novaAudio.addEventListener('error', function(){
    setTimeout(function(){
      if(!window._novaAudio.paused && _radioUrl){
        window._novaAudio.src = _radioUrl;
        window._novaAudio.load();
        window._novaAudio.play().catch(function(){});
      }
    }, 3000);
  });
}

function _syncPlayerUI(){
  var playing = !window._novaAudio.paused;
  var btn   = document.getElementById('radio-btn');
  var wave  = document.getElementById('radio-wave');
  var label = document.getElementById('radio-label');
  if(btn)   btn.textContent = playing ? '⏸' : '▶';
  if(wave)  wave.classList.toggle('playing', playing);
  if(label) label.textContent = playing ? 'EN DIRECT' : (document.body.dataset.radioLabel || '{{ ui.radio_live }}');
}

function radioToggle(streamUrl){
  _radioUrl = streamUrl;
  localStorage.setItem('nova-radio-url', streamUrl);
  var audio = window._novaAudio;

  if(audio.paused){
    // Premier play ou reprise
    if(audio.src !== streamUrl){
      audio.src = streamUrl;
    }
    var p = audio.play();
    if(p) p.then(function(){
      localStorage.setItem('nova-radio-playing','1');
      _syncPlayerUI();
    }).catch(function(e){ console.warn('play blocked:', e); });
  } else {
    audio.pause();
    localStorage.setItem('nova-radio-playing','0');
    _syncPlayerUI();
  }
}

// Sync UI au chargement (si la radio était en cours dans un autre onglet/page)
document.addEventListener('DOMContentLoaded', function(){
  window._novaAudio.addEventListener('play',  _syncPlayerUI);
  window._novaAudio.addEventListener('pause', _syncPlayerUI);
  _syncPlayerUI();

  // Auto-reprise si l'utilisateur avait la radio allumée avant de naviguer
  if(_radioPlaying && _radioUrl && window._novaAudio.paused){
    window._novaAudio.src = _radioUrl;
    window._novaAudio.play().catch(function(){});
  }
});

// ── Interception des liens internes pour navigation sans rechargement ─────────
// Cela permet à l'audio de survivre en changeant de section.
document.addEventListener('DOMContentLoaded', function(){
  document.addEventListener('click', function(e){
    var a = e.target.closest('a');
    if(!a) return;
    var href = a.getAttribute('href');
    // Rechargement complet pour config (redirect) et live (CSS spécifique)
    if(!href || href.startsWith('http') || href.startsWith('#')
       || href.startsWith('mailto') || a.target === '_blank'
       || href.startsWith('/config')
       || href === '/live' || href === '/live.html') return;
    e.preventDefault();
    fetch(href)
      .then(function(r){ return r.text(); })
      .then(function(html){
        var parser  = new DOMParser();
        var newDoc  = parser.parseFromString(html, 'text/html');
        // Remplace le contenu de la page
        document.title = newDoc.title;
        document.querySelector('.page') && (
          document.querySelector('.page').innerHTML =
          newDoc.querySelector('.page') ? newDoc.querySelector('.page').innerHTML : ''
        );
        // Remplace nav (pour les états actifs)
        var newNav = newDoc.querySelector('.topbar-nav');
        var curNav = document.querySelector('.topbar-nav');
        if(newNav && curNav) curNav.innerHTML = newNav.innerHTML;
        // Breaking banner
        var newBb = newDoc.querySelector('.breaking-banner');
        var curBb = document.querySelector('.breaking-banner');
        if(newBb && curBb) curBb.outerHTML = newBb.outerHTML;
        // Met à jour l'URL
        history.pushState({}, document.title, href);
        // Ré-initialise les scripts de la nouvelle page
        var newScripts = newDoc.querySelectorAll('script:not([src])');
        newScripts.forEach(function(s){
          // On n'exécute que les scripts spécifiques à la page (pas GLOBAL_SCRIPT)
          if(s.textContent.includes('filterCat') || s.textContent.includes('toggleSummary')){
            try{ eval(s.textContent); }catch(e){}
          }
        });
        // data-page pour le live refresh
        var newBody = newDoc.body;
        document.body.dataset.page = newBody.dataset.page || '';
        // Ré-init live refresh si page live
        if(document.body.dataset.page === 'live') _startLiveRefresh();
        // Theme
        if(localStorage.getItem('nova-theme')==='light') document.body.classList.add('light');
        // Scroll top
        window.scrollTo(0,0);
      })
      .catch(function(){ window.location.href = href; }); // fallback
  });

  // Bouton précédent/suivant du navigateur
  window.addEventListener('popstate', function(){
    window.location.reload();
  });
});

// ── Live refresh sans coupure audio ──────────────────────────────────────────
var _liveRefreshTimer = null;

function _startLiveRefresh(){
  if(_liveRefreshTimer) clearTimeout(_liveRefreshTimer);
  _liveRefreshTimer = setTimeout(function _doRefresh(){
    fetch(location.href)
      .then(function(r){ return r.text(); })
      .then(function(html){
        var parser  = new DOMParser();
        var newDoc  = parser.parseFromString(html, 'text/html');
        var newGrid = newDoc.getElementById('live-grid');
        var curGrid = document.getElementById('live-grid');
        if(newGrid && curGrid){
          // Mémorise le filtre actif
          var activeBtn = document.querySelector('.filter-btn.active');
          var activeCat = activeBtn ? activeBtn.dataset.cat : 'all';

          curGrid.innerHTML = newGrid.innerHTML;

          // Ré-applique le filtre
          document.querySelectorAll('#live-grid .live-item').forEach(function(el){
            el.style.display = (activeCat==='all' || el.dataset.cat===activeCat) ? '' : 'none';
          });

          // Met à jour les compteurs dans les boutons de filtre
          var newBar = newDoc.querySelector('.filter-bar');
          var curBar = document.querySelector('.filter-bar');
          if(newBar && curBar){
            // Remplace tous les boutons sauf le "Tout" actif
            curBar.innerHTML = newBar.innerHTML;
            // Remet le bon bouton actif
            curBar.querySelectorAll('.filter-btn').forEach(function(b){
              b.classList.toggle('active', b.dataset.cat === activeCat || (!b.dataset.cat && activeCat==='all'));
            });
          }

          // Timestamp
          var ts    = document.getElementById('live-updated');
          var newTs = newDoc.getElementById('live-updated');
          if(ts && newTs) ts.textContent = newTs.textContent;
        }
      })
      .catch(function(){})
      .finally(function(){ _liveRefreshTimer = setTimeout(_doRefresh, 120000); });
  }, 120000);
}

document.addEventListener('DOMContentLoaded', function(){
  if(document.body.dataset.page === 'live') _startLiveRefresh();
});
</script>
"""

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
            onclick="radioToggle('{{ icecast_url }}')">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  <button class="theme-btn" onclick="toggleTheme()" title="Changer le thème">🌙</button>
  <div class="topbar-nav">
    <a href="/">{{ ui.nav_home }}</a>
    <a href="/live" style="color:var(--accent)"><span class="live-dot"></span>{{ ui.nav_live }}</a>
    <a href="/{{ today_file }}">{{ ui.nav_chronicle }}</a>
    <a href="/archives">{{ ui.nav_archives }}</a>
    <a href="/config">⚙</a>
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
        <div class="article-item">
          <a href="{{ a.link }}" target="_blank" rel="noopener">
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
    <button class="sources-toggle" onclick="toggleSources(this)" aria-expanded="false">
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
        <a href="{{ a.link }}" target="_blank" rel="noopener" class="source-title">{{ a.title }}</a>
      </div>
      {% endif %}{% endfor %}
    </div>
  </div>
  {% endif %}
</div>
<footer><strong>{{ brand_name }}</strong> — {{ brand_tagline }}<br>Modèle : {{ model }} · © {{ year }}</footer>
<script>
function toggleSources(btn){
  const list=document.getElementById('sources-list');
  const open=list.classList.contains('open');
  list.classList.toggle('open',!open);
  btn.classList.toggle('open',!open);
  btn.setAttribute('aria-expanded',String(!open));
}
</script>
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
            onclick="radioToggle('{{ icecast_url }}')">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  <button class="theme-btn" onclick="toggleTheme()" title="Changer le thème">🌙</button>
  <div class="topbar-nav">
    <a href="/">{{ ui.nav_home }}</a>
    <a href="/live" style="color:var(--accent)"><span class="live-dot"></span>{{ ui.nav_live }}</a>
    {% if today_file %}<a href="/{{ today_file }}">{{ ui.nav_chronicle }}</a>{% endif %}
    <a href="/archives">{{ ui.nav_archives }}</a>
    <a href="/config">⚙</a>
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
            onclick="radioToggle('{{ icecast_url }}')">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  <button class="theme-btn" onclick="toggleTheme()" title="Changer le thème">🌙</button>
  <div class="topbar-nav">
    <a href="/">{{ ui.nav_home }}</a>
    <a href="/live" style="color:var(--accent)"><span class="live-dot"></span>{{ ui.nav_live }}</a>
    <a href="/{{ today_report }}">{{ ui.nav_chronicle }}</a>
    <a href="/archives">{{ ui.nav_archives }}</a>
    <a href="/config">⚙</a>
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
{{ global_script }}
</body></html>"""

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
            onclick="radioToggle('{{ icecast_url }}')">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  <button class="theme-btn" onclick="toggleTheme()" title="Changer le thème">🌙</button>
  <div class="topbar-nav">
    <a href="/">{{ ui.nav_home }}</a>
    <a href="/live" style="color:var(--accent)"><span class="live-dot"></span>{{ ui.nav_live }}</a>
    {% if today_file %}<a href="/{{ today_file }}">{{ ui.nav_chronicle }}</a>{% endif %}
    <a href="/archives">{{ ui.nav_archives }}</a>
    <a href="/config">⚙</a>
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

  <!-- Filtres par catégorie — masquage JS côté client -->
  <div class="filter-bar">
    <button class="filter-btn active" data-cat="all" onclick="filterCat('all',this)">
      {{ ui.filter_all }} <span class="filter-count">{{ total }}</span>
    </button>
    {% for cat, count in cat_counts %}
    <button class="filter-btn" data-cat="{{ cat }}" onclick="filterCat('{{ cat }}',this)">
      {{ cat_icons.get(cat,'') }} {{ cat_labels.get(cat,cat) }}
      <span class="filter-count">{{ count }}</span>
    </button>
    {% endfor %}
  </div>

  <!-- Fil chronologique unique — chaque item porte data-cat pour le filtre JS -->
  <div class="live-grid" id="live-grid">
    {% for a in articles %}
    {% set cat = a.get('category', 'monde') %}
    <div class="live-item" data-cat="{{ cat }}">

      <!-- Bande couleur + icône + titre -->
      <div class="live-item-row" style="border-left:3px solid var(--cat-color-{{ cat }},var(--accent))">
        <span class="live-cat-icon">{{ cat_icons.get(cat,'🌐') }}</span>
        <a href="{{ a.link }}" target="_blank" rel="noopener" class="live-item-title">{{ a.title }}</a>
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
                onclick="toggleSummary(this,'sum-{{ loop.index0 }}')"
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
<script>
function filterCat(cat, btn) {
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('#live-grid .live-item').forEach(el => {
    el.style.display = (cat === 'all' || el.dataset.cat === cat) ? '' : 'none';
  });
}

function toggleSummary(btn, id) {
  const el   = document.getElementById(id);
  const open = el.classList.toggle('expanded');
  btn.classList.toggle('open', open);
  btn.setAttribute('aria-expanded', open);
  btn.querySelector('.tgl-label').textContent = open ? '{{ ui.reduce }}' : '{{ ui.read_more }}';
}
</script>
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
            onclick="radioToggle('{{ icecast_url }}')">▶</button>
    <div class="radio-wave" id="radio-wave">
      <span></span><span></span><span></span><span></span><span></span>
    </div>
    <span class="radio-label" id="radio-label">{{ ui.radio_live }}</span>
  </div>
  <button class="theme-btn" onclick="toggleTheme()" title="Changer le thème">🌙</button>
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
<script>
document.getElementById('configForm').addEventListener('submit',async(e)=>{
  e.preventDefault();
  const data=Object.fromEntries(new FormData(e.target));
  ['fetch_timeout','edition_timeout','max_articles_feed','live_window_hours'].forEach(k=>{if(data[k])data[k]=parseInt(data[k]);});
  const r=await fetch('/config/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(r.ok){
    document.getElementById('saveMsg').style.display='block';
    setTimeout(()=>document.getElementById('saveMsg').style.display='none',4000);
    document.querySelector('.current-tag').textContent=data.ollama_model;
  }
});
</script>
{{ global_script }}
</body></html>"""


CONFIG_YAML_TEMPLATE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ brand_name }} — Configuration</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
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
select{cursor:pointer;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%238888a0' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right .75rem center;padding-right:2rem;}
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
  <button class="topbar-logo-btn" onclick="window.location.href='/'">
    <span class="topbar-logo">{{ logo_main }}<span>{{ logo_sub }}</span></span>
  </button>
  <div class="topbar-right">
    <button class="topbar-back" onclick="window.location.href='/'">← Retour au site</button>
  </div>
</header>

<div class="cfg-layout">

  <!-- ── Sidebar navigation ── -->
  <nav class="cfg-sidebar">
    <div class="cfg-sidebar-title">Configuration</div>
    <div class="cfg-nav">
      <button class="cfg-nav-btn active" onclick="showSection('service',this)">
        <span class="cfg-nav-icon">🏷</span> Identité du média
      </button>
      <button class="cfg-nav-btn" onclick="showSection('ollama',this)">
        <span class="cfg-nav-icon">🤖</span> Modèle Ollama
      </button>
      <button class="cfg-nav-btn" onclick="showSection('rss',this)">
        <span class="cfg-nav-icon">📡</span> Collecte RSS
      </button>
      <button class="cfg-nav-btn" onclick="showSection('radio',this)">
        <span class="cfg-nav-icon">📻</span> Radio & Audio
      </button>
      <button class="cfg-nav-btn" onclick="showSection('voices',this)">
        <span class="cfg-nav-icon">🎙</span> Voix TTS
      </button>
      <button class="cfg-nav-btn" onclick="showSection('web',this)">
        <span class="cfg-nav-icon">🌐</span> Serveur web
      </button>
      <button class="cfg-nav-btn" onclick="showSection('icecast',this)">
        <span class="cfg-nav-icon">📺</span> Icecast
      </button>
      <button class="cfg-nav-btn" onclick="showSection('system',this)">
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
          <button class="btn-save" onclick="saveService()">💾 Enregistrer</button>
        </div>
      </div>
    </section>

    <!-- ────────────────── OLLAMA ────────────────── -->
    <section class="cfg-section" id="sec-ollama">
      <div class="cfg-section-header">
        <div class="cfg-section-title"><span class="ico">🤖</span> Modèle Ollama</div>
      </div>
      <div class="cfg-card">
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Modèle actif</div>
            <div class="cfg-row-desc">Installez via <code style="background:var(--bg3);padding:.1rem .35rem;border-radius:4px;font-size:.8rem;">ollama pull &lt;modèle&gt;</code></div>
          </div>
          <select id="ollama-model">
            {% for mid, mlabel in [('qwen3:8b','qwen3:8b'),('mistral-small:22b','mistral-small:22b'),('mistral:7b','mistral:7b'),('phi4:14b','phi4:14b'),('llama3.1:8b','llama3.1:8b'),('deepseek-r1:14b','deepseek-r1:14b'),('gemma2:9b','gemma2:9b')] %}
            <option value="{{ mid }}" {{ 'selected' if ollama.get('model')==mid }}>{{ mlabel }}</option>
            {% endfor %}
          </select>
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Timeout résumé article (s)</div>
            <div class="cfg-row-desc">Secondes max pour résumer un article</div>
          </div>
          <input type="number" id="ollama-tfetch" value="{{ ollama.get('timeout_fetch',240) }}" min="60" max="600" step="30">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Timeout rapport (s)</div>
            <div class="cfg-row-desc">Secondes max pour générer le rapport quotidien</div>
          </div>
          <input type="number" id="ollama-treport" value="{{ ollama.get('timeout_report',600) }}" min="120" max="1800" step="60">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Timeout édition (s)</div>
            <div class="cfg-row-desc">Secondes max pour générer une édition</div>
          </div>
          <input type="number" id="ollama-tedition" value="{{ ollama.get('timeout_edition',900) }}" min="300" max="3600" step="60">
        </div>
        <div class="cfg-save-bar">
          <span class="save-indicator" id="ind-ollama">✓ Sauvegardé</span>
          <button class="btn-save" onclick="saveOllama()">💾 Enregistrer</button>
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
          <button class="btn-save" onclick="saveRss()">💾 Enregistrer</button>
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
            <div class="cfg-row-label">Articles par bulletin</div>
            <div class="cfg-row-desc">Nombre de nouvelles news pour déclencher un bulletin</div>
          </div>
          <input type="number" id="radio-nbulletin" value="{{ radio.get('news_per_bulletin',5) }}" min="1" max="20">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Intervalle de surveillance (s)</div>
            <div class="cfg-row-desc">Fréquence de vérification des nouveaux articles</div>
          </div>
          <input type="number" id="radio-interval" value="{{ radio.get('news_interval_seconds',30) }}" min="5" max="300">
        </div>
        <div class="cfg-row">
          <div>
            <div class="cfg-row-label">Volume musique de fond</div>
            <div class="cfg-row-desc">0.0 = silence · 1.0 = plein volume (défaut 0.30)</div>
          </div>
          <div style="display:flex;align-items:center;gap:.75rem;">
            <input type="range" id="radio-bgvol" min="0" max="1" step="0.05"
                   value="{{ radio.get('background_volume',0.30) }}"
                   oninput="document.getElementById('bgvol-val').textContent=parseFloat(this.value).toFixed(2)">
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
          <button class="btn-save" onclick="saveRadio()">💾 Enregistrer</button>
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
                    onclick="toggleLang('{{ lang_code }}',this)">
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
                 onclick="toggleVoice(this)">
              {{ v.split('-')[2] if v.split('-')|length > 2 else v }}
            </div>
            {% endfor %}
          </div>
        </div>
        {% endfor %}
      </div>
      <div style="display:flex;justify-content:flex-end;margin-top:1rem;">
        <span class="save-indicator" id="ind-voices" style="margin-right:.75rem;">✓ Sauvegardé</span>
        <button class="btn-save" onclick="saveVoices()">💾 Enregistrer les voix</button>
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
          <button class="btn-save" onclick="saveWeb()">💾 Enregistrer</button>
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
          <input type="text" id="ic-password" value="{{ ic.get('password','hackme') }}">
        </div>
        <div class="cfg-save-bar">
          <span class="save-indicator" id="ind-icecast">✓ Sauvegardé</span>
          <button class="btn-save" onclick="saveIcecast()">💾 Enregistrer</button>
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
        <button class="btn-save" id="btn-restart" onclick="restartEngine()">
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
          <button class="btn-save" onclick="savePosts()">💾 Enregistrer</button>
        </div>
      </div>
    </section>

  </main>
</div>

<!-- Toast global -->
<div class="save-toast" id="save-toast">✓ Sauvegardé dans config.yaml</div>

<script>
// ── Navigation sidebar ────────────────────────────────────────────────────────
function showSection(id, link){
  document.querySelectorAll('.cfg-section').forEach(s=>s.classList.remove('active'));
  document.querySelectorAll('.cfg-nav-btn').forEach(a=>a.classList.remove('active'));
  document.getElementById('sec-'+id).classList.add('active');
  link.classList.add('active');
  return false;
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(indId){
  var t = document.getElementById('save-toast');
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2500);
  if(indId){
    var ind = document.getElementById(indId);
    if(ind){ ind.classList.add('show'); setTimeout(()=>ind.classList.remove('show'),3000); }
  }
}

// ── Sauvegardes par section ───────────────────────────────────────────────────
function post(data, ind){ 
  return fetch('/config/yaml/save',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(data)
  }).then(r=>r.json()).then(d=>{ if(d.status==='ok') showToast(ind); });
}

function saveService(){
  Promise.all([
    post({section:'service',key:'name',    value:document.getElementById('svc-name').value},'ind-service'),
    post({section:'service',key:'tagline', value:document.getElementById('svc-tagline').value},'ind-service'),
    post({section:'service',key:'default_language',value:document.getElementById('svc-lang').value},'ind-service'),
  ]);
}

function saveOllama(){
  Promise.all([
    post({section:'ollama',key:'model',           value:document.getElementById('ollama-model').value},'ind-ollama'),
    post({section:'ollama',key:'timeout_fetch',   value:+document.getElementById('ollama-tfetch').value},'ind-ollama'),
    post({section:'ollama',key:'timeout_report',  value:+document.getElementById('ollama-treport').value},'ind-ollama'),
    post({section:'ollama',key:'timeout_edition', value:+document.getElementById('ollama-tedition').value},'ind-ollama'),
  ]);
}

function saveRss(){
  Promise.all([
    post({section:'rss',key:'max_articles_per_feed',value:+document.getElementById('rss-maxfeed').value},'ind-rss'),
    post({section:'rss',key:'live_window_hours',     value:+document.getElementById('rss-window').value},'ind-rss'),
  ]);
}

function saveRadio(){
  Promise.all([
    post({section:'radio',key:'news_per_bulletin',      value:+document.getElementById('radio-nbulletin').value},'ind-radio'),
    post({section:'radio',key:'news_interval_seconds',  value:+document.getElementById('radio-interval').value},'ind-radio'),
    post({section:'radio',key:'background_volume',      value:+document.getElementById('radio-bgvol').value},'ind-radio'),
    post({section:'radio',key:'bitrate',                value:document.getElementById('radio-bitrate').value},'ind-radio'),
  ]);
}

function saveWeb(){
  post({section:'web',key:'port',value:+document.getElementById('web-port').value},'ind-web');
}

function saveIcecast(){
  Promise.all([
    post({section:'icecast',key:'host',    value:document.getElementById('ic-host').value},'ind-icecast'),
    post({section:'icecast',key:'port',    value:+document.getElementById('ic-port').value},'ind-icecast'),
    post({section:'icecast',key:'mount',   value:document.getElementById('ic-mount').value},'ind-icecast'),
    post({section:'icecast',key:'password',value:document.getElementById('ic-password').value},'ind-icecast'),
  ]);
}

function savePosts(){
  Promise.all([
    post({section:'posts',key:'nb_posts_per_cycle',value:+document.getElementById('posts-nb').value},'ind-posts'),
    post({section:'posts',key:'window_hours',       value:+document.getElementById('posts-window').value},'ind-posts'),
  ]);
}

// ── Voix TTS ──────────────────────────────────────────────────────────────────
function toggleLang(lang, btn){
  var on = btn.classList.toggle('on');
  btn.classList.toggle('off', !on);
  btn.textContent = on ? '● ON' : '○ OFF';
  var grid = document.getElementById('vg-'+lang);
  if(grid) grid.classList.toggle('disabled', !on);
  if(!on){
    document.querySelectorAll('#vg-'+lang+' .voice-chip').forEach(c=>c.classList.remove('on'));
  } else {
    var first = document.querySelector('#vg-'+lang+' .voice-chip');
    if(first && !document.querySelector('#vg-'+lang+' .voice-chip.on'))
      first.classList.add('on');
  }
}

function toggleVoice(chip){
  chip.classList.toggle('on');
}

function saveVoices(){
  var result = {};
  document.querySelectorAll('.lang-block').forEach(function(block){
    var lang = block.dataset.lang;
    var on   = Array.from(block.querySelectorAll('.voice-chip.on')).map(c=>c.dataset.voice);
    if(on.length) result[lang] = on;
  });
  post({section:'radio',key:'voices',value:result},'ind-voices');
}

// ── Redémarrage ───────────────────────────────────────────────────────────────
function restartEngine(){
  var btn = document.getElementById('btn-restart');
  btn.textContent='⏳ En cours…'; btn.disabled=true;
  fetch('/config/restart',{method:'POST'})
    .then(r=>r.json())
    .then(d=>{
      btn.textContent = d.status==='ok' ? '✓ Redémarré' : '✗ Erreur';
      setTimeout(()=>{ btn.textContent='↺ Redémarrer'; btn.disabled=false; },3000);
    });
}
</script>

{{ global_script }}
</body></html>"""
