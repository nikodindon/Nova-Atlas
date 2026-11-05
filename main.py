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

from modules.core.scheduler import (
    FETCH_HOURS, REPORT_HOUR, REPORT_MIN, BUILD_HOUR, BUILD_MIN, EDITION_SCHEDULE,
    DEFAULT_POST_HOURS, ThreadRunner, news_tasks, radio_tasks, run_loop, validate_hours,
)

logger = logging.getLogger("nova.main")

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

def run_news_engine(config: dict, debug: bool = False, *, stop_event=None):
    """
    Scheduler news — vérifications à la minute, fenêtres historiques conservées.
    Attente interruptible ; tâches non-daemon drainées avant arrêt.
    """
    stop_event = stop_event or threading.Event()
    if stop_event.is_set():
        return
    # Sur Windows (spawn), le processus enfant repart de zéro :
    # il faut réinitialiser le logging pour que les logs apparaissent en console.
    setup_logging(debug)

    # Initialise Ollama pour ce processus
    from modules.core.llm_client      import init_ollama
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
    post_hours = validate_hours(config.get('radio', {}).get('post_hours', DEFAULT_POST_HOURS))
    runner = ThreadRunner(log=log)
    reload_deferred = False

    # ── Reload config à chaud ────────────────────────────────────────────────
    # Surveille un fichier flag créé par /config/restart
    # pour recharger la config sans tuer le processus
    import pathlib as _pl
    _reload_flag = _pl.Path(config.get('paths', {}).get('data_dir', 'data')) / '.reload_config'

    def _check_reload():
        nonlocal post_hours, config, _reload_flag, reload_deferred
        if _reload_flag.exists():
            if runner.active_count:
                if not reload_deferred:
                    log.info('Rechargement différé : traitements actifs')
                    reload_deferred = True
                return
            reload_deferred = False
            try:
                _reload_flag.unlink()
                from modules.core.llm_client import reload_ollama
                new_cfg = load_config("config/config.yaml")
                new_post_hours = validate_hours(new_cfg.get('radio', {}).get('post_hours', DEFAULT_POST_HOURS))
                # Recharge Ollama EN PREMIER (modèle + langue)
                reload_ollama(new_cfg)
                # Puis recharge tous les modules (chemins, timeouts, etc.)
                fetcher.reload_config(new_cfg)
                reporter.reload_config(new_cfg)
                editioner.reload_config(new_cfg)
                poster.reload_config(new_cfg)
                post_hours = new_post_hours
                config = new_cfg
                _reload_flag = _pl.Path(config.get('paths', {}).get('data_dir', 'data')) / '.reload_config'
                log.info("✅ [NewsEngine] Config rechargée — modèle=%s langue=%s",
                         new_cfg.get("llm",{}).get("model","?"),
                         new_cfg.get("service",{}).get("default_language","?"))
            except Exception as e:
                log.error(f"Erreur reload config : {e}", exc_info=True)

    def fetch_and_rebuild():
        try:
            new_count = fetcher.run(stop_event=stop_event)
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

    log.info("╔══════════════════════════════════════════════╗")
    log.info("║       Nova-Atlas — News Engine prêt         ║")
    log.info("║  Fetch        : toutes les 30 min           ║")
    log.info("║  Bulletins    : 2×/h (X:00, X:30)            ║")
    log.info("║  Éditions     : 06h · 12h · 19h             ║")
    log.info(f"║  Rapport      : {REPORT_HOUR:02d}h{REPORT_MIN:02d}                       ║")
    log.info("╚══════════════════════════════════════════════╝")

    def initial_fetch_and_rebuild():
        # Preserve the startup rebuild even when the initial fetch returns zero articles.
        try:
            fetcher.run(stop_event=stop_event)
            generate_static_site(config, full=False)
        except Exception as e:
            log.error(f"Fetch initial : {e}")

    run_loop(
        lambda now: news_tasks(now, post_hours),
        {'fetch': fetch_and_rebuild, 'report': rapport_and_rebuild,
         'edition': edition_and_rebuild, 'posts': posts_job,
         'rebuild': lambda: generate_static_site(config, full=True)},
        stop_event, runner=runner, clock=lambda: datetime.now(),
        before_tick=lambda now: _check_reload(), startup=initial_fetch_and_rebuild,
    )


# ─── PROCESSUS : RADIO ────────────────────────────────────────────────────────

