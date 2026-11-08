"""Additive bounded public projections for the opt-in frontend. No business writes."""
from dataclasses import asdict
from collections import OrderedDict
import hashlib
import threading
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import re
from bs4 import BeautifulSoup,NavigableString
from flask import request,jsonify
from modules.editorial.events import Coverage
from modules.editorial.index import read,decode,EventIndex,public_event
from modules.editorial.categories import registry
from modules.media.store import MediaStore,FALLBACK,target_for_article,target_for_event
from modules.web.web_security import render_markdown,safe_url

class PublicReadError(ValueError):pass


def regular(path,limit):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):raise PublicReadError('unsafe_public_file')
    return read(path,limit)


def date(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9]{8}',value):raise PublicReadError('invalid_date')
    try:datetime.strptime(value,'%Y%m%d')
    except ValueError:raise PublicReadError('invalid_date') from None
    return value


def document_nodes(text):
    # Same established Markdown/nh3 policy, projected as inert typed nodes, never a page scrape.
    soup=BeautifulSoup(str(render_markdown(text)),'html.parser');count=0
    tags={'p','br','hr','strong','em','b','i','del','s','blockquote','pre','code','h1','h2','h3','h4','h5','h6','ul','ol','li','table','thead','tbody','tr','th','td','a','sup','sub','dl','dt','dd'}
    def node(value,depth=0):
        nonlocal count
        count+=1
        if count>4000 or depth>16:raise PublicReadError('document_complexity_limit')
        if isinstance(value,NavigableString):return {'text':str(value)}
        tag=value.name
        if tag not in tags:return {'text':value.get_text()}
        result={'tag':'h2' if tag=='h1' else tag,'children':[node(child,depth+1) for child in value.children]}
        if tag=='a':result['href']=None if safe_url(value.get('href'))=='#' else value.get('href')
        return result
    return [node(child) for child in soup.children]


