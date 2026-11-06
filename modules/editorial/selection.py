"""Opt-in editorial packets. All source coverage remains available as provenance."""
from copy import deepcopy
from datetime import datetime,timezone
from dataclasses import asdict
from modules.editorial.events import Coverage,group_coverages


def select(articles,*,weights=None,maximum=None,now=None,overrides=None,overrides_sha=None):
    if len(articles)>500:raise ValueError('article_limit')
    weights=weights or {};now=now or datetime.now(timezone.utc)
    if any(not isinstance(k,str) or type(v) not in (int,float) or not 0<=v<=10 for k,v in weights.items()):raise ValueError('invalid_importance')
    if maximum is not None and (type(maximum) is not int or not 1<=maximum<=100):raise ValueError('event_limit')
    coverages=[];lookup={};unusable=[]
    for a in articles:
        try:c=Coverage.from_article(a)
        except (ValueError,TypeError):unusable.append(deepcopy(a));continue
        coverages.append(c);lookup[c.id]=a
    events,_=group_coverages(coverages,now=now.isoformat(),overrides=overrides)
    scored=[]
    for event in events:
        age=(now-datetime.fromisoformat(event['latest_published_at'])).total_seconds()/3600 if event['latest_published_at'] else None
        freshness=max(0,1-max(0,age)/24) if age is not None else 0
        richness=min(1,max(len(a['summary']) for a in event['articles'])/2000)
        diversity=min(.3,max(0,event['domain_count']-1)*.1)
        score=weights.get(event['category'],1)+freshness+richness+diversity
        scored.append((score,event,{'importance':weights.get(event['category'],1),'freshness':freshness,'richness':richness,'bounded_domain_diversity':diversity}))
    scored.sort(key=lambda x:(-x[0],x[1]['event_id']))
    # Round robin categories avoids crowding out a category; singleton events stay eligible.
    queues={}
    for item in scored:queues.setdefault(item[1]['category'],[]).append(item)
    ordered=[]
    while queues:
        for cat in list(queues):
            ordered.append(queues[cat].pop(0))
            if not queues[cat]:del queues[cat]
    packets=[]
    for score,event,reasons in ordered[:maximum]:
        representative=max(event['articles'],key=lambda a:(len(a['summary']),a['id']))
        packet=deepcopy(lookup[representative['id']]);packet['title']=event['title'];packet['_editorial_event']={'id':event['event_id'],'score':round(score,4),'score_reasons':reasons,'independent_confirmation':False,'coverages':event['articles'],'overrides_sha':overrides_sha}
        packets.append(packet)
    # Invalid records are never silently erased by this optional selector.
    return packets+unusable


def opt_in(articles,config):
    settings=config.get('editorial_events',{}) if isinstance(config,dict) else {}
    if settings.get('enabled') is not True:return articles
    from pathlib import Path
    from modules.editorial.index import override_state
    overrides,version=override_state(Path(config.get('paths',{}).get('data_dir','data'))/'editorial_overrides.json')
    return select(articles,weights=settings.get('importance',{}),maximum=settings.get('max_events',20),overrides=overrides,overrides_sha=version)


def prompt_provenance(packet):
    event=packet.get('_editorial_event')
    if not event:return ''
    rows=['Rapprochement éditorial proposé, confirmation indépendante non établie.']
    for a in event['coverages']:
        rows.append(f"- {a['source'] or a['domain'] or 'Source inconnue'} | {a['link'] or 'lien indisponible'} | {a['published_raw']} | {a['title']} : {a['summary'][:400]}")
    return '\n'.join(rows)


def save_selection(data,consumer,packets):
    import json
    from pathlib import Path
    from modules.editorial.index import lock,IndexError
    from modules.core.source_manager import atomic_write
    if consumer not in ('radio','editions'):raise IndexError('invalid_consumer')
    payload={'version':1,'consumer':consumer,'prepared_at':datetime.now(timezone.utc).isoformat(),
        'independent_confirmation':False,'packets':[p['_editorial_event'] for p in packets if '_editorial_event' in p]}
    raw=json.dumps(payload,ensure_ascii=False,allow_nan=False)
    if len(raw.encode())>8*1024*1024:raise IndexError('selection_manifest_limit')
    with lock(Path(data)/'editorial-selection.lock'):atomic_write(Path(data)/('editorial_selection_'+consumer+'.json'),raw)
