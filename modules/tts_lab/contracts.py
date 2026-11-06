"""Versioned lab contracts, exact text preservation and private experiment paths."""
from dataclasses import dataclass,field
import hashlib
import json
import math
from pathlib import Path
import re

VERSION=1
class LabError(ValueError):
    """Fixed reason codes only; provider stderr, paths and credentials stay private."""


def sha(raw):return hashlib.sha256(raw).hexdigest()

def bounded(value,maximum=200):
    return isinstance(value,str) and 0<len(value)<=maximum and not any(ord(c)<32 for c in value)


def checked(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise LabError('unsafe_path')
    return path


def private_root(path):
    path=checked(path)
    if set(path.parts)&{'audio_queue','android_cache','music','background_music','config','site','articles','reports','editions','secrets'}:raise LabError('production_path_forbidden')
    if path.exists():raise LabError('new_experiment_directory_required')
    path.mkdir(parents=True,mode=0o700);return path


def split_text(text,limit=600):
    if not isinstance(text,str) or not text.strip() or len(text)>128000 or '\x00' in text:raise LabError('invalid_text')
    if type(limit) is not int or not 40<=limit<=4000:raise LabError('invalid_chunk_limit')
    chunks=[];remaining=text
    while len(remaining)>limit:
        boundaries=[m.end() for m in re.finditer(r'[.!?][»”"]?(?:\s+|$)|\n\s*\n',remaining[:limit+1]) if m.end()<=limit]
        if boundaries:end=boundaries[-1]
        else:
            spaces=[m.end() for m in re.finditer(r'\s+',remaining[:limit+1]) if m.end()<=limit]
            if not spaces:raise LabError('unsplittable_token')
            end=spaces[-1]
        chunks.append(remaining[:end]);remaining=remaining[end:]
    if remaining:
        if remaining.strip():chunks.append(remaining)
        elif chunks:chunks[-1]+=remaining
    if ''.join(chunks)!=text or any(not s.strip() or len(s)>limit for s in chunks) or len(chunks)>512:raise LabError('invalid_chunk_plan')
    return [{'index':i,'id':sha((str(i)+'\0'+s).encode()),'text':s} for i,s in enumerate(chunks)]


@dataclass(frozen=True)
class EngineSpec:
    engine:str='fixture'
    model:str='synthetic-tone'
    version:str='1'
    voice:str='tone-a'
    language:str='fr-FR'
    code_version:str='stdlib'
    files:dict=field(default_factory=dict)
    parameters:dict=field(default_factory=dict)
    license_reviewed:bool=False
    license_reference:str='internal-synthetic-fixture'

    def validate(self,*,allow_network=False):
        if self.engine not in ('fixture','piper','edge'):raise LabError('unsupported_engine')
        if not all(bounded(v) for v in (self.model,self.version,self.voice,self.language,self.code_version,self.license_reference)):raise LabError('invalid_identity')
        if not all(re.fullmatch(r'[\w.-]{1,100}',v) for v in (self.model,self.version,self.voice,self.code_version)):raise LabError('invalid_public_identity')
        from urllib.parse import urlsplit
        if '://' in self.license_reference:
            url=urlsplit(self.license_reference)
            if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment:raise LabError('invalid_license_reference')
        if self.engine in ('piper','edge') and self.code_version!={'piper':'1.8.0','edge':'7.3.1'}[self.engine]:raise LabError('unsupported_adapter_version')
        if self.language not in ('fr','fr-FR'):raise LabError('french_required')
        if self.engine!='fixture' and self.license_reviewed is not True:raise LabError('license_review_required')
        if self.engine=='edge' and not allow_network:raise LabError('network_not_authorized')
        if self.engine=='edge' and self.voice not in ('fr-FR-HenriNeural','fr-FR-DeniseNeural'):raise LabError('voice_not_allowlisted')
        if self.engine=='fixture' and self.voice not in ('tone-a','tone-b'):raise LabError('invalid_fixture_voice')
        allowed={'fixture':{},'piper':{'length_scale':(.5,2)},'edge':{'rate_percent':(-30,30)}}[self.engine]
        if not isinstance(self.parameters,dict) or set(self.parameters)-set(allowed):raise LabError('unsupported_synthesis_parameters')
        for key,value in self.parameters.items():
            low,high=allowed[key]
            if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise LabError('invalid_synthesis_parameters')
        if self.engine=='piper':
            if set(self.files)!={'model','config'}:raise LabError('model_and_config_required')
            for key,value in self.files.items():
                if not isinstance(value,dict) or set(value)!={'path','sha256'} or not re.fullmatch('[0-9a-f]{64}',value['sha256']):raise LabError('invalid_model_identity')
                path=checked(value['path'])
                if not path.is_file() or path.stat().st_nlink!=1 or not 0<path.stat().st_size<=(65536 if key=='config' else 512*1024*1024):raise LabError('model_unavailable')
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
                if digest.hexdigest()!=value['sha256']:raise LabError('model_checksum_mismatch')
        return self

    def public(self):
        return {'engine':self.engine,'model':self.model,'version':self.version,'voice':self.voice,'language':self.language,'code_version':self.code_version,
                'parameters':self.parameters,'file_sha256':{k:v['sha256'] for k,v in self.files.items()},'license_reference':self.license_reference,'license_reviewed':self.license_reviewed}


def rtf(seconds,duration):
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (seconds,duration)) or seconds<0 or duration<=0:raise LabError('invalid_timing')
    return seconds/duration