def install_public_api(app,paths,config):
    store=MediaStore(paths['data'],mode=config.get('media',{}).get('mode','disabled'))
    def options(allowed):
        if set(request.args)-set(allowed) or any(len(request.args.getlist(k))!=1 or len(request.args[k])>200 for k in request.args):raise PublicReadError('invalid_query')
    def pagination():
        try:page=int(request.args.get('page','1'));size=int(request.args.get('per_page','20'))
        except ValueError:raise PublicReadError('invalid_pagination') from None
        if not 1<=page<=10000 or not 1<=size<=50:raise PublicReadError('invalid_pagination')
        return page,size
    def respond(**data):return jsonify(status='ok',observed_at=datetime.now(timezone.utc).isoformat(),**data)
    article_cache=OrderedDict();article_cache_lock=threading.RLock()
    def articles(day):
        # Hash the bounded authoritative file every time; cache only public text,
        # never rights decisions or secret/session state. Two versions per process.
        raw=regular(paths['articles']/f'{day}_articles.json',4*1024*1024)
        key=(day,None if raw is None else hashlib.sha256(raw).hexdigest())
        with article_cache_lock:
            if key in article_cache:
                article_cache.move_to_end(key)
                return [dict(row) for row in article_cache[key]]
            rows=article_rows(raw)
            article_cache[key]=rows
            while len(article_cache)>2:article_cache.popitem(last=False)
            return [dict(row) for row in rows]
    def article_rows(raw):
        values=[] if raw is None else decode(raw)
        if not isinstance(values,list) or len(values)>2000:raise PublicReadError('invalid_articles')
        result=[]
        for value in values:
            if isinstance(value,dict) and value.get('migrated'):continue
            try:
                c=Coverage.from_article(value)
                image_target={'kind':'article','id':c.article_hash,'fingerprint':c.id,'title':c.title}
                result.append({'id':c.id,'hash':c.article_hash,'title':c.title,'original_title':c.original_title,'summary':c.summary,'link':c.link,'source':c.source,'category':c.category,
                    'published_raw':c.published_raw,'published_at':c.published_at,'publication_status':c.publication_status,'observed_raw':c.observed_raw,'observed_at':c.observed_at,'language':c.language,'_image_target':image_target})
            except (ValueError,TypeError,KeyError):continue
        return sorted(result,key=lambda r:r['published_at'] or r['observed_at'] or r['observed_raw'],reverse=True)
    @app.after_request
    def public_headers(response):
        if request.path.startswith('/api/public/'):
            response.headers['Cache-Control']='public, max-age=15' if response.status_code==200 else 'no-store'
        return response
    @app.errorhandler(PublicReadError)
    def public_error(error):return jsonify(status='unavailable',reason=str(error)),503 if str(error) in ('unsafe_public_file','unsafe_public_directory','invalid_articles','document_complexity_limit','archive_file_limit') else 400
    def guarded(function):
        from functools import wraps
        @wraps(function)
        def wrapped(*args,**kwargs):
            try:return function(*args,**kwargs)
            except PublicReadError:raise
            except (ValueError,OSError,TypeError,KeyError,RecursionError):return jsonify(status='unavailable',reason='public_data_unavailable'),503
        return wrapped
    @app.get('/api/public/site')
    @guarded
    def public_site():
        options(())
        def mount(key,default):
            value=config.get('icecast',{}).get(key,default)
            return '/stream'+value if isinstance(value,str) and re.fullmatch(r'/[A-Za-z0-9_-]{1,64}',value) else '/stream'+default
        cats=[asdict(c) for c in registry(paths['feeds_file'])]
        return respond(name=str(config.get('service',{}).get('name','Nova Atlas'))[:100],categories=cats,streams={'primary':mount('mount','/nova'),'android':mount('android_mount','/nova-android')})
    @app.get('/api/public/articles')
    @guarded
    def public_articles():
        options(('date','page','per_page','category'));day=date(request.args.get('date') or datetime.now(ZoneInfo('Europe/Paris')).strftime('%Y%m%d'));page,size=pagination()
        rows=articles(day)
        if request.args.get('category'):rows=[r for r in rows if r['category']==request.args['category']]
        selected=rows[(page-1)*size:page*size]
        targets=[row.pop('_image_target') for row in selected]
        page_rows=[{**row,'image':image} for row,image in zip(selected,store.public_many(targets))]
        return respond(date=day,articles=page_rows,total=len(rows),page=page,per_page=size)
    @app.get('/api/public/archives')
    @guarded
    def public_archives():
        options(('page','per_page'));page,size=pagination();days={}
        for directory,pattern,kind in ((paths['articles'],r'(\d{8})_articles\.json','articles'),(paths['reports'],r'(\d{8})_report\.md','report'),(paths['editions'],r'(\d{8})_(matin|midi|soir)\.md','edition')):
            if any(p.is_symlink() for p in (directory,*directory.parents)):raise PublicReadError('unsafe_public_directory')
            if not directory.exists():continue
            for index,path in enumerate(directory.iterdir()):
                if index>=10000:raise PublicReadError('archive_file_limit')
                match=re.fullmatch(pattern,path.name)
                if not match or path.is_symlink() or not path.is_file():continue
                try:date(match[1])
                except ValueError:continue
                row=days.setdefault(match[1],{'date':match[1],'articles':False,'report':False,'editions':[]})
                if kind=='edition':row['editions'].append(match[2])
                else:row[kind]=True
        rows=sorted(days.values(),key=lambda r:r['date'],reverse=True)
        for row in rows:row['editions']=sorted(row['editions'])
        return respond(days=rows[(page-1)*size:page*size],total=len(rows),page=page,per_page=size)
    @app.get('/api/public/documents/<day>/<kind>')
    @guarded
    def public_document(day,kind):
        options(());date(day)
        if kind not in ('report','matin','midi','soir'):raise PublicReadError('invalid_document_kind')
        directory=paths['reports'] if kind=='report' else paths['editions']
        raw=regular(directory/f'{day}_{kind}.md',512*1024)
        if raw is None:return jsonify(status='unavailable',reason='document_not_found'),404
        text=raw.decode('utf-8');title=next((line.lstrip('# ').strip() for line in text.splitlines() if line.startswith('# ')),kind)[:300]
        return respond(date=day,kind=kind,title=title,nodes=document_nodes(text))
    def event_rows():
        data=EventIndex(paths['articles'],paths['data']).load()
        return (data or {}).get('events',[])
    def projection(row):return {**public_event(row),'image':store.public_for(target_for_event(row))}
    @app.get('/api/public/events')
    @guarded
    def public_events():
        options(('page','per_page','category'));page,size=pagination();data=EventIndex(paths['articles'],paths['data']).load();rows=(data or {}).get('events',[])
        if request.args.get('category'):rows=[r for r in rows if request.args['category'] in [r['category'],*r['secondary_categories']]]
        rows=sorted(rows,key=lambda r:r['last_updated_at'] or '',reverse=True)
        selected=rows[(page-1)*size:page*size]
        images=store.public_many([target_for_event(row) for row in selected])
        return respond(events=[{**public_event(row),'image':image} for row,image in zip(selected,images)],total=len(rows),page=page,per_page=size,index_available=data is not None)
    @app.get('/api/public/events/<identity>')
    @guarded
    def public_event_detail(identity):
        options(());row=next((r for r in event_rows() if r['event_id']==identity),None)
        if row is None:return jsonify(status='unavailable',reason='event_not_found'),404
        return respond(event=projection(row))
