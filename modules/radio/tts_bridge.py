"""Runtime-only TTS bridge. No corpus, benchmark, report, model download or cloning."""
import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
import math
import stat
import subprocess
import tempfile
import time
from modules.radio.audio_integrity import AudioIntegrityError,inspect_audio


@dataclass(frozen=True)
class NativeSpeech:
    path:Path
    format:str
    kind:str='speech'


@dataclass(frozen=True)
class SpeechResult:
    selected_voice:str
    engine:str
    fallback:bool=False


class EdgeRuntime:
    async def speak(self,text,voice,path,cancel):
        import edge_tts
        task=asyncio.create_task(edge_tts.Communicate(text,voice).save(str(path)))
        try:
            while not task.done():
                if cancel is not None and cancel.is_set():raise AudioIntegrityError('tts_cancelled')
                await asyncio.wait({task},timeout=.1)
            await task
            return NativeSpeech(path,'mp3')
        finally:
            if not task.done():task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    def synthesize(self,text,voice,path,*,timeout,cancel=None):
        return asyncio.run(asyncio.wait_for(self.speak(text,voice,path,cancel),timeout))


class TTSBridge:
    def __init__(self,profiles,*,providers=None,inspector=None,converter=None,observer=None):
        if not isinstance(profiles,dict):raise AudioIntegrityError('invalid_tts_profiles')
        self.profiles=profiles;self.providers=providers or {'edge':EdgeRuntime()};self.inspector=inspector;self.converter=converter or self.convert;self.observer=observer
    @staticmethod
    def convert(native,output,timeout):
        if native.format not in ('wav','mp3'):raise AudioIntegrityError('unsupported_native_format')
        try:
            result=subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-protocol_whitelist','file,pipe','-f',native.format,'-i',str(native.path),'-map','0:a:0','-vn','-ac','1','-ar','24000','-codec:a','libmp3lame','-threads','1','-f','mp3',str(output)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=timeout)
            if result.returncode:raise AudioIntegrityError('tts_conversion_failed')
        except (OSError,subprocess.TimeoutExpired):raise AudioIntegrityError('tts_conversion_failed') from None
    def synthesize(self,text,voice,output,*,timeout=90,cancel=None,profile='bulletin'):
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=300:raise AudioIntegrityError('invalid_tts_timeout')
        if not isinstance(text,str) or not text.strip() or len(text)>128000:raise AudioIntegrityError('invalid_tts_text')
        # The existing tag->voice map is resolved by the caller; no fuzzy voice substitution.
        choices=self.profiles.get(profile,{}).get(voice)
        if not isinstance(choices,dict):raise AudioIntegrityError('unconfigured_voice')
        plans=[choices]
        if choices.get('fallback_enabled') is True:
            if not isinstance(choices.get('fallbacks',[]),list):raise AudioIntegrityError('invalid_fallback_plan')
            plans+=choices.get('fallbacks',[])
        if len(plans)>2:raise AudioIntegrityError('fallback_budget_exceeded')
        for plan in plans:
            if not isinstance(plan,dict):raise AudioIntegrityError('invalid_tts_plan')
            if plan.get('engine')=='edge' and (plan.get('language','fr') not in ('fr','fr-FR') or not str(plan.get('voice','')).startswith('fr-')):raise AudioIntegrityError('tts_language_mismatch')
            if not isinstance(plan,dict) or plan.get('qualified') is not True or plan.get('engine') not in self.providers or not isinstance(plan.get('voice'),str) or not re.fullmatch(r'[\w-]{1,100}',plan['voice']):raise AudioIntegrityError('unqualified_tts_engine_or_voice')
        output=Path(output)
        if any(p.is_symlink() for p in (output,*output.parents)) or output.parent.stat().st_mode&0o077:raise AudioIntegrityError('tts_private_directory_required')
        if output.parent.name.startswith('android_cache') or re.fullmatch(r'bulletin_.*\.mp3',output.name):raise AudioIntegrityError('tts_must_not_publish')
        deadline=time.monotonic()+timeout
        output.unlink(missing_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix='.tts-',dir=output.parent) as tmp:
                work=Path(tmp)
                for index,plan in enumerate(plans):
                    if cancel is not None and cancel.is_set():raise AudioIntegrityError('tts_cancelled')
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise AudioIntegrityError('tts_timeout')
                    try:
                        native=self.providers[plan['engine']].synthesize(text,plan['voice'],work/f'native-{index}',timeout=remaining,cancel=cancel)
                        if native.kind!='speech':raise AudioIntegrityError('fixture_audio_forbidden')
                        if native.path.parent!=work or native.path.is_symlink() or not native.path.is_file() or native.path.stat().st_nlink!=1 or not 0<native.path.stat().st_size<=256*1024*1024:raise AudioIntegrityError('invalid_native_audio')
                        remaining=deadline-time.monotonic()
                        if remaining<=0:raise AudioIntegrityError('tts_timeout')
                        self.converter(native,output,remaining)
                        if cancel is not None and cancel.is_set():raise AudioIntegrityError('tts_cancelled')
                        if self.inspector:self.inspector(output)
                        else:inspect_audio(output,timeout=max(.001,deadline-time.monotonic()))
                        if time.monotonic()>deadline:raise AudioIntegrityError('tts_timeout')
                        if self.observer:self.observer('tts_bridge_selected',engine=plan['engine'],voice=plan['voice'],fallback=index>0)
                        return SpeechResult(plan['voice'],plan['engine'],index>0)
                    except Exception:
                        output.unlink(missing_ok=True)
                        if cancel is not None and cancel.is_set():raise AudioIntegrityError('tts_cancelled') from None
                        if index==len(plans)-1:raise AudioIntegrityError('tts_bridge_failed') from None
        except BaseException:
            output.unlink(missing_ok=True);raise
        return False
