"""YAML-authoritative source management. No network on read or mutation.

The stable sidecar flock is never removed. All state changes reread under it;
SHA256 optimistic versions protect user intent. Source metadata is YAML comments,
so legacy readers and snapshot imports retain the operational format.
"""
from contextlib import contextmanager
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
import unicodedata
from urllib.parse import urlsplit,parse_qsl
import yaml
from lxml import etree
from modules.http.external import parse_url, BlockedURL

MAX_BYTES=1024*1024
MAX_SOURCES=512
META='# atlas-source '

class SourceError(ValueError):pass
class Conflict(SourceError):pass
class NotFound(SourceError):pass


def category(value):
    if not isinstance(value,str) or not 1<=len(value)<=100 or value!=value.strip() or any(unicodedata.category(c).startswith('C') for c in value) or any(c in value for c in ':#{}[]&*!|>\'"%@`<>\\'):
        raise SourceError('Catégorie invalide')
    return value


def source_url(value):
    if not isinstance(value,str) or len(value)>2048:raise SourceError('URL invalide')
    from modules.web.config_security import credential_url
    if credential_url(value):raise SourceError('URL avec credentials interdite')
    try:
        if any(k.casefold() in {'key','auth','access_key','signature','sig','jwt','bearer','pass'} for k,_ in parse_qsl(urlsplit(value).query)):raise SourceError('Paramètre confidentiel interdit')
    except ValueError:raise SourceError('URL invalide') from None
    try:target=parse_url(value)
    except BlockedURL:raise SourceError('URL externe interdite ou invalide') from None
    if target.host.lower().rstrip('.') in ('localhost','localhost.localdomain') or target.host.lower().endswith(('.localhost','.local','.internal')):raise SourceError('Destination locale interdite')
    return value


def text_name(value):
    if not isinstance(value,str) or len(value)>160 or any(unicodedata.category(c).startswith('C') for c in value):raise SourceError('Nom invalide')
    return value


def digest(value):return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Source:
    id:str
    url:str
    category:str
    enabled:bool=True
    name:str=''
    added_at:str|None=None


@dataclass
class Document:
    categories:list
    sources:list
    comments:list
    version:str=''


def scalar(value):
    try:
        # Single scalar only: no aliases, constructors, sequences or multiline values.
        if any(isinstance(token,(yaml.tokens.AnchorToken,yaml.tokens.AliasToken,yaml.tokens.TagToken)) for token in yaml.scan(value)):raise SourceError('Alias et tags YAML interdits')
        node=yaml.compose(value,Loader=yaml.SafeLoader)
        if not isinstance(node,yaml.ScalarNode) or node.tag!='tag:yaml.org,2002:str':raise SourceError('Scalaire YAML requis')
        return node.value
    except yaml.YAMLError:raise SourceError('YAML invalide') from None


def parse_document(raw):
    if len(raw.encode())>MAX_BYTES:raise SourceError('Liste trop volumineuse')
    doc=Document([],[],[],digest(raw));current=None;last=None;ids=set();pairs=set()
    for line in raw.splitlines():
        stripped=line.strip()
        if not stripped:continue
        if stripped.startswith(META):
            if last is None:raise SourceError('Métadonnées orphelines')
            try:
                meta=json.loads(stripped[len(META):])
                if not isinstance(meta,dict) or set(meta)-{'id','name','added_at'}:raise ValueError()
                identity=meta['id'];name=text_name(meta.get('name',''));added=meta.get('added_at')
                if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{64}',identity):raise ValueError()
                if added is not None:
                    if not isinstance(added,str) or len(added)>40 or datetime.fromisoformat(added).tzinfo is None:raise ValueError()
                if identity in ids-{doc.sources[last].id}:raise ValueError()
                ids.discard(doc.sources[last].id);ids.add(identity)
                doc.sources[last]=replace(doc.sources[last],id=identity,name=name,added_at=added);last=None
            except (ValueError,KeyError,TypeError):raise SourceError('Métadonnées invalides') from None
            continue
        disabled=False;payload=None
        if stripped.startswith('- #'):
            payload=stripped[3:].lstrip('#').strip();disabled=True
        elif stripped.startswith('#'):
            body=stripped[1:].strip()
            marked=body.startswith('[SPRINT0]')
            if marked:body=body[9:].strip()
            if marked or body.startswith(('http://','https://')):payload=body;disabled=True
            else:
                doc.comments.append(line);last=None;continue
        elif line[:1] not in (' ','\t') and stripped.endswith(':'):
            current=category(scalar(stripped[:-1]));last=None
            if current in doc.categories:raise SourceError('Catégorie dupliquée')
            doc.categories.append(current);continue
        elif stripped.startswith('- '):payload=stripped[2:]
        else:raise SourceError('Format YAML non pris en charge')
        if payload is None or current is None:raise SourceError('Source sans catégorie')
        if payload.startswith('[SPRINT0]'):payload=payload[9:].strip()
        if '  #' in payload:payload=payload.split('  #',1)[0].rstrip()
        url=source_url(scalar(payload))
        pair=(current,url)
        if pair in pairs:raise SourceError('Source dupliquée')
        pairs.add(pair);identity=digest(current+'\0'+url)
        if identity in ids:raise SourceError('Identifiant dupliqué')
        ids.add(identity);doc.sources.append(Source(identity,url,current,not disabled));last=len(doc.sources)-1
        if len(doc.sources)>MAX_SOURCES:raise SourceError('Trop de sources')
    return doc


