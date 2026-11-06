"""Only explicitly selected engines; no model manager, downloader or cloning API."""
import asyncio
import importlib.metadata
import math
import struct
import wave
from modules.tts_lab.contracts import LabError


def dependency(name,expected):
    try:version=importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:raise LabError('dependency_missing') from None
    if version!=expected:raise LabError('dependency_version_mismatch')
    return version


class FixtureAdapter:
    """A tone, NEVER speech or a voice-quality/performance proxy for a TTS engine."""
    sample_kind='synthetic_fixture'
    def __init__(self,spec):self.spec=spec
    def synthesize(self,text,path):
        rate=24000;duration=.08;frequency=330 if self.spec.voice=='tone-a' else 440
        samples=[round(5000*math.sin(2*math.pi*frequency*i/rate)) for i in range(int(duration*rate))]
        with wave.open(str(path),'wb') as stream:
            stream.setparams((1,2,rate,0,'NONE','not compressed'));stream.writeframes(struct.pack('<'+'h'*len(samples),*samples))
        return {'native_format':'wav','sample_kind':self.sample_kind}


class PiperAdapter:
    sample_kind='real_synthesis'
    def __init__(self,spec):
        dependency('piper-tts',spec.code_version);self.parameters=spec.parameters
        import json
        from piper import PiperVoice
        from piper.config import PiperConfig
        import onnxruntime as ort
        config=json.loads(__import__('pathlib').Path(spec.files['config']['path']).read_text())
        if not str(config.get('language',{}).get('code','')).startswith('fr'):raise LabError('model_language_mismatch')
        if config.get('num_speakers',1)!=1:raise LabError('single_speaker_model_required')
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        self.voice=PiperVoice(config=PiperConfig.from_dict(config),session=ort.InferenceSession(spec.files['model']['path'],sess_options=options,providers=['CPUExecutionProvider']))
    def synthesize(self,text,path):
        from piper.config import SynthesisConfig
        with wave.open(str(path),'wb') as stream:self.voice.synthesize_wav(text,stream,syn_config=SynthesisConfig(**self.parameters))
        return {'native_format':'wav','sample_kind':self.sample_kind}


class EdgeAdapter:
    sample_kind='real_synthesis'
    def __init__(self,spec):dependency('edge-tts',spec.code_version);self.spec=spec
    def synthesize(self,text,path):
        import edge_tts
        async def run():await edge_tts.Communicate(text,self.spec.voice,rate=f"{self.spec.parameters.get('rate_percent',0):+g}%").save(str(path))
        asyncio.run(run()) # The parent process owns timeout, cancellation and cleanup.
        return {'native_format':'mp3','sample_kind':self.sample_kind}


def create(spec):return {'fixture':FixtureAdapter,'piper':PiperAdapter,'edge':EdgeAdapter}[spec.engine](spec)
