"""
modules/radio/streamer.py — Nova Media
Architecture : UN SEUL process ffmpeg permanent + pipe stdin
Supporte fadeout fluide entre musique et bulletins.
"""

import logging
from collections import OrderedDict
from modules.radio.audio_integrity import validated_publication, cache_publication, receipt_path, inspect_audio
from modules.radio import transport
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
            f"icecast://{ice.get('user', 'source')}:{ice.get('password', '')}"
            f"@{ice.get('host', 'localhost')}:{ice.get('port', 8000)}{ice.get('mount', '/nova')}"
        )

        self._stop_event      = threading.Event()
        self._fade_requested  = threading.Event()
        self._streaming_event = threading.Event()
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._play_queue: Queue = Queue()
        self._lock = threading.Lock()
        self._pipe_lock = threading.Lock()
        self._bulletin_lock = threading.Lock()
        self._accepted_bulletins = OrderedDict()

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
        """Admit an immutable validated publication once per recent history."""
        with self._bulletin_lock:
            if not path or not validated_publication(path) or Path(path).name in self._accepted_bulletins:
                logger.warning('Bulletin admission refused (invalid or duplicate)')
                return False
            studio=getattr(self,'studio',None)
            if studio:
                try:
                    if not studio.channel_event(Path(path),'primary','queued'):return False
                except (ValueError,OSError):return False
            self._play_queue.put(Path(path))
            self._accepted_bulletins[Path(path).name] = None
            if len(self._accepted_bulletins) > 4096:
                self._accepted_bulletins.popitem(last=False)
            self._fade_requested.set()
            return True

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
            logger.error('Streamer failure')
        finally:
            self._stop_event.set()
            self._kill_ffmpeg()
            heartbeat.join(timeout=6)

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------ #
    #  Heartbeat & maintenance                                             #
    # ------------------------------------------------------------------ #

    def _heartbeat(self):
        last_silence=-1.0
        while not self._stop_event.is_set():
            if self._ffmpeg_proc is None or self._ffmpeg_proc.poll() is not None or getattr(self,'_source_fault',False):
                logger.warning("⚠️ ffmpeg est mort → relance")
                self._start_ffmpeg()
            now=time.monotonic()
            if not self._streaming_event.is_set() and now-last_silence>=1:
                self._write_to_pipe(_SILENCE_BYTES);last_silence=now
            self._stop_event.wait(HEARTBEAT_INTERVAL)

    # ------------------------------------------------------------------ #
    #  Logique de lecture                                                  #
    # ------------------------------------------------------------------ #

    def _play_next(self):
        try:
            bulletin = self._play_queue.get_nowait()
            logger.info(f"🎙️ Diffusion du journal : {bulletin.name}")
            if not validated_publication(bulletin):
                logger.warning('Queued bulletin changed; playback refused')
                return
            studio=getattr(self,'studio',None)
            if studio:
                try:studio.channel_event(bulletin,'primary','playing')
                except (ValueError,OSError):return
            success=self._stream_file(bulletin, is_music=False)
            if studio:
                try:studio.channel_event(bulletin,'primary','locally_sent' if success else 'interrupted' if self._stop_event.is_set() else 'failed',bytes_sent=getattr(self,'_last_pass_bytes',0))
                except (ValueError,OSError):pass
            # Studio keeps immutable publications for reconciliation; legacy deletes only after success.
            if success and not studio:
                receipt_path(bulletin).unlink(missing_ok=True);bulletin.unlink(missing_ok=True)
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
                self._stop_event.wait(5)

    def _pick_music(self) -> Path | None:
        if not self.music_dir.exists() or self.music_dir.is_symlink():return None
        cache=getattr(self,'_music_validation',{});self._music_validation=cache
        files=list(self.music_dir.glob('*.mp3'))[:512];random.shuffle(files)
        for path in files:
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_nlink!=1:continue
                key=(path.name,path.stat().st_mtime_ns,path.stat().st_size)
                if key not in cache:
                    try:inspect_audio(path);cache[key]=True
                    except (ValueError,OSError):cache[key]=False
                if cache[key]:return path
            except OSError:continue
        if len(cache)>1024:self._music_validation={}
        return None

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
            "ffmpeg", "-nostdin", "-y", "-protocol_whitelist", "file,pipe", "-f", "mp3", "-i", str(music_path),
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
            logger.warning('Intro fade failed')
        finally:
            out_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    #  Streaming principal                                                 #
    # ------------------------------------------------------------------ #

    def _stream_file(self, path: Path, is_music: bool = True):
        success=transport.stream_file(self,path,music=is_music)
        if not success and is_music:self._stop_event.wait(1)
        return success

    def _write_to_pipe(self, data):return transport.write_pipe(self,data)
    def _start_ffmpeg(self):return transport.start_source(self,'Nova Media')
    def _kill_ffmpeg(self):
        with self._lock:
            transport.stop_process(self._ffmpeg_proc);self._ffmpeg_proc=None
        transport.observe(self,'stopped')


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
            f"icecast://{ice.get('user', 'source')}:{ice.get('password', '')}"
            f"@{ice.get('host', 'localhost')}:{ice.get('port', 8000)}{android_mount}"
        )
        # On garde la config pour recharger le bulletin courant si besoin
        self._config = config

        self._stop_event     = threading.Event()
        self._ffmpeg_proc    = None
        self._lock           = threading.Lock()
        self._pipe_lock      = threading.Lock()
        self._bulletin_lock  = threading.Lock()
        self._accepted_bulletins = OrderedDict()
        # Bulletin "courant" (celui qui est en train de boucler).
        # Au démarrage, on charge le dernier bulletin dispo dans
        # audio_queue/ pour ne pas commencer en silence.
        # None si aucun bulletin n'a encore été créé.
        self._current: Path | None = self._load_last_bulletin(config)
        # Nouveau bulletin qui attend la fin du courant
        self._pending: Path | None = None
        if self._current:
            self._accepted_bulletins[self._current.name] = None
        # Event levé quand le passage courant finit (entre 2 loops)
        self._current_done = threading.Event()
        self._current_done.set()  # rien en cours au début
        # Buffer de silence : on l'envoie à ffmpeg quand il n'y a
        # pas de bulletin, pour que le mount Icecast existe (sinon
        # Icecast ne liste pas le mount tant qu'aucune donnée n'est envoyée).
        self._silence_bytes = self._generate_silence()

    def _load_last_bulletin(self, config):
        queue_dir = Path(config.get('paths', {}).get('audio_queue', 'audio_queue'))
        candidates = list(queue_dir.glob('bulletin_*.mp3')) + list((queue_dir / 'android_cache').glob('bulletin_*.mp3'))
        ordered = []
        for path in candidates:
            try:
                if validated_publication(path):
                    ordered.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue  # The primary may have consumed it during the scan.
        for _, candidate in sorted(ordered, key=lambda entry: entry[0], reverse=True):
            try:
                return cache_publication(candidate, queue_dir / 'android_cache')
            except (OSError, ValueError):
                logger.warning('Android cached publication unavailable')
        return None

    def enqueue_bulletin(self, path: Path):
        """Copy atomically before primary playback can remove the original."""
        with self._bulletin_lock:
            if not path or not validated_publication(path) or Path(path).name in self._accepted_bulletins:
                logger.warning('Android bulletin admission refused (invalid or duplicate)')
                return False
            try:
                cached = cache_publication(path, Path(self._config.get('paths', {}).get('audio_queue', 'audio_queue')) / 'android_cache')
            except (OSError, ValueError):
                logger.warning('Android publication copy failed; previous audio retained')
                return False
            studio=getattr(self,'studio',None)
            if studio:
                try:
                    if not studio.channel_event(cached,'android','queued'):return False
                except (ValueError,OSError):return False
            if self._current is None:
                self._current = cached
                self._current_done.set()
            else:
                self._pending = cached
            self._accepted_bulletins[Path(path).name] = None
            if len(self._accepted_bulletins) > 4096:
                self._accepted_bulletins.popitem(last=False)
            return True

    def run(self):
        """Boucle principale : joue le bulletin courant en boucle."""
        logger.info("📻 [android] Démarrage streamer Android")
        self._start_ffmpeg()
        # Heartbeat : vérifie que le source ffmpeg est vivant toutes
        # les 2s. Si Icecast a timeout le mount, le ffmpeg source
        # ne crashe pas forcément (le pipe reste ouvert), donc on
        # doit le détecter manuellement et le relancer.
        heartbeat = threading.Thread(target=self._heartbeat, daemon=True, name="AndroidHeartbeat")
        heartbeat.start()
        try:
            while not self._stop_event.is_set():
                with self._bulletin_lock:
                    if self._current is not None and not validated_publication(self._current):
                        logger.warning('Android cached audio changed; playback refused')
                        self._current = self._pending if validated_publication(self._pending) else self._load_last_bulletin(self._config)
                        self._pending = None
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
                with self._bulletin_lock:
                    pending = self._pending
                    if pending is not None:
                        old = self._current
                        self._current = pending
                        self._pending = None
                if pending is not None:
                    logger.info(
                        f"🔄 [android] Bascule : {old.name} → {self._current.name}"
                    )
                elif self._current is not None and not self._current.exists():
                    # Le bulletin courant a été supprimé pendant la lecture
                    # (cleanup automatique). On recharge le dernier dispo.
                    fallback = self._load_last_bulletin(self._config)
                    if fallback and fallback != self._current:
                        old = self._current
                        self._current = fallback
                        logger.warning(
                            f"🔁 [android] Bulletin supprimé ({old.name}), "
                            f"fallback sur {self._current.name}"
                        )
                    else:
                        # Aucun autre bulletin → on reste en silence
                        old = self._current
                        self._current = None
                        logger.warning(
                            f"🔇 [android] Bulletin {old.name} supprimé, "
                            f"aucun fallback → silence"
                        )
                else:
                    # Sinon, on continue à boucler sur le même
                    logger.info(f"🔁 [android] Re-boucle sur {self._current.name}")
        except Exception as e:
            logger.error('Android streamer failure')
        finally:
            self._stop_event.set()
            self._kill_ffmpeg()
            heartbeat.join(timeout=6)

    def _heartbeat(self):
        """
        Vérifie toutes les 2s que le source ffmpeg est vivant et
        toujours connecté à Icecast. Si le pipe est cassé ou si
        ffmpeg est mort, on le relance.
        """
        while not self._stop_event.is_set():
            try:
                if self._ffmpeg_proc is None or self._ffmpeg_proc.poll() is not None or getattr(self,'_source_fault',False):
                    logger.warning("[android] Heartbeat: ffmpeg mort, relance")
                    self._start_ffmpeg()
            except Exception as e:
                logger.warning('Android heartbeat failed')
            self._stop_event.wait(2)

    def stop(self):
        self._stop_event.set()
        self._current_done.set()  # débloquer les wait

    def _play_in_loop(self, path: Path):
        self._current_done.clear()
        try:
            while not self._stop_event.is_set():
                if not validated_publication(path):return
                studio=getattr(self,'studio',None)
                if studio:
                    try:studio.channel_event(path,'android','playing')
                    except (ValueError,OSError):pass # Historical cache can be valid but untracked.
                success=transport.stream_file(self,path,pending=lambda:self._pending is not None)
                if studio:
                    try:studio.channel_event(path,'android','locally_sent' if success else 'interrupted',bytes_sent=getattr(self,'_last_pass_bytes',0))
                    except (ValueError,OSError):pass
                if self._pending is not None:return
                if not success:self._stop_event.wait(1)
        finally:self._current_done.set()

    def _write_to_pipe(self,data):return transport.write_pipe(self,data)
    def _start_ffmpeg(self):return transport.start_source(self,'Nova Atlas Android')
    def _kill_ffmpeg(self):
        with self._lock:
            transport.stop_process(self._ffmpeg_proc);self._ffmpeg_proc=None
        transport.observe(self,'stopped')

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
        # Exactly one one-second frame per second; wake immediately on a bulletin/stop.
        while not self._stop_event.is_set() and self._current is None:
            self._write_to_pipe(self._silence_bytes)
            self._current_done.clear()
            if self._current is None and not self._stop_event.is_set():
                self._current_done.wait(timeout=1)
