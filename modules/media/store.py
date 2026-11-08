"""Private manifest and immutable local image artifacts. No network in public reads."""
import base64
from contextlib import nullcontext
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from modules.editorial.index import read,decode,lock,IndexError,EventIndex
from modules.core.source_manager import atomic_write,source_url
from modules.media.rights import assess,digest
from modules.media.worker import VERSION

MAX_MANIFEST=2*1024*1024
MAX_DISK=256*1024*1024
FALLBACK={'url':'/assets/news-fallback.svg','width':320,'height':180,'fallback':True,'caption':'Illustration indisponible','attribution':None}
class MediaError(ValueError):pass
class MediaConflict(MediaError):pass


def checked(path):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):raise MediaError('unsafe_path')
    return path


def decode_image(raw):
    project=Path(__file__).resolve().parents[2]
    try:
        result=subprocess.run([sys.executable,'-m','modules.media.worker'],input=raw,capture_output=True,timeout=10,cwd='/tmp',
            env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(project),'PYTHONDONTWRITEBYTECODE':'1','LANG':'C.UTF-8'})
        if result.returncode or len(result.stdout)>4*1024*1024:raise MediaError('image_decode_refused')
        data=json.loads(result.stdout)
        if data.get('error'):raise MediaError('image_decode_refused')
        return data
    except (ValueError,OSError,subprocess.TimeoutExpired):raise MediaError('image_decode_refused') from None


def target_for_article(article):
    from modules.editorial.events import Coverage
    c=Coverage.from_article(article)
    return {'kind':'article','id':c.article_hash,'fingerprint':c.id,'title':c.title}


def target_for_event(event):
    return {'kind':'event','id':event['event_id'],'fingerprint':digest([event['algorithm'],sorted(a['id'] for a in event['articles'])]),'title':event['title']}


