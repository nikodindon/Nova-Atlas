"""One bounded private experiment: model loaded once, repeat complete ordered texts."""
from dataclasses import asdict
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import socket
import subprocess
import sys
import time
from modules.tts_lab.contracts import EngineSpec,LabError,split_text,sha,rtf
from modules.tts_lab.audio import inspect_wav,normalize_pcm,concatenate


def versions():
    result={'python':sys.version.split()[0]}
    for name in ('piper-tts','onnxruntime','numpy','edge-tts','aiohttp'):
        try:result[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:pass
    return result


def convert(source,destination,*,seconds=15):
    try:
        result=subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-protocol_whitelist','file,pipe','-f','mp3' if Path(source).suffix=='.mp3' else 'wav','-i',str(source),
            '-map','0:a:0','-vn','-ac','1','-ar','24000','-c:a','pcm_s16le','-threads','1',str(destination)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=seconds)
        if result.returncode:raise LabError('conversion_failed')
        return inspect_wav(destination)
    except (OSError,subprocess.TimeoutExpired):raise LabError('conversion_failed') from None


def experiment(spec,cases,root,*,repetitions=3,limit=600,adapter_factory=None,converter=convert):
    from modules.tts_lab.adapters import create
    started=time.monotonic();adapter=(adapter_factory or create)(spec);load=time.monotonic()-started
    rows=[]
    for case in cases:
        chunks=split_text(case['text'],limit)
        for repetition in range(repetitions):
            records=[];files=[];cpu_start=time.process_time();begin=time.monotonic();first_segment=None
            for chunk in chunks:
                if sum(p.stat().st_size for p in root.iterdir() if p.is_file())>224*1024*1024:raise LabError('experiment_disk_quota')
                stem=root/f"{case['id']}-{repetition}-{chunk['index']:03d}"
                native=stem.with_suffix('.mp3' if spec.engine=='edge' else '.wav')
                segment_start=time.monotonic();result=adapter.synthesize(chunk['text'],native);elapsed=time.monotonic()-segment_start
                if first_segment is None:first_segment=elapsed
                if not native.is_file() or native.is_symlink() or native.stat().st_nlink!=1 or not 0<native.stat().st_size<=32*1024*1024:raise LabError('missing_native_audio')
                comparison=stem.with_name(stem.name+'-comparison.wav')
                # Normalize every engine to the same mono/24k PCM comparison format.
                converter(native,comparison)
                info=inspect_wav(comparison)
                if info['all_silent']:raise LabError('silent_output')
                records.append({'index':chunk['index'],'text_sha256':sha(chunk['text'].encode()),'characters':len(chunk['text']),
                    'generation_seconds':elapsed,'native_audio':inspect_wav(native) if result['native_format']=='wav' else None,'native_format':result['native_format'],'native_sha256':sha(native.read_bytes()),'audio':info,'file':comparison.name})
                files.append(comparison)
            synthesis=sum(r['generation_seconds'] for r in records)
            joined=root/f"{case['id']}-{repetition}-joined.wav";audio=concatenate(files,joined)
            normalized=root/f"{case['id']}-{repetition}-normalized.wav";normal=normalize_pcm(joined,normalized)
            total=time.monotonic()-begin;cpu=time.process_time()-cpu_start
            rows.append({'case_id':case['id'],'text_sha256':sha(case['text'].encode()),'characters':len(case['text']),'words':len(case['text'].split()),
                'repetition':repetition,'phase':'first_after_load' if not rows else 'loaded_model_repeat','sample_kind':adapter.sample_kind,
                'synthesis_seconds':synthesis,'end_to_end_seconds':total,'first_segment_seconds':first_segment,'audio':audio,'normalized_audio':normal,
                'rtf':rtf(synthesis,audio['duration_seconds']),'end_to_end_rtf':rtf(total,audio['duration_seconds']),
                'characters_per_second':len(case['text'])/synthesis if synthesis>0 else None,'cpu_seconds':cpu,'cpu_percent_of_one_core':100*cpu/total if total else None,
                'peak_ram_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'peak_vram_mib':None,'gpu_utilization_percent':None,
                'warnings':['long_silence_requires_review'] if audio['longest_silence_seconds']>2 else [],'coverage':'unverified_no_asr_or_listening','segments':records,'comparison_file':normalized.name})
    return {'version':1,'engine':spec.public(),'sample_kind':adapter.sample_kind,'model_load_seconds':load,'versions':versions(),'runs':rows,
        'memory_scope':'Linux worker ru_maxrss cumulative process maximum; not per-segment RAM or total system usage'}


def main():
    resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3));resource.setrlimit(resource.RLIMIT_CPU,(300,300));resource.setrlimit(resource.RLIMIT_FSIZE,(32*1024**2,32*1024**2))
    os.nice(10)
    if hasattr(os,'sched_getaffinity'):os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    try:
        data=json.loads(sys.stdin.buffer.read(512*1024));spec=EngineSpec(**data['spec']);spec.validate(allow_network=data.get('allow_network') is True)
        if spec.engine!='edge':
            def denied(*args,**kwargs):raise LabError('network_forbidden')
            socket.socket.connect=denied;socket.socket.connect_ex=denied;socket.getaddrinfo=denied
        result=experiment(spec,data['cases'],Path.cwd(),repetitions=data['repetitions'],limit=data['limit'])
        spec.validate(allow_network=data.get('allow_network') is True)
        for binary in ('ffmpeg','ffprobe'):
            try:
                version=subprocess.run([binary,'-version'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=2)
                result['versions'][binary]=version.stdout.splitlines()[0][:200] if version.returncode==0 else None
            except (OSError,subprocess.TimeoutExpired,IndexError):result['versions'][binary]=None
        print(json.dumps({'status':'success','result':result},allow_nan=False));return 0
    except LabError as error:print(json.dumps({'status':'failed','reason':str(error)}));return 2
    except Exception:print(json.dumps({'status':'failed','reason':'engine_or_audio_failure'}));return 2

if __name__=='__main__':raise SystemExit(main())