def render(doc):
    lines=list(doc.comments)
    for cat in doc.categories:
        category(cat);lines.append(cat+':')
        for src in doc.sources:
            if src.category!=cat:continue
            source_url(src.url);text_name(src.name)
            # Validated HTTP URL scalars retain exact historical spelling.
            lines.append('  - '+src.url if src.enabled else '  # [SPRINT0] '+src.url)
            lines.append('  '+META+json.dumps({'id':src.id,'name':src.name,'added_at':src.added_at},ensure_ascii=False,separators=(',',':')))
        lines.append('')
    value='\n'.join(lines)+'\n';parse_document(value);return value


def read_file(path):
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return ''
    except OSError:raise SourceError('Fichier de sources inaccessible') from None
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_BYTES:raise SourceError('Fichier de sources non sûr')
        try:return stream.read(MAX_BYTES+1).decode('utf-8')
        except UnicodeError:raise SourceError('Encodage invalide') from None


def atomic_write(path,raw):
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)


class SourceManager:
    def __init__(self,path,health_path=None,*,clock=None):
        self.path=Path(path);self.health_path=health_path;self.clock=clock or time.time

    def read(self):return parse_document(read_file(self.path))

    @contextmanager
    def locked(self):
        # Parent is fixed by the application, never supplied by an API caller.
        if self.path.parent.is_symlink():raise SourceError('Répertoire non sûr')
        fd=os.open(self.path.with_name(self.path.name+'.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise SourceError('Verrou non sûr')
            deadline=time.monotonic()+5
            while True:
                try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
                except BlockingIOError:
                    if time.monotonic()>=deadline:raise Conflict('Modification en cours')
                    time.sleep(.02)
            yield
        finally:os.close(fd)

    def mutate(self,action,body,expected=None):
        if expected is not None and (not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected)):raise SourceError('Version invalide')
        with self.locked():
            raw=read_file(self.path);doc=parse_document(raw)
            if expected is not None and expected!=doc.version:raise Conflict('Version périmée, rechargez la liste')
            self._apply(doc,action,body)
            output=render(doc)
            # One latest known-good backup, written before replacement; no overwrite of a symlink.
            backup=self.path.with_name(self.path.name+'.bak')
            if backup.is_symlink():raise SourceError('Sauvegarde non sûre')
            atomic_write(backup,raw);atomic_write(self.path,output)
            doc.version=digest(output);return doc

    def save_active(self,feeds):
        """Compatibility for versioned legacy load/edit/save callers."""
        with self.locked():
            raw=read_file(self.path);doc=parse_document(raw)
            expected=getattr(feeds,'version',None)
            if raw.strip() and expected is None:raise Conflict('Lecture versionnée requise avant sauvegarde')
            if expected is not None and expected!=doc.version:raise Conflict('Version périmée')
            for cat,urls in feeds.items():
                category(cat)
                if not isinstance(urls,list) or len(urls)!=len(set(urls)):raise ValueError('Liste invalide')
                for url in urls:source_url(url)
            known={(s.category,s.url) for s in doc.sources}
            doc.sources=[replace(s,enabled=s.url in feeds.get(s.category,[])) for s in doc.sources]
            for cat,urls in feeds.items():
                if cat not in doc.categories:doc.categories.append(cat)
                for url in urls:
                    if (cat,url) not in known:doc.sources.append(Source(digest(cat+'\0'+url),url,cat))
            order={(cat,url):i for cat,urls in feeds.items() for i,url in enumerate(urls)}
            doc.sources.sort(key=lambda s:(doc.categories.index(s.category),order.get((s.category,s.url),10000)))
            output=render(doc)
            atomic_write(self.path.with_name(self.path.name+'.bak'),raw)
            atomic_write(self.path,output)


    def _apply(self,doc,action,b):
        if not isinstance(b,dict):raise SourceError('Objet requis')
        allowed={'add':{'category','url','name','enabled'},'category':{'category'},'rename_category':{'category','new_category'},
            'update':{'id','url','name','enabled','category'},'toggle':{'id'},'disable':{'id'},'enable':{'id'},'delete':{'id'},
            'move':{'id','direction','category'},'reorder':{'category','ids'},'import':{'document'}}
        if action not in allowed or set(b)-allowed[action]:raise SourceError('Action ou champs invalides')
        if action=='category':
            cat=category(b.get('category'))
            if cat in doc.categories:raise Conflict('Catégorie existante')
            doc.categories.append(cat);return
        if action=='rename_category':
            cat=category(b.get('category'));new=category(b.get('new_category'))
            if cat not in doc.categories:raise NotFound('Catégorie absente')
            if new in doc.categories:raise Conflict('Catégorie existante')
            doc.categories[doc.categories.index(cat)]=new
            doc.sources=[replace(s,category=new) if s.category==cat else s for s in doc.sources];return
        if action=='import':
            incoming=b.get('document')
            if not isinstance(incoming,Document):raise SourceError('Import invalide')
            for cat in incoming.categories:
                if cat not in doc.categories:doc.categories.append(cat)
            pairs={(s.category,s.url) for s in doc.sources}
            for s in incoming.sources:
                if (s.category,s.url) in pairs:continue
                # New membership gets its own identity; imports cannot hijack another source ID.
                identity=digest(s.category+'\0'+s.url)
                if any(x.id==identity for x in doc.sources):raise Conflict('Identité en conflit')
                doc.sources.append(replace(s,id=identity));pairs.add((s.category,s.url))
            return
        if action=='add':
            cat=category(b.get('category'));url=source_url(b.get('url'));name=text_name(b.get('name',''))
            enabled=b.get('enabled',True)
            if type(enabled) is not bool:raise SourceError('État invalide')
            if any(s.url==url and s.category==cat for s in doc.sources):raise Conflict('Source existante')
            if cat not in doc.categories:doc.categories.append(cat)
            doc.sources.append(Source(digest(cat+'\0'+url),url,cat,enabled,name,datetime.fromtimestamp(self.clock(),timezone.utc).isoformat()));return
        if action=='reorder':
            cat=category(b.get('category'));ids=b.get('ids')
            if cat not in doc.categories:raise NotFound('Catégorie absente')
            current=[s.id for s in doc.sources if s.category==cat]
            if not isinstance(ids,list) or any(not isinstance(i,str) for i in ids) or len(ids)!=len(current) or set(ids)!=set(current):raise SourceError('Ordre invalide')
            lookup={s.id:s for s in doc.sources};iterator=iter(ids)
            doc.sources=[lookup[next(iterator)] if s.category==cat else s for s in doc.sources];return
        identity=b.get('id')
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{64}',identity):raise SourceError('Identifiant invalide')
        index=next((i for i,s in enumerate(doc.sources) if s.id==identity),None)
        if index is None:raise NotFound('Source absente')
        src=doc.sources[index]
        if action=='delete':doc.sources.pop(index);return
        if action in ('toggle','enable','disable'):doc.sources[index]=replace(src,enabled=not src.enabled if action=='toggle' else action=='enable');return
        if action=='move':
            if 'category' in b:
                cat=category(b['category'])
                if cat not in doc.categories:raise NotFound('Catégorie absente')
                doc.sources[index]=replace(src,category=cat)
            else:
                if b.get('direction') not in ('up','down'):raise SourceError('Direction invalide')
                indices=[i for i,s in enumerate(doc.sources) if s.category==src.category];pos=indices.index(index)+(1 if b['direction']=='down' else -1)
                if not 0<=pos<len(indices):raise Conflict('Source au bord')
                other=indices[pos];doc.sources[index],doc.sources[other]=doc.sources[other],src
            return
        values={k:v for k,v in b.items() if k!='id'}
        if 'url' in values:source_url(values['url'])
        if 'category' in values:
            category(values['category'])
            if values['category'] not in doc.categories:raise NotFound('Catégorie absente')
        if 'name' in values:text_name(values['name'])
        if 'enabled' in values and type(values['enabled']) is not bool:raise SourceError('État invalide')
        doc.sources[index]=replace(src,**values)

    def listing(self):
        from modules.fetch.ingestion import FeedState
        doc=self.read();health_state=FeedState(self.health_path);records=health_state.records;now=self.clock();rows=[];positions={}
        for src in doc.sources:
            rec=records.get(digest(src.url),{});status=rec.get('status');observed=rec.get('last_attempt');age=max(0,now-observed) if observed is not None else None
            state={'success':'healthy','not_modified':'unchanged','invalid_feed':'degraded'}.get(status,status or 'never_checked')
            if state in ('healthy','unchanged') and rec.get('warnings'):state='degraded'
            if not health_state.writable:state='degraded'
            if not src.enabled:state='disabled'
            row=asdict(src);row.update(position=positions.get(src.category,0),health=state,observed_at=observed,observation_age_seconds=age,stale=age is not None and age>86400,
                last_success=rec.get('last_success'),http_status=rec.get('http_status'),failures=rec.get('failures',0),entries=rec.get('last_count'),next_check=rec.get('next_attempt_at'),warnings=rec.get('warnings',[]),error=rec.get('error') if health_state.writable else 'health_state_unavailable')
            positions[src.category]=row['position']+1;rows.append(row)
        return {'version':doc.version,'categories':doc.categories,'sources':rows,'stats':{'categories':len(doc.categories),'active':sum(s.enabled for s in doc.sources),'disabled':sum(not s.enabled for s in doc.sources),'total':len(doc.sources)}}

    def export(self,fmt='yaml'):
        doc=self.read()
        if fmt=='yaml':return render(doc)
        if fmt!='opml':raise SourceError('Format invalide')
        root=etree.Element('opml',version='2.0');body=etree.SubElement(root,'body')
        for cat in doc.categories:
            node=etree.SubElement(body,'outline',text=cat)
            for s in doc.sources:
                if s.category==cat:etree.SubElement(node,'outline',type='rss',text=s.name,xmlUrl=s.url,enabled=str(s.enabled).lower())
        return etree.tostring(root,encoding='unicode')

    def preview(self,content,fmt):
        if not isinstance(content,str) or len(content.encode())>MAX_BYTES:raise SourceError('Import trop volumineux')
        if fmt=='yaml':incoming=parse_document(content)
        elif fmt=='opml':
            try:
                tree=etree.fromstring(content.encode(),etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True,huge_tree=False))
                if tree.getroottree().docinfo.doctype or tree.tag!='opml' or len(list(tree.iter()))>2048:raise ValueError()
                incoming=Document([],[],[])
                def walk(node,cat='import'):
                    if len(list(node.iterancestors()))>10:raise ValueError()
                    if node.tag=='outline':
                        if node.get('xmlUrl'):
                            url=source_url(node.get('xmlUrl'));category(cat)
                            if cat not in incoming.categories:incoming.categories.append(cat)
                            enabled=node.get('enabled','true')
                            if enabled not in ('true','false'):raise ValueError()
                            incoming.sources.append(Source(digest(cat+'\0'+url),url,cat,enabled=='true',text_name(node.get('text',''))))
                        else:
                            cat=category(node.get('text','import'))
                            if cat not in incoming.categories:incoming.categories.append(cat)
                    for child in node:walk(child,cat)
                walk(tree);parse_document(render(incoming))
            except (etree.XMLSyntaxError,ValueError,TypeError):raise SourceError('OPML invalide') from None
        else:raise SourceError('Format invalide')
        current=self.read();pairs={(s.category,s.url) for s in current.sources}
        duplicates=sum((s.category,s.url) in pairs for s in incoming.sources)
        report={'version':current.version,'fingerprint':digest(content),'additions':len(incoming.sources)-duplicates,'duplicates':duplicates,'rejected':0,'categories':incoming.categories,'sources':[asdict(s) for s in incoming.sources]}
        return incoming,report