def run_radio(config: dict, debug: bool = False, *, stop_event=None):
    """
    Streamers daemon ; génération bulletin non-daemon, une seule à la fois.
    Le scheduler attend les bulletins actifs avant l'arrêt des streamers.

    Le declenchement des bulletins est assure par le scheduler
    integre (cf bulletins.yaml post_hours : 5 min avant chaque :30
    et :00 de 7h a 21h30). Avant 2026-07-28 il y avait un
    NewsWatcher qui declenchait un bulletin quand N nouvelles
    news arrivaient, mais ce systeme a ete supprime car le
    scheduler par heure suffit et est plus previsible.

    Le config.yaml de la radio est déjà dans config (sections icecast, radio, tts).
    Le fichier messages.yaml (intros, transitions, outros) est lu par bulletin_generator
    depuis config/messages.yaml — chemin résolu par le module radio.
    """
    stop_event = stop_event or threading.Event()
    if stop_event.is_set():
        return
    setup_logging(debug)
    from modules.radio.streamer        import Streamer, AndroidStreamer
    from modules.radio.bulletin_generator import BulletinGenerator, get_recent_articles
    from pathlib import Path as _P
    log = logging.getLogger("nova.radio")
    log.info("Initialisation radio...")

    streamer = Streamer(config)
    # 2e streamer dédié pour le client Android : bulletins en boucle
    # sur le mount /nova-android (cf config.yaml icecast.android_mount)
    android_streamer = AndroidStreamer(config)
    # BulletinGenerator remplace l'ancien JournalBuilder
    _proj = _P(__file__).parent.resolve()
    radio_paths = {
        "root":             _proj,
        "data":             _proj / "data",
        "articles":         _proj / "data" / "articles",
        "audio_queue":      _proj / "audio_queue",
        "tmp":              _proj / "tmp",
        "background_music":  _proj / "background_music",
    }
    builder = BulletinGenerator(config, radio_paths)

    def generate_bulletin():
        """Génère un bulletin avec les articles des 30 dernières minutes."""
        try:
            from modules.radio.bulletin_generator import load_bulletins_config
            bulletins_cfg = load_bulletins_config(radio_paths)
            window = bulletins_cfg.get("window_minutes", 30)
            articles = get_recent_articles(radio_paths, window_minutes=window)
            # Cap d'articles lu depuis config radio. Avant hardcode (20, puis 40),
            # maintenant parametre configurable (defaut 40).
            max_articles = config.get("radio", {}).get("max_articles_per_bulletin", 40)
            articles = articles[:max_articles]
            log.info(f"📡 Bulletin radio: {len(articles)} articles des {window} dernières min (cap {max_articles})")
            path = builder.build(articles)
            if path:
                # Cache Android before the primary can consume/delete the MP3.
                android_streamer.enqueue_bulletin(path)
                streamer.enqueue_bulletin(path)
                log.info(f"📥 Bulletin en file: {path.name}")
            else:
                log.info("⏭️ Bulletin skippé (pas assez d'articles ou erreur)")
        except Exception as e:
            log.error(f"Erreur bulletin : {e}", exc_info=True)

    # Lance les 2 workers principaux (Streamer + BulletinGen via scheduler)
    stream_threads = [threading.Thread(target=streamer.run, daemon=True, name="Streamer"),
                      threading.Thread(target=android_streamer.run, daemon=True, name="AndroidStreamer")]
    for thread in stream_threads:
        thread.start()

    ic = config.get("icecast", {})
    log.info(f"✅ Radio → http://localhost:{ic.get('port',8000)}{ic.get('mount','/nova')}")
    log.info(f"✅ Android → http://localhost:{ic.get('port',8000)}{ic.get('android_mount','/nova-android')}")

    # Keep :25/:55 pre-generation, using the configured TARGET hour.
    post_hours = ()
    last_heartbeat = None
    def refresh_radio_schedule(now):
        nonlocal post_hours, last_heartbeat
        from modules.radio.bulletin_generator import load_bulletins_config
        try:
            post_hours = validate_hours(load_bulletins_config(radio_paths).get('post_hours', []))
        except Exception:
            log.warning('Calendrier radio invalide : dernières heures valides conservées')
        current = time.monotonic()
        if last_heartbeat is None or current - last_heartbeat >= 300:
            log.info('⏰ Scheduler radio actif, post_hours=%s', post_hours)
            last_heartbeat = current

    try:
        run_loop(lambda now: radio_tasks(now, post_hours), {'bulletin': generate_bulletin},
                 stop_event, runner=ThreadRunner(log=log), clock=lambda: datetime.now(),
                 before_tick=refresh_radio_schedule)
    finally:
        # run_loop drains bulletin work first; stop audio only after its final enqueue.
        streamer.stop()
        android_streamer.stop()
        for thread in stream_threads:
            thread.join()


# ─── PROCESSUS : WEB SERVER ───────────────────────────────────────────────────

def run_web_server(config: dict, port_override: int = None, debug: bool = False, *, stop_event=None):
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
    if stop_event is not None and stop_event.is_set():
        return
    run_server(config, host=host, port=port, stop_event=stop_event)


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
    from modules.core.llm_client  import init_ollama
    from modules.web.atlas_web import generate_static_site
    init_ollama(config)
    logger.info("🔨 Rebuild complet...")
    generate_static_site(config, full=True)
    logger.info("✅ Site généré.")

