"""
modules/radio/streamer.py — Nova Media
Architecture : UN SEUL process ffmpeg permanent + pipe stdin
Supporte fadeout fluide entre musique et bulletins.
"""

import logging
import random
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from queue import Empty, Queue

logger = logging.getLogger("nova.streamer")

FADE_DURATION      = 3.0
CHUNK_SIZE         = 32768
HEARTBEAT_INTERVAL = 0.5

_SILENCE_BYTES: bytes = b""


def _generate_silence(sample_rate: int, bitrate: str, duration: float = 1.0) -> bytes:
    """Génère du silence MP3 pour le heartbeat."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl=stereo",
        "-t", str(duration),
        "-b:a", bitrate,
        "-codec:a", "libmp3lame",
        "-f", "mp3",
        "pipe:1"
    ]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=10).stdout
    except Exception:
        return b""


class Streamer:
    def __init__(self, config: dict):
        radio = config.get("radio", {})
        ice   = config.get("icecast", {})
        paths = config.get("paths", {})

        self.music_dir   = Path(paths.get("music", "music"))
        self.queue_dir   = Path(paths.get("audio_queue", "audio_queue"))
        self.bitrate     = radio.get("bitrate", "128k")
        self.sample_rate = radio.get("sample_rate", 44100)
        self.channels    = radio.get("channels", 2)
        self._debug      = config.get("_debug", False)

        self.icecast_url = (
            f"icecast://{ice.get('user', 'source')}:{ice.get('password', 'hackme')}"
            f"@{ice.get('host', 'localhost')}:{ice.get('port', 8000)}{ice.get('mount', '/nova')}"
        )

        self._stop_event      = threading.Event()
        self._fade_requested  = threading.Event()
        self._streaming_event = threading.Event()
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._play_queue: Queue = Queue()
        self._lock = threading.Lock()

        self._fade_cache: bytes | None = None
        self._fade_cache_ready = threading.Event()

        self.queue_dir.mkdir(parents=True, exist_ok=True)

        global _SILENCE_BYTES
        _SILENCE_BYTES = _generate_silence(self.sample_rate, self.bitrate)
        if _SILENCE_BYTES:
            logger.info("🔇 Buffer de silence initialisé")
        else:
            logger.warning("⚠️ Impossible de générer le buffer de silence")

    # ------------------------------------------------------------------ #
    #  API publique                                                        #
    # ------------------------------------------------------------------ #

    def enqueue_bulletin(self, path: Path):
        """Ajoute un bulletin dans la file d'attente."""
        if path and path.exists():
            self._play_queue.put(path)
            logger.info(f"📥 Journal en file : {path.name}")
            self._fade_requested.set()

    def run(self):
        """Boucle principale du streamer."""
        logger.info("📻 Démarrage du streamer")
        self._start_ffmpeg()

        heartbeat = threading.Thread(target=self._heartbeat, daemon=True, name="Heartbeat")
        heartbeat.start()

        try:
            while not self._stop_event.is_set():
                self._play_next()
        except Exception as e:
            logger.error(f"Erreur streamer : {e}", exc_info=True)
        finally:
            self._kill_ffmpeg()

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------ #
    #  Heartbeat & maintenance                                             #
    # ------------------------------------------------------------------ #

    def _heartbeat(self):
        while not self._stop_event.is_set():
            if self._ffmpeg_proc and self._ffmpeg_proc.poll() is not None:
                logger.warning("⚠️ ffmpeg est mort → relance")
                self._start_ffmpeg()
            if not self._streaming_event.is_set():
                self._write_to_pipe(_SILENCE_BYTES)
            time.sleep(HEARTBEAT_INTERVAL)

    # ------------------------------------------------------------------ #
    #  Logique de lecture                                                  #
    # ------------------------------------------------------------------ #

    def _play_next(self):
        try:
            bulletin = self._play_queue.get_nowait()
            logger.info(f"🎙️ Diffusion du journal : {bulletin.name}")
            self._stream_file(bulletin, is_music=False)
            bulletin.unlink(missing_ok=True)
        except Empty:
            music = self._pick_music()
            if music:
                if self._fade_requested.is_set():
                    self._stream_music_with_intro_fade(music)
                else:
                    logger.info(f"🎵 Musique : {music.name}")
                    self._stream_file(music, is_music=True)
            else:
                logger.warning("⚠️ Aucune musique trouvée")
                time.sleep(5)

    def _pick_music(self) -> Path | None:
        if not self.music_dir.exists():
            return None
        files = list(self.music_dir.glob("*.mp3"))
        return random.choice(files) if files else None

    # ------------------------------------------------------------------ #
    #  Fade + intro musicale courte                                        #
    # ------------------------------------------------------------------ #

    def _stream_music_with_intro_fade(self, music_path: Path):
        logger.info(f"🎵 Intro musicale avant journal : {music_path.name}")
        self._fade_requested.clear()
        INTRO = 15.0
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir=self.queue_dir)
        tmp.close()
        out_path = Path(tmp.name)

        cmd = [
            "ffmpeg", "-y", "-i", str(music_path),
            "-vn", "-map", "0:a",
            "-t", str(INTRO + FADE_DURATION),
            "-af", f"afade=t=out:st={INTRO:.2f}:d={FADE_DURATION:.2f}",
            "-ar", str(self.sample_rate), "-ac", str(self.channels),
            "-b:a", self.bitrate, "-codec:a", "libmp3lame",
            str(out_path)
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60)
            if r.returncode == 0:
                self._stream_file(out_path, is_music=False)
            else:
                logger.warning("Intro fade échouée")
        except Exception as e:
            logger.warning(f"Erreur intro fade : {e}")
        finally:
            out_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    #  Streaming principal                                                 #
    # ------------------------------------------------------------------ #

    def _stream_file(self, path: Path, is_music: bool = True):
        if self._ffmpeg_proc is None or self._ffmpeg_proc.poll() is not None:
            self._start_ffmpeg()
            time.sleep(1)

        transcode_cmd = [
            "ffmpeg", "-y", "-i", str(path),
            "-vn", "-map", "0:a",
            "-ar", str(self.sample_rate), "-ac", str(self.channels),
            "-b:a", self.bitrate,
            "-codec:a", "libmp3lame",
            "-f", "mp3",
            "pipe:1"
        ]

        try:
            transcode = subprocess.Popen(
                transcode_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE if self._debug else subprocess.DEVNULL,
            )

            self._streaming_event.set()
            start_time = time.monotonic()

            while not self._stop_event.is_set():
                if is_music and self._fade_requested.is_set():
                    # Gestion du fadeout (ton ancienne logique)
                    elapsed = time.monotonic() - start_time
                    logger.info(f"Fadeout demandé après {elapsed:.1f}s")
                    # ... (tu peux réintégrer ta logique _prebuild_fadeout ici si tu veux)
                    self._fade_requested.clear()
                    break

                chunk = transcode.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                self._write_to_pipe(chunk)

            transcode.stdout.close()
            transcode.wait(timeout=5)

        except Exception as e:
            logger.error(f"Erreur streaming {path.name} : {e}")
        finally:
            self._streaming_event.clear()

    def _write_to_pipe(self, data: bytes) -> bool:
        if not data or not self._ffmpeg_proc or self._ffmpeg_proc.stdin is None:
            return False
        try:
            self._ffmpeg_proc.stdin.write(data)
            self._ffmpeg_proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            logger.warning("Pipe cassé → relance ffmpeg")
            self._start_ffmpeg()
            return False

    def _start_ffmpeg(self):
        with self._lock:
            self._kill_ffmpeg()
            cmd = [
                "ffmpeg", "-re",
                "-probesize", "32", "-analyzeduration", "0",
                "-f", "mp3", "-i", "pipe:0",
                "-vn", "-map", "0:a",
                "-codec:a", "libmp3lame",
                "-b:a", self.bitrate,
                "-ar", str(self.sample_rate), "-ac", str(self.channels),
                "-f", "mp3", "-content_type", "audio/mpeg",
                "-ice_name", "Nova Media",
                self.icecast_url,
            ]
            self._ffmpeg_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if self._debug else subprocess.DEVNULL,
            )
            logger.info("🔗 ffmpeg connecté à Icecast")

    def _kill_ffmpeg(self):
        if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
            try:
                if self._ffmpeg_proc.stdin:
                    self._ffmpeg_proc.stdin.close()
                self._ffmpeg_proc.terminate()
                self._ffmpeg_proc.wait(timeout=5)
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────
#  AndroidStreamer : stream "bulletin en boucle" pour l'app Android
class AndroidStreamer:
    """
    Streamer dédié pour le client Android : joue le bulletin courant
    en boucle. Quand un nouveau bulletin est prêt, attend la fin du
    passage courant (jusqu'à la fin du fichier), puis enchaîne sur
    le nouveau (sans blanc, sans jingle).
    """

    def __init__(self, config: dict):
        ice   = config.get("icecast", {})
        radio = config.get("radio", {})

        # Mount spécifique Android (par défaut /nova-android)
        android_mount = ice.get("android_mount", "/nova-android")
        self.bitrate     = radio.get("bitrate", "128k")
        self.sample_rate = radio.get("sample_rate", 44100)
        self.channels    = radio.get("channels", 2)
        self._debug      = config.get("_debug", False)

        self.icecast_url = (
            f"icecast://{ice.get('user', 'source')}:{ice.get('password', 'hackme')}"
            f"@{ice.get('host', 'localhost')}:{ice.get('port', 8000)}{android_mount}"
        )

        self._stop_event     = threading.Event()
        self._ffmpeg_proc    = None
        self._lock           = threading.Lock()
        # Bulletin "courant" (celui qui est en train de boucler).
        # None tant qu'aucun bulletin n'a été enqueue.
        self._current: Path | None = None
        # Nouveau bulletin qui attend la fin du courant
        self._pending: Path | None = None
        # Event levé quand le passage courant finit (entre 2 loops)
        self._current_done = threading.Event()
        self._current_done.set()  # rien en cours au début
        # Buffer de silence : on l'envoie à ffmpeg quand il n'y a
        # pas de bulletin, pour que le mount Icecast existe (sinon
        # Icecast ne liste pas le mount tant qu'aucune donnée n'est envoyée).
        self._silence_bytes = self._generate_silence()

    def enqueue_bulletin(self, path: Path):
        """Le scheduler appelle ça avec le nouveau bulletin."""
        if path and path.exists():
            # Si rien n'est en cours de diffusion, on devient le courant direct
            if self._current is None:
                self._current = path
                self._current_done.set()
                logger.info(f"📥 [android] Premier bulletin : {path.name}")
            else:
                # Sinon, on attend que le courant finisse
                self._pending = path
                logger.info(f"📥 [android] Bulletin en attente : {path.name}")
        else:
            logger.warning(f"[android] enqueue ignoré (path invalide) : {path}")

    def run(self):
        """Boucle principale : joue le bulletin courant en boucle."""
        logger.info("📻 [android] Démarrage streamer Android")
        self._start_ffmpeg()
        try:
            while not self._stop_event.is_set():
                if self._current is None:
                    # Pas de bulletin : on envoie du silence en boucle
                    # pour que le mount Icecast existe (sinon 404)
                    # On attend qu'un bulletin arrive
                    self._send_silence_loop()
                    if self._stop_event.is_set():
                        break
                    continue
                # Joue le bulletin courant en boucle
                self._play_in_loop(self._current)
                # Si un nouveau bulletin attend, on bascule
                if self._pending is not None:
                    old = self._current
                    self._current = self._pending
                    self._pending = None
                    logger.info(
                        f"🔄 [android] Bascule : {old.name} → {self._current.name}"
                    )
                else:
                    # Sinon, on continue à boucler sur le même
                    logger.info(f"🔁 [android] Re-boucle sur {self._current.name}")
        except Exception as e:
            logger.error(f"[android] Erreur streamer : {e}", exc_info=True)
        finally:
            self._kill_ffmpeg()

    def stop(self):
        self._stop_event.set()
        self._current_done.set()  # débloquer les wait

    def _play_in_loop(self, path: Path):
        """
        Joue `path` jusqu'à la fin du fichier, en boucle tant que
        `_pending` n'est pas set. Si `_pending` est set pendant la
        lecture, on finit le passage en cours puis on sort.
        """
        self._current_done.clear()
        # On transcode le MP3 vers le pipe ffmpeg en boucle.
        # Note : on n'utilise pas ffmpeg -stream_loop, on reboucle
        # côté Python pour pouvoir intercepter `_pending` entre 2 passes.
        while not self._stop_event.is_set():
            if self._ffmpeg_proc is None or self._ffmpeg_proc.poll() is not None:
                self._start_ffmpeg()
                time.sleep(0.5)
            transcode_cmd = [
                "ffmpeg", "-y", "-i", str(path),
                "-vn", "-map", "0:a",
                "-ar", str(self.sample_rate), "-ac", str(self.channels),
                "-b:a", self.bitrate,
                "-codec:a", "libmp3lame",
                "-f", "mp3",
                "pipe:1",
            ]
            try:
                transcode = subprocess.Popen(
                    transcode_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
                # Stream chunk par chunk
                while not self._stop_event.is_set():
                    chunk = transcode.stdout.read(CHUNK_SIZE)
                    if not chunk:
                        break  # fin du fichier
                    self._write_to_pipe(chunk)
                    # Si un nouveau bulletin est arrivé, on finit la passe en cours
                    if self._pending is not None:
                        # vide le reste du fichier (court, ~10min)
                        # mais ne relance PAS une nouvelle passe
                        while not self._stop_event.is_set():
                            chunk = transcode.stdout.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            self._write_to_pipe(chunk)
                        break
                transcode.stdout.close()
                transcode.wait(timeout=5)
            except Exception as e:
                logger.error(f"[android] Erreur streaming {path.name} : {e}")
                break
            # Si un nouveau bulletin est pending, on sort de la boucle externe
            if self._pending is not None:
                break
            # Sinon, on reboucle (logger.info géré par le caller)
        self._current_done.set()

    def _write_to_pipe(self, data: bytes) -> bool:
        if not data or not self._ffmpeg_proc or self._ffmpeg_proc.stdin is None:
            return False
        try:
            self._ffmpeg_proc.stdin.write(data)
            self._ffmpeg_proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            logger.warning("[android] Pipe cassé → relance ffmpeg")
            self._start_ffmpeg()
            return False

    def _start_ffmpeg(self):
        with self._lock:
            self._kill_ffmpeg()
            cmd = [
                "ffmpeg", "-re",
                "-probesize", "32", "-analyzeduration", "0",
                "-f", "mp3", "-i", "pipe:0",
                "-vn", "-map", "0:a",
                "-codec:a", "libmp3lame",
                "-b:a", self.bitrate,
                "-ar", str(self.sample_rate), "-ac", str(self.channels),
                "-f", "mp3", "-content_type", "audio/mpeg",
                "-ice_name", "Nova Atlas Android",
                self.icecast_url,
            ]
            self._ffmpeg_proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE if self._debug else subprocess.DEVNULL,
            )
            logger.info(f"🔗 [android] ffmpeg connecté à Icecast {self.icecast_url}")

    def _kill_ffmpeg(self):
        if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
            try:
                if self._ffmpeg_proc.stdin:
                    self._ffmpeg_proc.stdin.close()
                self._ffmpeg_proc.terminate()
                self._ffmpeg_proc.wait(timeout=5)
            except Exception:
                pass
        self._ffmpeg_proc = None

    def _generate_silence(self) -> bytes:
        """Génère 1 seconde de silence MP3 pour le mount idle."""
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            f"anullsrc=r={self.sample_rate}:cl=stereo",
            "-t", "1",
            "-b:a", self.bitrate,
            "-codec:a", "libmp3lame",
            "-f", "mp3",
            "pipe:1",
        ]
        try:
            return subprocess.run(cmd, capture_output=True, timeout=10).stdout
        except Exception:
            return b""

    def _send_silence_loop(self):
        """
        Envoie du silence en boucle sur le pipe ffmpeg jusqu'à ce
        qu'un bulletin arrive (ou qu'on s'arrête). Ça force Icecast
        à créer le mount même quand il n'y a pas de contenu.
        """
        if not self._silence_bytes:
            # Si on n'a pas pu générer de silence, on attend juste
            self._current_done.clear()
            self._current_done.wait(timeout=5)
            return
        # Envoie 1s de silence en boucle
        deadline = time.monotonic() + 1.0
        while not self._stop_event.is_set() and self._current is None:
            self._write_to_pipe(self._silence_bytes)
            # Attend un peu (et vérifie si un bulletin est arrivé)
            remaining = deadline - time.monotonic()
            if remaining > 0:
                self._current_done.clear()
                self._current_done.wait(timeout=min(remaining, 0.1))
            else:
                deadline = time.monotonic() + 1.0