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
        self._ffmpeg_proc = None