def cmd_fetch(config):
    from modules.core.llm_client       import init_ollama
    from modules.fetch.atlas_fetch import ArticleFetcher
    from modules.web.atlas_web     import generate_static_site
    init_ollama(config)
    logger.info("📡 Cycle RSS...")
    ArticleFetcher(config).run()
    generate_static_site(config, full=False)
    logger.info("✅ Collecte terminée.")

def cmd_edition(config, edition_name):
    from modules.core.llm_client             import init_ollama
    from modules.editions.atlas_editions import EditionGenerator
    from modules.web.atlas_web           import generate_static_site
    init_ollama(config)
    logger.info(f"📰 Édition : {edition_name}")
    out = EditionGenerator(config).generate(edition_name)
    if out:
        generate_static_site(config, full=False)
        logger.info(f"✅ → {out}")

def cmd_report(config, day=None):
    from modules.core.llm_client      import init_ollama
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

def _scheduler_process(fn, args, stop_event):
    def request_stop(sig, frame):
        stop_event.set()
    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, request_stop)
    fn(*args, stop_event=stop_event)


def _start_process(fn, name: str, *args) -> multiprocessing.Process:
    if fn in (run_news_engine, run_radio, run_web_server):
        stop_event = multiprocessing.Event()
        p = multiprocessing.Process(target=_scheduler_process, args=(fn, args, stop_event),
                                    name=name, daemon=True)
        p.scheduler_stop_event = stop_event
    else:
        p = multiprocessing.Process(target=fn, args=args, name=name, daemon=True)
    p.start()
    logger.info(f"▶  '{name}' démarré (pid={p.pid})")
    return p


def _stop_process(process):
    event = getattr(process, 'scheduler_stop_event', None)
    if event is not None:
        event.set()
        process.join()  # Preserve active jobs: no forced timeout/terminate on normal stop.
    elif process.is_alive():
        process.terminate()
        process.join(timeout=5)


def main():
    args = parse_args()
    setup_logging(args.debug)

    config       = load_config(args.config)
    from modules.persistence import runtime_mode, StorageError
    try:runtime_mode(config)
    except StorageError:
        logger.error("PostgreSQL business mode is not enabled; use the explicit snapshot CLI")
        raise SystemExit(78)
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
    stopping = False
    starting = True
    stop_requested = False
    restart_requested = False
    def shutdown(sig, frame):
        nonlocal stopping, stop_requested
        if starting:
            stop_requested = True
            return
        if stopping:
            return
        stopping = True
        logger.info("\n⛔ Arrêt : attente des traitements actifs...")
        for p in processes:
            event = getattr(p, 'scheduler_stop_event', None)
            if event is not None:
                event.set()
        for p in processes:
            _stop_process(p)
        sys.exit(0)

    # Handle Docker SIGTERM before the first child can start work.
    signal.signal(signal.SIGINT, shutdown)
    if sys.platform != 'win32':
        signal.signal(signal.SIGTERM, shutdown)
    for fn, name, fn_args in proc_defs:
        if stop_requested:
            break
        processes.append(_start_process(fn, name, *fn_args))
        time.sleep(0.5)
    starting = False
    if stop_requested:
        shutdown(signal.SIGTERM, None)

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

    def request_restart(sig, frame):
        nonlocal restart_requested
        if not stopping:
            restart_requested = True

    def restart_all():
        """Executed serially in the supervisor loop, never inside a signal handler."""
        logger.info("🔄 Redémarrage demandé via SIGUSR1 — rechargement config...")
        # Recharge la config depuis le disque
        new_config = load_config(args.config)
        for i, (fn, name, fn_args) in enumerate(proc_defs):
            p = processes[i]
            if p.is_alive():
                logger.info(f"   Arrêt '{name}'...")
                _stop_process(p)
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
            signal.signal(signal.SIGUSR1, request_restart)
            logger.info("💡 Redémarrage à distance disponible via POST /config/restart")

    # Watchdog : relance un processus crashé
    while True:
        time.sleep(15)
        if restart_requested:
            restart_requested = False
            restart_all()
            continue
        for i, (fn, name, fn_args) in enumerate(proc_defs):
            if not processes[i].is_alive():
                logger.warning(f"⚠️  '{name}' mort (exit={processes[i].exitcode}) — relance...")
                current_config = load_config(args.config)
                current_args = (current_config,) + fn_args[1:]
                processes[i] = _start_process(fn, name, *current_args)
                proc_defs[i] = (fn, name, current_args)


if __name__ == "__main__":
    multiprocessing.freeze_support()  # Windows
    main()
