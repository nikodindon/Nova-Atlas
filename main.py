#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py — Point d'entrée principal de Nova-Atlas

Modes de lancement :
  python main.py               — tout démarrer (news engine + radio + web)
  python main.py --all         — idem explicite
  python main.py --news        — moteur de news seul
  python main.py --radio       — radio seule
  python main.py --web         — serveur web seul

Commandes one-shot (quittent après exécution) :
  python main.py --build       — rebuild site statique complet
  python main.py --fetch       — un cycle de collecte RSS immédiat
  python main.py --edition [matin|midi|soir|auto]
  python main.py --report [YYYYMMDD]

Options :
  --debug          Active les logs DEBUG
  --config <path>  Chemin vers config.yaml (défaut: config/config.yaml)
  --port <int>     Override port web
"""

import argparse
import logging
import multiprocessing
import signal
import sys
import threading
import time
from datetime import datetime

from modules.core.config import load_config, get_service_name

logger = logging.getLogger("nova.main")

# ─── SCHEDULER CONSTANTS (pattern atlas.py) ───────────────────────────────────

FETCH_HOURS      = list(range(5, 24)) + list(range(0, 5))
REPORT_HOUR      = 23
REPORT_MIN       = 0
BUILD_HOUR       = 23
BUILD_MIN        = 30
EDITION_SCHEDULE = [(6, "matin"), (12, "midi"), (19, "soir")]

# ─── ARGS & LOGGING ───────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Nova-Atlas — AI News Engine & Radio",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--all",     action="store_true")
    p.add_argument("--news",    action="store_true")
    p.add_argument("--radio",   action="store_true")
    p.add_argument("--web",     action="store_true")
    p.add_argument("--build",   action="store_true")
    p.add_argument("--fetch",   action="store_true")
    p.add_argument("--edition", nargs="?", const="auto",
                   metavar="matin|midi|soir|auto")
    p.add_argument("--report",  nargs="?", const="today", metavar="YYYYMMDD")
    p.add_argument("--cleanup", action="store_true",
                   help="Supprime les articles sans résumé du JSON du jour puis continue")
    p.add_argument("--dry-run", action="store_true",
                   help="Avec --cleanup : affiche ce qui serait supprimé sans écrire")
    p.add_argument("--debug",   action="store_true")
    p.add_argument("--config",  default="config/config.yaml")
    p.add_argument("--port",    type=int, default=None)
    return p.parse_args()


def setup_logging(debug: bool):
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)-22s — %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("nova.log", encoding="utf-8"),
        ],
    )

# ─── PROCESSUS : NEWS ENGINE ──────────────────────────────────────────────────

def run_news_engine(config: dict, debug: bool = False):
    """
    Scheduler news engine — boucle 60s, pattern atlas.py.
    Chaque tâche est lancée dans un thread daemon.
    """
    # Sur Windows (spawn), le processus enfant repart de zéro :
    # il faut réinitialiser le logging pour que les logs apparaissent en console.
    setup_logging(debug)

    # Initialise Ollama pour ce processus
    from modules.core.ollama      import init_ollama
    from modules.fetch.atlas_fetch       import ArticleFetcher
    from modules.report.atlas_report     import ReportGenerator
    from modules.editions.atlas_editions import EditionGenerator
    from modules.posts.atlas_posts       import PostsGenerator
    from modules.web.atlas_web           import generate_static_site

    init_ollama(config)

    log = logging.getLogger("nova.news")

    fetcher   = ArticleFetcher(config)
    reporter  = ReportGenerator(config)
    editioner = EditionGenerator(config)
    poster    = PostsGenerator(config)

    post_hours = config.get("radio", {}).get("post_hours",
                 [7, 9, 11, 13, 15, 17, 19, 21])

    # ── Reload config à chaud ────────────────────────────────────────────────
    # Surveille un fichier flag créé par /config/restart
    # pour recharger la config sans tuer le processus
    import pathlib as _pl
    _reload_flag = _pl.Path("data/.reload_config")

    def _check_reload():
        nonlocal post_hours
        if _reload_flag.exists():
            try:
                _reload_flag.unlink()
                from modules.core.ollama import reload_ollama
                new_cfg = load_config("config/config.yaml")
                # Recharge Ollama EN PREMIER (modèle + langue)
                reload_ollama(new_cfg)
                # Puis recharge tous les modules (chemins, timeouts, etc.)
                fetcher.reload_config(new_cfg)
                reporter.reload_config(new_cfg)
                editioner.reload_config(new_cfg)
                poster.reload_config(new_cfg)
                post_hours = new_cfg.get("radio", {}).get(
                    "post_hours", [7, 9, 11, 13, 15, 17, 19, 21])
                log.info("✅ [NewsEngine] Config rechargée — modèle=%s langue=%s",
                         new_cfg.get("llm",{}).get("model","?"),
                         new_cfg.get("service",{}).get("default_language","?"))
            except Exception as e:
                log.error(f"Erreur reload config : {e}", exc_info=True)

    # Suivi des slots (pattern atlas.py)
    last_fetch_slot  = ""
    last_report_day  = ""
    last_build_day   = ""
    last_post_hour   = -1
    last_edition_day: dict = {}
    active_threads:   dict = {}

    def run_in_thread(name: str, fn, *args):
        t = active_threads.get(name)
        if t and t.is_alive():
            log.debug(f"[SKIP] {name} déjà en cours")
            return
        t = threading.Thread(target=fn, args=args, daemon=True, name=name)
        active_threads[name] = t
        t.start()

    def fetch_and_rebuild():
        try:
            new_count = fetcher.run()
            if new_count and new_count > 0:
                generate_static_site(config, full=False)
                log.info(f"[WEB] Site régénéré ({new_count} nouveaux articles)")
        except Exception as e:
            log.error(f"Erreur fetch : {e}", exc_info=True)

    def edition_and_rebuild(ed_name: str, today: str):
        try:
            out = editioner.generate(ed_name, today)
            if out:
                generate_static_site(config, full=False)
                log.info(f"[EDITION] {ed_name} → {out}")
        except Exception as e:
            log.error(f"Erreur édition {ed_name} : {e}", exc_info=True)

    def rapport_and_rebuild(today: str):
        try:
            out = reporter.generate(today)
            if out:
                log.info(f"[REPORT] → {out}")
        except Exception as e:
            log.error(f"Erreur rapport : {e}", exc_info=True)

    def posts_job(today: str):
        try:
            poster.generate(today)
        except Exception as e:
            log.error(f"Erreur posts : {e}", exc_info=True)

    log.info("╔══════════════════════════════════════╗")
    log.info("║    Nova-Atlas — News Engine prêt     ║")
    log.info("║  Fetch        : toutes les 30 min    ║")
    log.info("║  Éditions     : 06h · 12h · 19h     ║")
    log.info(f"║  Rapport      : {REPORT_HOUR:02d}h{REPORT_MIN:02d}                ║")
    log.info("╚══════════════════════════════════════╝")

    log.info("Fetch initial au démarrage...")
    try:
        fetcher.run()
        generate_static_site(config, full=False)
    except Exception as e:
        log.error(f"Fetch initial : {e}")

    while True:
        now   = datetime.now()
        h, m  = now.hour, now.minute
        today = now.strftime("%Y%m%d")

        # Vérifie si un reload config a été demandé
        _check_reload()

        # Fetch RSS toutes les 30 min
        current_slot = f"{h:02d}:{(m // 30) * 30:02d}"
        if h in FETCH_HOURS and m % 30 < 2 and current_slot != last_fetch_slot:
            last_fetch_slot = current_slot
            log.info(f"[FETCH] Cycle {current_slot}")
            run_in_thread(f"fetch_{current_slot}", fetch_and_rebuild)

        # Rapport quotidien à 23h00
        if (h == REPORT_HOUR and REPORT_MIN <= m < REPORT_MIN + 5
                and last_report_day != today):
            last_report_day = today
            log.info(f"[REPORT] Génération rapport {today}")
            run_in_thread("rapport", rapport_and_rebuild, today)

        # Rebuild site à 23h30
        if (h == BUILD_HOUR and BUILD_MIN <= m < BUILD_MIN + 5
                and last_build_day != today):
            last_build_day = today
            log.info("[WEB] Rebuild post-rapport")
            run_in_thread("rebuild", generate_static_site, config, True)

        # Posts réseaux sociaux
        if h in post_hours and m < 3 and h != last_post_hour:
            last_post_hour = h
            log.info(f"[POSTS] Génération {h:02d}h00")
            run_in_thread(f"posts_{h}", posts_job, today)

        # Éditions Matin / Midi / Soir
        for ed_hour, ed_name in EDITION_SCHEDULE:
            key = f"{today}_{ed_name}"
            if h == ed_hour and m < 5 and key not in last_edition_day:
                last_edition_day[key] = True
                log.info(f"[EDITION] Génération {ed_name}")
                run_in_thread(f"edition_{ed_name}", edition_and_rebuild, ed_name, today)

        time.sleep(60)


# ─── PROCESSUS : RADIO ────────────────────────────────────────────────────────

def run_radio(config: dict, debug: bool = False):
    """
    3 threads daemon :
      NewsWatcher  — surveille les JSON d'articles
      BulletinGen  — TTS + mix audio à la demande
      Streamer     — flux Icecast continu via pipe ffmpeg

    Le config.yaml de la radio est déjà dans config (sections icecast, radio, tts).
    Le fichier messages.yaml (intros, transitions, outros) est lu par journal_builder
    depuis config/messages.yaml — chemin résolu par le module radio.
    """
    setup_logging(debug)
    from modules.radio.news_watcher    import NewsWatcher
    from modules.radio.journal_builder import JournalBuilder
    from modules.radio.streamer        import Streamer

    log = logging.getLogger("nova.radio")
    log.info("Initialisation radio...")

    streamer = Streamer(config)
    builder  = JournalBuilder(config)

    def on_bulletin_ready(articles: list):
        def _gen():
            try:
                path = builder.build(articles)
                if path:
                    streamer.enqueue_bulletin(path)
                else:
                    log.error("Échec génération bulletin")
            except Exception as e:
                log.error(f"Erreur bulletin : {e}", exc_info=True)
        threading.Thread(target=_gen, daemon=True, name="BulletinGen").start()

    watcher = NewsWatcher(config.get("radio", {}), on_bulletin_ready)
    threading.Thread(target=watcher.run,  daemon=True, name="NewsWatcher").start()
    threading.Thread(target=streamer.run, daemon=True, name="Streamer").start()

    ic = config.get("icecast", {})
    log.info(f"✅ Radio → http://localhost:{ic.get('port',8000)}{ic.get('mount','/nova')}")

    while True:
        time.sleep(1)


# ─── PROCESSUS : WEB SERVER ───────────────────────────────────────────────────

def run_web_server(config: dict, port_override: int = None, debug: bool = False):
    setup_logging(debug)
    from modules.web.atlas_web import run_server, generate_static_site

    log = logging.getLogger("nova.web")
    web_cfg = config.get("web", {})
    host    = web_cfg.get("host", "0.0.0.0")
    port    = port_override or int(web_cfg.get("port", 5055))

    log.info("Génération site statique (incrémental)...")
    try:
        generate_static_site(config, full=False)
    except Exception as e:
        log.warning(f"Génération site : {e}")

    log.info(f"✅ Web → http://localhost:{port}/")
    run_server(config, host=host, port=port)


# ─── ONE-SHOT COMMANDS ────────────────────────────────────────────────────────

def cmd_cleanup(config, dry_run: bool = False):
    from modules.fetch.atlas_fetch import ArticleFetcher
    fetcher = ArticleFetcher(config)
    removed = fetcher.cleanup(dry_run=dry_run)
    if removed and not dry_run:
        # Rebuild le site pour refléter le nettoyage
        from modules.web.atlas_web import generate_static_site
        generate_static_site(config, full=False)


def cmd_build(config):
    from modules.core.ollama  import init_ollama
    from modules.web.atlas_web import generate_static_site
    init_ollama(config)
    logger.info("🔨 Rebuild complet...")
    generate_static_site(config, full=True)
    logger.info("✅ Site généré.")

def cmd_fetch(config):
    from modules.core.ollama       import init_ollama
    from modules.fetch.atlas_fetch import ArticleFetcher
    from modules.web.atlas_web     import generate_static_site
    init_ollama(config)
    logger.info("📡 Cycle RSS...")
    ArticleFetcher(config).run()
    generate_static_site(config, full=False)
    logger.info("✅ Collecte terminée.")

def cmd_edition(config, edition_name):
    from modules.core.ollama             import init_ollama
    from modules.editions.atlas_editions import EditionGenerator
    from modules.web.atlas_web           import generate_static_site
    init_ollama(config)
    logger.info(f"📰 Édition : {edition_name}")
    out = EditionGenerator(config).generate(edition_name)
    if out:
        generate_static_site(config, full=False)
        logger.info(f"✅ → {out}")

def cmd_report(config, day=None):
    from modules.core.ollama      import init_ollama
    from modules.report.atlas_report import ReportGenerator
    from modules.web.atlas_web       import generate_static_site
    init_ollama(config)
    target = day or datetime.now().strftime("%Y%m%d")
    logger.info(f"📋 Rapport : {target}")
    out = ReportGenerator(config).generate(target)
    if out:
        generate_static_site(config, full=False)
        logger.info(f"✅ → {out}")

# ─── ORCHESTRATEUR ────────────────────────────────────────────────────────────

def _start_process(fn, name: str, *args) -> multiprocessing.Process:
    p = multiprocessing.Process(target=fn, args=args, name=name, daemon=True)
    p.start()
    logger.info(f"▶  '{name}' démarré (pid={p.pid})")
    return p


def main():
    args = parse_args()
    setup_logging(args.debug)

    config       = load_config(args.config)
    service_name = get_service_name(config)

    logger.info("=" * 68)
    logger.info(f"🚀  {service_name}")
    if args.debug:
        logger.info("🐛  MODE DEBUG")
    logger.info("=" * 68)

    # ── One-shot ──────────────────────────────────────────────────────────────
    if args.cleanup:
        cmd_cleanup(config, dry_run=getattr(args, "dry_run", False))
        # --cleanup peut précéder un mode service ou quitter seul
        if not (args.all or args.news or args.radio or args.web
                or args.build or args.fetch
                or args.edition is not None or args.report is not None):
            return  # seul → on quitte après nettoyage

    if args.build:
        cmd_build(config); return
    if args.fetch:
        cmd_fetch(config); return
    if args.edition is not None:
        cmd_edition(config, args.edition); return
    if args.report is not None:
        cmd_report(config, None if args.report == "today" else args.report)
        return

    # ── Mode service ──────────────────────────────────────────────────────────
    run_all  = args.all or not (args.news or args.radio or args.web)
    do_news  = run_all or args.news
    do_radio = run_all or args.radio
    do_web   = run_all or args.web

    debug = args.debug
    proc_defs = []
    if do_news:  proc_defs.append((run_news_engine, "NewsEngine", (config, debug)))
    if do_radio: proc_defs.append((run_radio,       "Radio",      (config, debug)))
    if do_web:   proc_defs.append((run_web_server,  "WebServer",  (config, args.port, debug)))

    processes = []
    for fn, name, fn_args in proc_defs:
        processes.append(_start_process(fn, name, *fn_args))
        time.sleep(0.5)

    logger.info("=" * 68)
    if do_news:
        logger.info("📰  News engine  : fetch 30 min · éditions 06/12/19h · rapport 23h")
    if do_radio:
        ic = config.get("icecast", {})
        logger.info(f"📻  Radio        : http://localhost:{ic.get('port',8000)}{ic.get('mount','/nova')}")
    if do_web:
        wc = config.get("web", {})
        logger.info(f"🌐  Web          : http://localhost:{args.port or wc.get('port',5055)}/")
    logger.info("=" * 68)
    logger.info("Ctrl+C pour arrêter")

    def shutdown(sig, frame):
        logger.info("\n⛔ Arrêt...")
        for p in processes:
            if p.is_alive():
                p.terminate()
        sys.exit(0)

    def restart_all(sig, frame):
        """Redémarre tous les processus (déclenché par SIGUSR1 depuis /config/restart)."""
        logger.info("🔄 Redémarrage demandé via SIGUSR1 — rechargement config...")
        # Recharge la config depuis le disque
        new_config = load_config(args.config)
        for i, (fn, name, fn_args) in enumerate(proc_defs):
            p = processes[i]
            if p.is_alive():
                logger.info(f"   Arrêt '{name}'...")
                p.terminate()
                p.join(timeout=5)
            # Relance avec la nouvelle config
            new_args = (new_config,) + fn_args[1:]  # remplace config, garde le reste
            new_p = _start_process(fn, name, *new_args)
            processes[i] = new_p
            proc_defs[i] = (fn, name, new_args)
            time.sleep(0.5)
        logger.info("✅ Tous les processus relancés avec la nouvelle configuration.")

    signal.signal(signal.SIGINT, shutdown)
    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, shutdown)
        if hasattr(signal, 'SIGUSR1'):
            signal.signal(signal.SIGUSR1, restart_all)
            logger.info("💡 Redémarrage à distance disponible via POST /config/restart")

    # Watchdog : relance un processus crashé
    while True:
        time.sleep(15)
        for i, (fn, name, fn_args) in enumerate(proc_defs):
            if not processes[i].is_alive():
                logger.warning(f"⚠️  '{name}' mort (exit={processes[i].exitcode}) — relance...")
                processes[i] = _start_process(fn, name, *fn_args)


if __name__ == "__main__":
    multiprocessing.freeze_support()  # Windows
    main()
