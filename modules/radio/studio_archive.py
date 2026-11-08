"""Manual archive: immutable rows + durable tombstones before journal compaction."""
from contextlib import ExitStack
from datetime import datetime,timezone
import hashlib
import json
import os
import re
from modules.core.source_manager import atomic_write
from modules.editorial.index import read,decode,lock
from modules.radio.studio import StudioError,StudioConflict,checked,digest,lease

LIMIT=100000


def index(root):
    try:
        raw=read(checked(root/'archive_index.json'),16*1024**2)
        if raw is None:return {'version':1,'jobs':{}}
        value=decode(raw)
        if set(value)!={'version','jobs'} or type(value['version']) is not int or value['version']!=1 or not isinstance(value['jobs'],dict) or len(value['jobs'])>LIMIT:raise ValueError()
        for identity,sha in value['jobs'].items():
            if not re.fullmatch('[a-f0-9]{32}',identity) or not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError()
        return value
    except (OSError,ValueError,TypeError,KeyError):raise StudioError('archive_index_invalid_preserved') from None


def is_archived(root,identity):return identity in index(root)['jobs']


def archive(journal,*,before,expected=None,apply=False):
    if before.tzinfo is None or before.timestamp()>journal.clock()-86400:raise StudioError('archive_cutoff_must_be_aware_and_older_than_one_day')
    if apply and expected is None:raise StudioConflict('version_required')
    if not journal.root.exists():return {'dry_run':not apply,'eligible':0,'version':digest(None)}
    with ExitStack() as stack:
        if apply:
            stack.enter_context(lease(journal.root/'runtime.lock'))
            stack.enter_context(lease(journal.root/'generation.lock'))
            stack.enter_context(lock(journal.root/'journal.lock'))
        data,version=journal.load();existing=index(journal.root)
        if expected is not None and expected!=version:raise StudioConflict('version_conflict')
        # Preserve the newest job on every channel and all ambiguous/in-flight rows.
        protected=set()
        for channel in ('primary','android','web'):
            rows=[r for r in data['jobs'].values() if channel in r['channels']]
            if rows:protected.add(max(rows,key=lambda r:r['channels'][channel]['updated_at'])['id'])
        for identity,row in data['jobs'].items():
            # Physical publications, even corrupt ones, may be needed by Android
            # recovery. Move old assets explicitly after backup before compaction.
            if row.get('publication') and any(checked(path).exists() for path in (journal.queue/row['publication_name'],journal.queue/'android_cache'/row['publication_name'])):protected.add(identity)
        chosen={identity:row for identity,row in data['jobs'].items() if identity not in protected and row['status'] in ('completed','skipped') and not row.get('resume_requested') and datetime.fromisoformat(row['created_at'])<before and all(c['status']=='locally_sent' for c in row['channels'].values())}
        report={'dry_run':not apply,'version':version,'eligible':len(chosen),'preserved':len(data['jobs'])-len(chosen),'audio_deleted':False}
        if not apply or not chosen:return report
        verify(journal.root)
        if len(set(existing['jobs'])|set(chosen))>LIMIT:raise StudioError('archive_index_quota')
        directory=checked(journal.root/'archives');directory.mkdir(mode=0o700,exist_ok=True)
        raw=json.dumps({'version':1,'jobs':chosen},sort_keys=True,separators=(',',':'),allow_nan=False).encode();sha=hashlib.sha256(raw).hexdigest();path=checked(directory/(sha+'.json'))
        old=read(path,4*1024**2)
        if old is not None and old!=raw:raise StudioError('archive_corrupt_preserved')
        if old is None:atomic_write(path,raw.decode())
        # A crash after this write leaves duplicate metadata, never an admissible old slot.
        existing['jobs'].update({identity:sha for identity in chosen})
        atomic_write(journal.root/'archive_index.json',json.dumps(existing,sort_keys=True,separators=(',',':')))
        for identity in chosen:del data['jobs'][identity]
        journal.validate(data)
        atomic_write(journal.path,json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False))
        return report


def verify(root):
    value=index(root);groups={}
    for identity,sha in value['jobs'].items():groups.setdefault(sha,set()).add(identity)
    if len(groups)>128:raise StudioError('archive_verification_budget')
    total=0
    for sha,identities in groups.items():
        raw=read(checked(root/'archives'/(sha+'.json')),4*1024**2)
        if raw is None or hashlib.sha256(raw).hexdigest()!=sha:raise StudioError('archive_missing_or_corrupt')
        total+=len(raw)
        if total>64*1024**2:raise StudioError('archive_verification_budget')
        data=decode(raw)
        if set(data)!={'version','jobs'} or type(data['version']) is not int or data['version']!=1 or not isinstance(data['jobs'],dict) or not identities<=set(data['jobs']):raise StudioError('archive_invalid')
    return {'jobs':len(value['jobs']),'files':len(groups),'bytes':total}
