"""Read-only, bounded, no-follow historical inventory and semantic projections."""
from datetime import datetime,date,timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path,PurePosixPath
import re
import stat
from urllib.parse import urlsplit
import yaml
from modules.persistence import StorageError

LIMIT=64*1024*1024
STATES={'discovered','processing','retryable','succeeded','failed','ignored'}

def canonical(value):
    def inspect(v):
        if isinstance(v,str) and '\x00' in v:raise StorageError('unsupported_nul_value')
        if isinstance(v,dict):
            for k,item in v.items():inspect(k);inspect(item)
        elif isinstance(v,list):
            for item in v:inspect(item)
    try:
        inspect(value)
    except RecursionError:raise StorageError('excessive_nesting') from None
    try:return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
    except (ValueError,TypeError,UnicodeError):raise StorageError('unsupported_value') from None


def digest(value):return hashlib.sha256(canonical(value)).hexdigest()


def unique_pairs(pairs):
    out={}
    for key,value in pairs:
        if key in out:raise StorageError('duplicate_document_key')
        out[key]=value
    return out


class StrictYAML(yaml.SafeLoader):
    def compose_node(self,parent,index):
        if self.check_event(yaml.AliasEvent):raise StorageError('yaml_aliases_unsupported')
        return super().compose_node(parent,index)

def mapping(loader,node,deep=False):
    loader.flatten_mapping(node)
    return unique_pairs([(loader.construct_object(k,deep=deep),loader.construct_object(v,deep=deep)) for k,v in node.value])
StrictYAML.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)
StrictYAML.add_constructor('tag:yaml.org,2002:timestamp',lambda loader,node:loader.construct_scalar(node))


def date_value(raw):
    if raw is None or raw=='':return {'raw':raw,'status':'missing','utc':None}
    parsed=None
    try:
        if type(raw) in (int,float):
            if not math.isfinite(raw):raise ValueError()
            parsed=datetime.fromtimestamp(raw,timezone.utc)
        elif isinstance(raw,str):
            try:parsed=datetime.fromisoformat(raw.replace('Z','+00:00'))
            except ValueError:parsed=parsedate_to_datetime(raw)
        else:raise ValueError()
        if parsed.tzinfo is None:return {'raw':raw,'status':'ambiguous_local','utc':None}
        return {'raw':raw,'status':'aware','utc':parsed.astimezone(timezone.utc).isoformat()}
    except (ValueError,TypeError,OverflowError,OSError):return {'raw':raw,'status':'invalid','utc':None}


def utc(raw):
    v=date_value(raw)['utc'];return datetime.fromisoformat(v) if v else None


