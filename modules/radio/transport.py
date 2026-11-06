"""Local FFmpeg observations and bounded reconnects; no claim of Icecast delivery."""
import os
from pathlib import Path
import select
import subprocess
import time


def observe(owner,event,**values):
    studio=getattr(owner,'studio',None)
    if studio:
        try:studio.observe(owner.studio_channel,event,**values)
        except (ValueError,OSError):pass # Broken telemetry must not cut continuous music.


def stop_process(process):
    if process is None:return
    try:
        if process.poll() is None:process.terminate()
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
    except (OSError,subprocess.TimeoutExpired):pass
    for name in ('stdin','stdout'):
        stream=getattr(process,name,None)
        if stream:
            try:stream.close()
            except OSError:pass


def start_source(owner,name):
    if owner._stop_event.is_set():return False
    if owner._ffmpeg_proc and owner._ffmpeg_proc.poll() is None and not getattr(owner,'_source_fault',False):return True
    if time.monotonic()<getattr(owner,'_retry_at',0):return False
    with owner._lock:
        if owner._ffmpeg_proc and owner._ffmpeg_proc.poll() is None and not getattr(owner,'_source_fault',False):return True
        if owner._stop_event.is_set() or time.monotonic()<getattr(owner,'_retry_at',0):return False
        existed=getattr(owner,'_source_epoch',0)>0
        stop_process(owner._ffmpeg_proc);owner._ffmpeg_proc=None
        failures=getattr(owner,'_transport_failures',0)+(1 if existed else 0);owner._transport_failures=failures
        delay=min(30,2**min(failures,5));owner._retry_at=time.monotonic()+delay
        cmd=['ffmpeg','-nostdin','-re','-probesize','32','-analyzeduration','0','-f','mp3','-i','pipe:0','-vn','-map','0:a',
             '-codec:a','libmp3lame','-b:a',owner.bitrate,'-ar',str(owner.sample_rate),'-ac',str(owner.channels),'-f','mp3','-content_type','audio/mpeg','-ice_name',name,owner.icecast_url]
        try:
            owner._ffmpeg_proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            os.set_blocking(owner._ffmpeg_proc.stdin.fileno(),False)
            owner._source_epoch=getattr(owner,'_source_epoch',0)+1;owner._source_fault=False
            observe(owner,'ffmpeg_started',epoch=owner._source_epoch,delay=delay)
            return True
        except (OSError,ValueError):
            stop_process(owner._ffmpeg_proc);owner._ffmpeg_proc=None;owner._transport_failures=failures+1
            observe(owner,'transport_failed',delay=delay);return False


def write_pipe(owner,data):
    if not data or not owner._ffmpeg_proc or owner._ffmpeg_proc.poll() is not None:return False
    # Serialize heartbeat and audio writes. No restart while holding the pipe lock.
    with owner._pipe_lock:
        process=owner._ffmpeg_proc;until=time.monotonic()+5;offset=0
        try:
            fd=process.stdin.fileno()
            while offset<len(data) and not owner._stop_event.is_set():
                if process.poll() is not None or time.monotonic()>=until:raise OSError()
                if not select.select([],[fd],[],.1)[1]:continue
                try:offset+=os.write(fd,data[offset:])
                except BlockingIOError:continue
            if offset!=len(data):return False
            owner._bytes_local_pipe=getattr(owner,'_bytes_local_pipe',0)+offset
            if time.monotonic()-getattr(owner,'_last_observation',0)>=5:
                observe(owner,'local_bytes',bytes=owner._bytes_local_pipe);owner._bytes_local_pipe=0;owner._last_observation=time.monotonic()
            return True
        except (OSError,ValueError):
            owner._source_fault=True;observe(owner,'transport_failed');return False


def stream_file(owner,path,*,music=False,pending=None):
    if not owner._start_ffmpeg():return False
    path=Path(path);process=None;success=False;owner._last_pass_bytes=0;epoch=getattr(owner,'_source_epoch',0)
    cmd=['ffmpeg','-v','error','-nostdin','-protocol_whitelist','file,pipe','-f','mp3','-i',str(path),'-vn','-map','0:a','-ar',str(owner.sample_rate),'-ac',str(owner.channels),
         '-b:a',owner.bitrate,'-codec:a','libmp3lame','-threads','1','-f','mp3','pipe:1']
    try:
        process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        os.set_blocking(process.stdout.fileno(),False);deadline=time.monotonic()+3600
        if hasattr(owner,'_streaming_event'):owner._streaming_event.set()
        while not owner._stop_event.is_set() and time.monotonic()<deadline:
            if pending and pending():break
            if music and owner._fade_requested.is_set():owner._fade_requested.clear();break
            fd=process.stdout.fileno()
            if not select.select([fd],[],[],.1)[0]:continue
            chunk=os.read(fd,32768)
            if not chunk:
                success=process.wait(timeout=5)==0 and owner._last_pass_bytes>0 and owner._ffmpeg_proc is not None and owner._ffmpeg_proc.poll() is None and epoch==getattr(owner,'_source_epoch',0) and not getattr(owner,'_source_fault',False)
                break
            if not owner._write_to_pipe(chunk):break
            owner._last_pass_bytes+=len(chunk)
        return success
    except (OSError,ValueError,subprocess.TimeoutExpired):return False
    finally:
        stop_process(process)
        if hasattr(owner,'_streaming_event'):owner._streaming_event.clear()