class MediaStore:
    def __init__(self,data,*,mode='disabled',clock=None,runner=None):
        if mode not in ('disabled','suggest','approved_only'):raise MediaError('unsupported_media_policy')
        self.data=checked(data);self.root=self.data/'news_media';self.path=self.root/'manifest.json';self.mode=mode
        self.clock=clock or time.time;self.runner=runner or decode_image

    def init(self):
        checked(self.root);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        for name in ('originals','variants'):checked(self.root/name).mkdir(mode=0o700,exist_ok=True)

    def load(self):
        checked(self.path)
        try:raw=read(self.path,MAX_MANIFEST)
        except IndexError:raise MediaError('manifest_unavailable') from None
        if raw is None:return {'version':1,'records':{},'audit':[],'admissions':[]},digest(None)
        try:
            state=decode(raw)
            if not isinstance(state,dict) or type(state.get('version')) is not int or state['version']!=1 or set(state)!={'version','records','audit','admissions'}:raise ValueError()
            if not isinstance(state['records'],dict) or len(state['records'])>128 or not isinstance(state['audit'],list) or len(state['audit'])>1000 or not isinstance(state['admissions'],list) or len(state['admissions'])>3:raise ValueError()
            for identity,record in state['records'].items():
                if not re.fullmatch('[0-9a-f]{64}',identity) or not isinstance(record,dict) or record.get('id')!=identity:raise ValueError()
                source_url(record['candidate']['url']);source_url(record['candidate']['source_page'])
                if record['status'] not in ('rights_verified','rights_unknown','rights_restricted','rights_conflicting','verification_failed','approved','rejected'):raise ValueError()
                if not isinstance(record.get('history'),list) or len(record['history'])>8:raise ValueError()
                if record.get('artifact'):
                    if not re.fullmatch('[0-9a-f]{64}',record['artifact']['id']):raise ValueError()
                    for v in record['artifact']['variants']:
                        if v['size'] not in (320,768) or not re.fullmatch('[0-9a-f]{64}',v['sha256']):raise ValueError()
                if record.get('approval') and not re.fullmatch('[0-9a-f]{64}',record['approval']['id']):raise ValueError()
            self.validate(state)
            return state,hashlib.sha256(raw).hexdigest()
        except (ValueError,KeyError,TypeError,AttributeError,IndexError):raise MediaError('manifest_invalid_original_preserved') from None

    @staticmethod
    def validate(state):
        import math
        if any(type(t) not in (int,float) or not math.isfinite(t) for t in state['admissions']):raise ValueError()
        for row in state['records'].values():
            if row['resource_version']!=digest(row['candidate']) or row['nature']!='illustration' or not isinstance(row['caption'],str) or len(row['caption'])>500:raise ValueError()
            if row['rights']!=assess(row['candidate'].get('proof',{}),trusted_commons=row.get('proof_trusted') is True and row['candidate'].get('provider')=='commons'):raise ValueError()
            if any(type(row.get(t)) not in (int,float) or not math.isfinite(row[t]) for t in ('checked_at','created_at')):raise ValueError()
            if row['target'].get('kind') not in ('article','event','article_reference') or not re.fullmatch('[0-9a-f]{64}',row['target']['fingerprint']):raise ValueError()
            for record in [row,*row['history']]:
                artifact=record.get('artifact')
                if artifact:
                    if not re.fullmatch('[0-9a-f]{64}',artifact['id']) or not re.fullmatch('[0-9a-f]{64}',artifact['source_sha256']):raise ValueError()
                    variants=artifact['variants']
                    if not isinstance(variants,list) or len(variants)!=2 or {v['size'] for v in variants}!={320,768}:raise ValueError()
                    for v in variants:
                        if not re.fullmatch('[0-9a-f]{64}',v['sha256']) or type(v['width']) is not int or type(v['height']) is not int or not 1<=v['width']<=768 or not 1<=v['height']<=432:raise ValueError()
                approval=record.get('approval')
                if approval and (not re.fullmatch('[0-9a-f]{64}',approval['id']) or not re.fullmatch('[0-9a-f]{64}',approval['binding'])):raise ValueError()

    def write(self,state):
        raw=json.dumps(state,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))
        if len(raw.encode())>MAX_MANIFEST or len(state['records'])>128 or len(state['audit'])>1000:raise MediaError('manifest_quota')
        atomic_write(self.path,raw)

    def transaction(self,callback,expected=None):
        self.init()
        with lock(self.root/'media.lock'):
            state,version=self.load()
            if expected is not None and expected!=version:raise MediaConflict('version_conflict_reload_required')
            result=callback(state)
            self.write(state);return result

    def add(self,candidate,target,*,trusted=False):
        if self.mode=='disabled':return None
        candidate=asdict(candidate);source_url(candidate['url']);source_url(candidate['source_page'])
        identity=digest([candidate['url'],target['kind'],target['id'],target['fingerprint']]);rights=assess(candidate.get('proof',{}),trusted_commons=trusted)
        def save(state):
            old=state['records'].get(identity);version=digest(candidate)
            if old and old['resource_version']==version:
                old['checked_at']=self.clock();return identity
            if old:
                if len(old['history'])>=8:raise MediaError('version_history_full')
                history=old['history']+[{'resource_version':old['resource_version'],'candidate':old['candidate'],'rights':old['rights'],'artifact':old.get('artifact'),'approval':old.get('approval')}]
            else:history=[]
            state['records'][identity]={'id':identity,'candidate':candidate,'target':target,'resource_version':version,'rights':rights,'status':rights['status'],
                'caption':old.get('caption','Illustration générique') if old else 'Illustration générique','proof_trusted':trusted,'nature':'illustration','checked_at':self.clock(),'created_at':old.get('created_at',self.clock()) if old else self.clock(),'artifact':None,'approval':None,'history':history,'error':None}
            return identity
        return self.transaction(save)

    def usage(self):
        total=0;count=0
        if not self.root.exists():return 0
        for directory,dirs,files in os.walk(checked(self.root),followlinks=False):
            for name in dirs:checked(Path(directory)/name)
            for name in files:
                path=checked(Path(directory)/name);info=path.stat(follow_symlinks=False)
                if info.st_nlink!=1 or not __import__('stat').S_ISREG(info.st_mode):raise MediaError('unsafe_file')
                total+=info.st_size;count+=1
                if count>4096:raise MediaError('file_count_quota')
        return total

    def prepare(self,identity,raw,*,expected):
        if self.mode=='disabled':raise MediaError('downloads_disabled')
        data=self.runner(raw);source_sha=hashlib.sha256(raw).hexdigest()
        if data.get('version')!=VERSION or data.get('source_sha256')!=source_sha or len(data.get('variants',[]))!=2:raise MediaError('invalid_decoder_output')
        artifact_id=digest([source_sha,VERSION]);artifact={'id':artifact_id,'source_sha256':source_sha,'processor':VERSION,'variants':[],'metrics':data.get('metrics',{})}
        content={}
        for v in data['variants']:
            if v['size'] not in (320,768) or not 1<=v['width']<=v['size'] or not 1<=v['height']<=432:raise MediaError('invalid_decoder_output')
            blob=base64.b64decode(v['data'],validate=True)
            if not 1<=len(blob)<=1024*1024 or hashlib.sha256(blob).hexdigest()!=v['sha256'] or not blob.startswith(b'\xff\xd8'):raise MediaError('invalid_decoder_output')
            content[v['size']]=blob;artifact['variants'].append({k:v[k] for k in ('size','width','height','sha256','bytes')})
        if set(content)!={320,768}:raise MediaError('invalid_decoder_output')
        def store(state):
            record=state['records'].get(identity)
            if not record or record['rights']['status']!='rights_verified' or record['status']=='rejected':raise MediaError('rights_not_verified')
            proof=record['candidate']['proof']
            if hashlib.sha1(raw).hexdigest()!=proof.get('file_sha1') or len(raw)!=proof.get('size'):raise MediaError('resource_version_mismatch')
            if self.usage()+len(raw)+sum(map(len,content.values()))>MAX_DISK:raise MediaError('disk_quota')
            original=checked(self.root/'originals'/(source_sha+'.bin'))
            if not original.exists():
                fd,name=tempfile.mkstemp(prefix='.original-',dir=self.root)
                try:
                    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
                    os.replace(name,original)
                finally:
                    if os.path.exists(name):os.unlink(name)
            elif hashlib.sha256(read(original,4*1024*1024)).hexdigest()!=source_sha:raise MediaError('original_corrupt')
            destination=checked(self.root/'variants'/artifact_id)
            if not destination.exists():
                staged=Path(tempfile.mkdtemp(prefix='.stage-',dir=self.root))
                try:
                    for size,blob in content.items():
                        with open(staged/(str(size)+'.jpg'),'xb') as stream:os.chmod(stream.name,0o600);stream.write(blob);stream.flush();os.fsync(stream.fileno())
                    os.rename(staged,destination)
                finally:
                    if staged.exists():shutil.rmtree(staged)
            else:
                for size,blob in content.items():
                    if read(checked(destination/(str(size)+'.jpg')),1024*1024)!=blob:raise MediaError('derivative_corrupt')
            approval=record.get('approval');old_artifact=record.get('artifact')
            record.update(artifact=artifact,status='rights_verified',approval=None,error=None)
            if approval and approval.get('binding')==self.binding(record):record.update(approval=approval,status='approved')
            elif approval:
                if len(record['history'])>=8:raise MediaError('version_history_full')
                record['history'].append({'artifact':old_artifact,'approval':approval,'resource_version':record['resource_version']})
            for directory in (self.root/'originals',destination,self.root/'variants'):
                fd=os.open(checked(directory),os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            return artifact
        return self.transaction(store,expected)

    @staticmethod
    def binding(record):return digest([record['resource_version'],{k:v for k,v in record['artifact'].items() if k!='metrics'} if record.get('artifact') else None,record['caption'],record['nature'],record['target']])

    def decide(self,identity,action,*,expected,binding=None,ack=False,caption=None):
        if action not in ('approve','reject','disable','caption'):raise MediaError('invalid_decision')
        def save(state):
            row=state['records'].get(identity)
            if not row:raise MediaError('candidate_missing')
            if action=='approve':
                if self.mode=='disabled' or row['rights']['status']!='rights_verified' or not row['artifact'] or not 0<=self.clock()-row['checked_at']<=86400 or ack is not True or binding!=self.binding(row):raise MediaError('approval_requires_exact_resource_rights_and_review')
                # Only generic illustrations are supported; never infer a documentary match.
                row['approval']={'id':digest([identity,binding]),'binding':binding,'at':self.clock(),'third_party_review_acknowledged':True};row['status']='approved'
            elif action=='caption':
                if not isinstance(caption,str) or not caption.strip() or len(caption)>500 or any(ord(c)<32 for c in caption):raise MediaError('invalid_caption')
                if row['approval']:
                    if len(row['history'])>=8:raise MediaError('version_history_full')
                    row['history'].append({'artifact':row['artifact'],'approval':row['approval'],'resource_version':row['resource_version']})
                row.update(caption=caption,approval=None,status=row['rights']['status'])
            else:row['status']='rejected' # Keep the artifact, prior approval and history for audit; never serve it.
            state['audit'].append({'candidate':identity,'action':action,'at':self.clock()});return row
        return self.transaction(save,expected)

    def record_projection(self,row):
        approval=row.get('approval')
        if self.mode!='approved_only' or row['status']!='approved' or not approval or row['rights']['status']!='rights_verified' or not 0<=self.clock()-row['checked_at']<=86400:return None
        if approval.get('binding')!=self.binding(row):return None
        v=next((v for v in row['artifact']['variants'] if v['size']==320),None)
        if not v:return None
        # Fail closed on missing/corrupted storage, with no image decoder in a public route.
        try:
            for size in (320,768):self.bytes(row,size)
        except (ValueError,OSError,TypeError):return None
        return {'url':f"/media/images/{approval['id']}/320.jpg",'large_url':f"/media/images/{approval['id']}/768.jpg",'width':v['width'],'height':v['height'],'fallback':False,
            'caption':'Illustration générique — '+row['caption'],'attribution':{'author':row['rights']['author'],'credit':row['rights']['credit'],'source':row['candidate']['source_page'],'license':'CC BY 4.0','license_url':row['rights']['license_url'],'changes':'Redimensionnement, orientation corrigée et réencodage JPEG ; métadonnées supprimées.'}}

    def public_for(self,target):
        return self.public_many([target])[0]

    def public_many(self,targets):
        """One validated manifest snapshot per bounded page; no cross-request cache."""
        if not targets:return []
        if len(targets)>100:raise MediaError('projection_page_limit')
        fallback=lambda:[dict(FALLBACK) for _ in targets]
        if self.mode!='approved_only':return fallback()
        try:
            state,_=self.load();grouped={}
            for row in state['records'].values():
                target=row['target'];key=(target['kind'],target['id'],target['fingerprint'])
                grouped.setdefault(key,[]).append(row)
            results=[]
            for target in targets:
                rows=grouped.get((target['kind'],target['id'],target['fingerprint']),[])
                projected=None
                for row in sorted(rows,key=lambda r:r.get('approval',{}).get('at',0) if r.get('approval') else 0,reverse=True):
                    projected=self.record_projection(row)
                    if projected:break
                results.append(projected or dict(FALLBACK))
            return results
        except (ValueError,OSError,KeyError,TypeError):return fallback()

    def bytes(self,row,size):
        if size not in (320,768):raise MediaError('invalid_variant')
        v=next(v for v in row['artifact']['variants'] if v['size']==size)
        path=checked(self.root/'variants'/row['artifact']['id']/(str(size)+'.jpg'))
        blob=read(path,1024*1024)
        if blob is None or hashlib.sha256(blob).hexdigest()!=v['sha256']:raise MediaError('derivative_missing_or_corrupt')
        return blob

    def cleanup(self,*,apply=False,expected=None):
        if not self.root.exists():return {'version':digest(None),'dry_run':not apply,'paths':[]}
        if apply:self.init()
        with lock(self.root/'media.lock') if apply else nullcontext():
            state,version=self.load()
            if apply and expected!=version:raise MediaConflict('version_conflict_reload_required')
            protected=set();sources=set()
            for row in state['records'].values():
                for record in [row,*row['history']]:
                    artifact=record.get('artifact')
                    if artifact:protected.add(artifact['id']);sources.add(artifact['source_sha256'])
            paths=[]
            for directory,keep in ((self.root/'variants',protected),(self.root/'originals',{s+'.bin' for s in sources})):
                for child in directory.iterdir():
                    checked(child)
                    if child.name not in keep and self.clock()-child.stat().st_mtime>7*86400:paths.append(child)
            for child in self.root.glob('.stage-*'):
                checked(child)
                if self.clock()-child.stat().st_mtime>86400:paths.append(child)
            if apply:
                for path in paths:
                    if path.is_dir():shutil.rmtree(path)
                    else:path.unlink()
            return {'version':version,'dry_run':not apply,'paths':[str(p.relative_to(self.root)) for p in paths]}
