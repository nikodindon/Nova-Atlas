"""Optional private evidence; never participates in publication admission."""
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import uuid

log=logging.getLogger('nova.fetch')
NAME=re.compile(r'[0-9a-f]{32}\.json')
SECRET=re.compile(r'(?i)(?:-----BEGIN .*PRIVATE KEY|(?:sk-or-v1-|sk-proj-|hf_)[a-z0-9_-]{16,}|bearer\s+\S+|(?:password|api[_-]?key|token|secret)\s*[:=]\s*[^\s,;]+|https?://[^\s/]+:[^\s/]+@)')
THOUGHT=re.compile(r'<think\b|</think>|<\|channel>thought',re.I)


def options(raw):
    defaults={'enabled':False,'include_source':False,'include_raw_response':False,
              'retention_days':7,'max_records':200,'max_bytes':16*1024*1024,'entry_max_bytes':65536}
    if not isinstance(raw,dict) or set(raw)-set(defaults):raise ValueError('invalid_trace_configuration')
    result={**defaults,**raw}
    for k in ('enabled','include_source','include_raw_response'):
        if type(result[k]) is not bool:raise ValueError('invalid_trace_configuration')
    for k,lower,upper in [('retention_days',1,30),('max_records',1,1000),('max_bytes',65536,64*1024*1024),('entry_max_bytes',4096,128000)]:
        if type(result[k]) is not int or not lower<=result[k]<=upper:raise ValueError('invalid_trace_configuration')
    if result['entry_max_bytes']>result['max_bytes']:raise ValueError('invalid_trace_configuration')
    return result


def checked(path):
    path=Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('unsafe_trace_directory')
    return path


def read(path,maximum):
    fd=os.open(checked(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or info.st_mode&0o077 or info.st_size>maximum:raise ValueError('unsafe_trace_record')
        raw=stream.read(maximum+1)
        if len(raw)>maximum:raise ValueError('trace_size')
        return raw


def sensitive(value):
    from modules.fetch.editorial import REASONING
    if isinstance(value,dict):
        for key,item in value.items():
            if str(key).lower() in ('password','api_key','token','secret','reasoning','reasoning_content','analysis') and item is not None:
                return True
            if sensitive(item):return True
    elif isinstance(value,list):
        return any(sensitive(item) for item in value)
    elif isinstance(value,str):
        # Inspect the original strings, before JSON escaping can hide delimiters.
        return bool(SECRET.search(value) or THOUGHT.search(value) or REASONING.search(value)
                    or re.search(r"(?i)(?:password|api[_-]?key|token|secret)[\"']?\s*[:=]\s*\S+",value))
    return False


def save(data_dir,evidence,config,*,clock=time.time):
    """Nonblocking OS lease, bounded housekeeping, no raw exception logging."""
    fd=None;temp=None
    try:
        cfg=options(config)
        if not cfg['enabled']:return False
        if not isinstance(evidence,dict):raise ValueError('invalid_trace')
        raw=json.dumps(evidence,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if sensitive(evidence):raise ValueError('sensitive_trace_refused')
        if len(raw.encode())>cfg['entry_max_bytes']:raise ValueError('trace_size')
        root=checked(Path(data_dir)/'editorial_traces')
        root.mkdir(mode=0o700,exist_ok=True)
        info=root.stat()
        if info.st_uid!=os.geteuid() or info.st_mode&0o077:raise ValueError('private_trace_permissions_required')
        fd=os.open(root/'archive.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=os.geteuid() or info.st_mode&0o077:raise ValueError('unsafe_trace_lock')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        paths=list(root.iterdir())
        if len(paths)>1002:raise ValueError('trace_inventory_limit')
        rows=[];now=clock()
        for path in paths:
            if path.name=='archive.lock':continue
            if not NAME.fullmatch(path.name):raise ValueError('unknown_trace_file_preserved')
            content=read(path,128000)
            record=json.loads(content)
            if not isinstance(record,dict) or type(record.get('version')) is not int or record.get('version')!=1 or record.get('trace_id')!=path.stem or type(record.get('created_at')) not in (int,float) or not 0<=record['created_at']<=now+60:
                raise ValueError('invalid_trace_preserved')
            rows.append((record['created_at'],path,len(content)))
        # Validate the entire bounded inventory before removing any eligible row.
        rows.sort(key=lambda row:(row[0],row[1].name))
        kept=[]
        for row in rows:
            if now-row[0]>cfg['retention_days']*86400:row[1].unlink()
            else:kept.append(row)
        identity=uuid.uuid4().hex
        record={**evidence,'version':1,'trace_id':identity,'created_at':now}
        encoded=json.dumps(record,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
        if len(encoded)>cfg['entry_max_bytes']:raise ValueError('trace_size')
        while kept and (len(kept)>=cfg['max_records'] or sum(r[2] for r in kept)+len(encoded)>cfg['max_bytes']):
            kept.pop(0)[1].unlink()
        if sum(r[2] for r in kept)+len(encoded)>cfg['max_bytes']:raise ValueError('trace_quota')
        tmp_fd,temp=tempfile.mkstemp(prefix='.trace-',dir=root)
        with os.fdopen(tmp_fd,'wb') as stream:
            stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        os.replace(temp,root/(identity+'.json'));temp=None
        directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)
        return True
    except Exception:
        log.warning('Editorial trace unavailable; normal pipeline continues')
        return False
    finally:
        if temp:
            try:Path(temp).unlink(missing_ok=True)
            except OSError:pass
        if fd is not None:
            try:os.close(fd) # Stable lock inode is never removed.
            except OSError:log.warning('Editorial trace descriptor close failed')