def day(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{8}',value):raise StorageError('invalid_editorial_date')
    try:datetime.strptime(value,'%Y%m%d')
    except ValueError:raise StorageError('invalid_editorial_date') from None
    return value


def text(value,*,required=False,maximum=1000000):
    if value is None and not required:return None
    if not isinstance(value,str) or len(value)>maximum or '\x00' in value or (required and not value.strip()):raise StorageError('invalid_record_text')
    return value


def url(value):
    text(value,required=True,maximum=8192)
    try:
        p=urlsplit(value);_=p.port
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or any(c.isspace() for c in value):raise ValueError()
    except ValueError:raise StorageError('invalid_record_url') from None
    return value


def safe_read(root,relative):
    """Walk every directory with openat/O_NOFOLLOW, including absolute root ancestors."""
    path=PurePosixPath(relative)
    if path.is_absolute() or '..' in path.parts or not path.parts:raise StorageError('unsafe_path')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in Path(root).absolute().parts[1:]+path.parts[:-1]:
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=new
        leaf=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(leaf,'rb') as source:
            before=os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>LIMIT:raise StorageError('unsafe_file')
            raw=source.read(LIMIT+1);after=os.fstat(source.fileno())
            if len(raw)>LIMIT or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise StorageError('source_changed')
            return raw
    except OSError:raise StorageError('unsafe_or_unreadable_path') from None
    finally:os.close(fd)


def kind_for(path):
    if path=='config/feeds.yaml':return 'feeds'
    if path=='data/seen_hashes.json':return 'seen'
    if path=='data/article_processing.json':return 'attempts'
    patterns=[('articles',r'data/articles/(\d{8})_articles\.json'),('report',r'data/reports/(\d{8})_report\.md'),
        ('edition',r'data/editions/(\d{8})_(matin|midi|soir)\.md'),('post',r'data/posts/(\d{8})_\d{4}_posts\.(json|txt)')]
    for kind,pattern in patterns:
        match=re.fullmatch(pattern,path)
        if match:day(match.group(1));return kind
    raise StorageError('unsupported_historical_path')


def parse_document(path,raw):
    kind=kind_for(path)
    try:
        decoded=raw.decode('utf-8')
        if path.endswith('.json'):payload=json.loads(decoded,object_pairs_hook=unique_pairs,parse_constant=lambda _:(_ for _ in ()).throw(StorageError('nonfinite_json')))
        elif path.endswith('.yaml'):payload=yaml.load(decoded,Loader=StrictYAML)
        else:payload=decoded
        canonical(payload)
    except (UnicodeError,ValueError,yaml.YAMLError,RecursionError,TypeError):raise StorageError('malformed_document') from None
    return {'path':path,'kind':kind,'raw_sha256':hashlib.sha256(raw).hexdigest(),'semantic_sha256':digest(payload),'payload':payload,'source_text':decoded}


def inventory(root):
    root=Path(root).absolute()
    if not root.is_dir():raise StorageError('missing_input_directory')
    names=[]
    for relative in ['config/feeds.yaml','data/seen_hashes.json','data/article_processing.json']:
        path=root/relative
        if path.exists() or path.is_symlink():names.append(relative)
    for folder in ['data/articles','data/reports','data/editions','data/posts']:
        path=root/folder
        if path.is_symlink():raise StorageError('unsafe_path')
        if not path.exists():continue
        for child in path.iterdir():
            if child.is_symlink() or child.is_dir():raise StorageError('unsafe_path')
            if child.suffix in ('.json','.md','.txt'):names.append(child.relative_to(root).as_posix())
    if not names or len(names)>10000:raise StorageError('empty_or_excessive_inventory')
    return sorted(names)


def projections(documents):
    rows={name:{} for name in ['sources','articles','processing_attempts','editorial_outputs','seen_entries']}
    duplicates=0;hash_ids={}
    def add(table,identity,payload,**fields):
        nonlocal duplicates
        row={'id':identity,'payload':payload,**fields}
        old=rows[table].get(identity)
        if old:
            if canonical(old['payload'])!=canonical(payload):raise StorageError('conflicting_duplicate')
            duplicates+=1;return
        rows[table][identity]=row
    for doc in documents:
        path=doc['path'];payload=doc['payload'];kind=doc['kind']
        if kind=='feeds':
            if not isinstance(payload,dict):raise StorageError('invalid_feeds')
            for category,entries in payload.items():
                text(category,required=True,maximum=200)
                if not isinstance(entries,list):raise StorageError('invalid_feeds')
                for entry in entries:
                    if not isinstance(entry,(str,dict)):raise StorageError('invalid_feed')
                    feed=url(entry if isinstance(entry,str) else entry.get('url'))
                    name=None if isinstance(entry,str) else text(entry.get('name'))
                    enabled=True if isinstance(entry,str) else entry.get('enabled',True)
                    if type(enabled) is not bool:raise StorageError('invalid_feed_state')
                    add('sources',digest([category,feed]),entry,feed_url=feed,canonical_url=None if isinstance(entry,str) or not entry.get('canonical_url') else url(entry['canonical_url']),category=category,name=name,state='active' if enabled else 'disabled')
        elif kind=='articles':
            if not isinstance(payload,list):raise StorageError('invalid_articles')
            editorial_day=day(Path(path).name[:8]);hashes={}
            for article in payload:
                if not isinstance(article,dict):raise StorageError('invalid_article')
                link=url(article.get('link'));title=text(article.get('title'),required=True)
                legacy_hash=text(article.get('hash') or hashlib.md5(link.strip().encode()).hexdigest(),required=True,maximum=512)
                identity=digest([editorial_day,link])
                if legacy_hash in hashes and hashes[legacy_hash]!=identity:raise StorageError('conflicting_hash')
                hashes[legacy_hash]=identity;hash_ids.setdefault(legacy_hash,set()).add(identity)
                summary=text(article.get('summary'));dates={key:date_value(article.get(key)) for key in ['pub_date','timestamp']}
                add('articles',identity,article,legacy_hash=legacy_hash,editorial_day=editorial_day,original_url=link,title=title,
                    title_original=text(article.get('title_original')),source=text(article.get('source')),category=text(article.get('category')),
                    summary=summary,published_raw=text(article.get('pub_date')),dates=dates,published_at=utc(article.get('pub_date')),
                    first_seen_at=None,fetched_at=None,processed_at=utc(article.get('timestamp')),
                    publication_state='published' if summary and summary.strip() and not article.get('no_content') else 'incomplete')
        elif kind=='seen':
            if not isinstance(payload,dict):raise StorageError('invalid_seen')
            for h,value in payload.items():
                text(h,required=True,maximum=512)
                if type(value) not in (str,int):raise StorageError('invalid_seen_entry')
                try:day(str(value));status='calendar_date'
                except StorageError:status='invalid_preserved'
                add('seen_entries',digest(h),{'hash':h,'date':value},legacy_hash=h,date_raw=value,date_status=status)
        elif kind in ('report','edition','post'):
            editorial_day=day(Path(path).name[:8])
            items=payload if path.endswith('.json') else [payload]
            if not isinstance(items,list):raise StorageError('invalid_posts')
            for index,item in enumerate(items):
                if path.endswith('.json') and not isinstance(item,dict):raise StorageError('invalid_post')
                add('editorial_outputs',digest([path,index]),item,kind=kind,editorial_day=editorial_day,content=item,
                    status='validated' if isinstance(item,dict) and item.get('validated') is True else 'legacy',provenance=path)
    for doc in documents:
        if doc['kind']!='attempts':continue
        p=doc['payload']
        if not isinstance(p,dict) or p.get('version')!=1 or not isinstance(p.get('records'),dict):raise StorageError('unsupported_processing_schema')
        for h,rec in p['records'].items():
            text(h,required=True,maximum=512)
            if not isinstance(rec,dict) or rec.get('status') not in STATES or type(rec.get('attempts')) is not int or rec['attempts']<0:raise StorageError('invalid_processing_record')
            ids=hash_ids.get(h,set());article_id=next(iter(ids)) if len(ids)==1 else None
            add('processing_attempts',digest(h),rec,article_id=article_id,legacy_hash=h,state=rec['status'],attempts=rec['attempts'],
                reason=text(rec.get('reason')),next_attempt_at=utc(rec.get('next_attempt_at')),checkpoint=rec.get('prepared'),source_version=1)
            if article_id:rows['articles'][article_id]['first_seen_at']=utc(rec.get('discovered_at'))
    return {table:list(records.values()) for table,records in rows.items()},duplicates


def assemble(documents):
    documents=sorted(documents,key=lambda d:d['path'])
    rows,duplicates=projections(documents)
    manifest=[{k:d[k] for k in ['path','kind','raw_sha256','semantic_sha256']} for d in documents]
    return {'id':digest({'version':1,'files':manifest}),'version':1,'manifest':manifest,'documents':documents,
            'rows':rows,'counts':{**{t:len(records) for t,records in rows.items()},'files':len(documents),'duplicates':duplicates}}


def plan_import(root):
    names=inventory(root);documents=[];total=0
    for name in names:
        raw=safe_read(root,name);total+=len(raw)
        if total>256*1024*1024:raise StorageError('inventory_too_large')
        documents.append(parse_document(name,raw))
    return assemble(documents)


def verify_sources(root,plan):
    if inventory(root)!=[d['path'] for d in plan['documents']]:raise StorageError('source_set_changed')
    for d in plan['documents']:
        if hashlib.sha256(safe_read(root,d['path'])).hexdigest()!=d['raw_sha256']:raise StorageError('source_changed')
