"""Bounded PCM inspection and explicit laboratory conversion; no radio receipts."""
import math
from array import array
import sys
from pathlib import Path
import struct
import wave
from modules.tts_lab.contracts import checked,LabError,sha

MAX_BYTES=32*1024*1024


def inspect_wav(path):
    path=checked(path)
    if not path.is_file() or path.stat().st_nlink!=1 or not 44<path.stat().st_size<=MAX_BYTES:raise LabError('missing_or_oversized_audio')
    try:
        with wave.open(str(path),'rb') as stream:
            rate,channels,width,count=stream.getframerate(),stream.getnchannels(),stream.getsampwidth(),stream.getnframes()
            if width!=2 or channels not in (1,2) or not 8000<=rate<=48000 or count<=0 or stream.getcomptype()!='NONE':raise LabError('unsupported_pcm')
            raw=stream.readframes(count)
            if len(raw)!=count*channels*width:raise LabError('truncated_audio')
    except (wave.Error,EOFError,OSError):raise LabError('invalid_audio') from None
    samples=array('h');samples.frombytes(raw)
    if sys.byteorder!='little':samples.byteswap()
    square=sum(v*v for v in samples)/len(samples);rms=math.sqrt(square)
    silent=0;longest=0
    for i in range(0,len(samples),channels):
        if max(abs(v) for v in samples[i:i+channels])<104:silent+=1;longest=max(longest,silent)
        else:silent=0
    return {'duration_seconds':count/rate,'sample_rate':rate,'channels':channels,'sample_width':width,'frames':count,
            'bytes':path.stat().st_size,'sha256':sha(path.read_bytes()),'rms_dbfs':20*math.log10(rms/32768) if rms else None,
            'peak':max(map(abs,samples))/32768,'longest_silence_seconds':longest/rate,'all_silent':rms<104}


def normalize_pcm(source,destination,*,target_dbfs=-20):
    """Fixed comparison gain, bounded peak. Not EBU R128 or radio mastering."""
    info=inspect_wav(source)
    if info['all_silent']:raise LabError('silent_output')
    with wave.open(str(source),'rb') as stream:raw=stream.readframes(info['frames'])
    values=array('h');values.frombytes(raw)
    if sys.byteorder!='little':values.byteswap()
    gain=min(10**((target_dbfs-info['rms_dbfs'])/20),.95/max(info['peak'],1e-9))
    result=array('h',(max(-32768,min(32767,round(v*gain))) for v in values))
    if sys.byteorder!='little':result.byteswap()
    out=result.tobytes()
    with wave.open(str(destination),'wb') as stream:
        stream.setparams((info['channels'],2,info['sample_rate'],0,'NONE','not compressed'));stream.writeframes(out)
    return inspect_wav(destination)


def concatenate(paths,destination):
    if not paths:raise LabError('missing_segments')
    infos=[inspect_wav(p) for p in paths]
    if len({(i['sample_rate'],i['channels'],i['sample_width']) for i in infos})!=1:raise LabError('inconsistent_formats')
    if sum(i['bytes'] for i in infos)>MAX_BYTES:raise LabError('assembled_audio_too_large')
    with wave.open(str(destination),'wb') as stream:
        stream.setparams((infos[0]['channels'],2,infos[0]['sample_rate'],0,'NONE','not compressed'))
        for path in paths:
            with wave.open(str(path),'rb') as part:stream.writeframes(part.readframes(part.getnframes()))
    result=inspect_wav(destination)
    if result['frames']!=sum(i['frames'] for i in infos):raise LabError('missing_samples')
    return result
