"""Explainable complete-link grouping. Similarity is evidence, never verification."""
from dataclasses import dataclass,asdict,field
from functools import lru_cache
from datetime import datetime,timezone
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlsplit
from bs4 import BeautifulSoup
from modules.fetch.feed_parser import source_date
from modules.web.config_security import credential_url
from modules.web.web_security import safe_url

ALGORITHM='conservative-complete-link-v1'
STOP=set('le la les de du des un une au aux en et a à par pour dans sur avec son sa ses est sont ce cette ces se qui que apres avant the of in to and is are for on at an that this it as was be by or from has have says said will new plus selon dont lors mais leur leurs les'.split())
ACTIONS={'demissionne':'demission','demission':'demission','resigns':'demission','resignation':'demission','nomme':'nomination','nomination':'nomination','attaque':'attaque','attack':'attaque','attentat':'attaque','election':'election','elections':'election','scrutin':'election','adopte':'adoption','adoption':'adoption','adopts':'adoption','baisse':'baisse','abaisse':'baisse','hausse':'hausse','releve':'hausse','augmente':'hausse','annonce':'annonce','announces':'annonce','annoncee':'annonce','accident':'accident','crash':'accident','signe':'signature','signature':'signature','suspend':'suspension','suspension':'suspension','condamne':'condamnation','condamnation':'condamnation','lance':'lancement','lancement':'lancement','quitte':'demission','remporte':'victoire','victoire':'victoire','rejette':'rejet','rejet':'rejet'}
DENIAL={'dement','dementi','nie','denies','denial','faux','fausse','not','pas','aucun'}
DAYS={'lundi','mardi','mercredi','jeudi','vendredi','samedi','dimanche','monday','tuesday','wednesday','thursday','friday','saturday','sunday'}


def sha(value):return hashlib.sha256(value.encode()).hexdigest()

def plain(value,maximum):
    soup=BeautifulSoup(str(value)[:maximum*3],'html.parser')
    for node in soup(['script','style']):node.decompose()
    return ' '.join(soup.get_text(' ',strip=True).split())[:maximum]


def normalize(value):
    text=''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c))
    return re.findall(r'[^\W_]+',text,re.UNICODE)


def tokens(value,maximum=256):return frozenset(ACTIONS.get(w,w) for w in normalize(value)[:maximum] if len(w)>2 and w not in STOP)


def jaccard(a,b):return len(a&b)/len(a|b) if a|b else 0.0


def entities(value):
    # A lexical anchor heuristic, NOT a named-entity recognizer.
    return frozenset(w for raw in re.findall(r'\b[^\W\d_][\w-]*',value) if raw[:1].isupper() for w in normalize(raw) if len(w)>2 and w not in STOP and w not in ACTIONS)


def public_link(value):
    if not isinstance(value,str) or credential_url(value) or safe_url(value)=='#':return None
    return value


@dataclass(frozen=True)
class Coverage:
    id:str
    article_hash:str
    title:str
    original_title:str
    summary:str
    link:str|None
    source:str
    domain:str|None
    category:str
    published_raw:str
    published_at:str|None
    publication_status:str
    observed_raw:str
    observed_at:str|None
    observation_status:str
    language:str|None=None
    first_seen_at:str|None=None
    fetched_at:str|None=None
    processed_at:str|None=None
    locations:tuple=()

    @classmethod
    def from_article(cls,a,location=''):
        if not isinstance(a,dict) or not isinstance(a.get('title'),str) or not a['title'].strip():raise ValueError('invalid_title')
        for key in ('summary','title_original','link','source','category','pub_date','timestamp','hash'):
            if a.get(key) is not None and (not isinstance(a[key],str) or len(a[key])>100000):raise ValueError('invalid_field')
        if len(a.get('category') or 'monde')>100:raise ValueError('invalid_category')
        title=plain(a['title'],1000);original=plain(a.get('title_original') or a['title'],1000)
        link=public_link(a.get('link'));summary=plain(a.get('summary') or '',4000)
        historical=a.get('hash') or 'derived:'+sha(a.get('link') or title)
        pub=a.get('pub_date') or '';obs=a.get('timestamp') or ''
        published,status=source_date(pub);observed,obsstatus=source_date(obs)
        identity=sha(json.dumps([historical,a.get('link'),pub,obs,a.get('category'),title,summary],ensure_ascii=True,separators=(',',':')))
        domain=urlsplit(link).hostname.lower() if link else None
        return cls(identity,historical,title,original,summary,link,plain(a.get('source') or '',200),domain,a.get('category') or 'monde',pub[:1024],published.isoformat() if published else None,status,obs[:100],observed.isoformat() if observed else None,obsstatus,
            a.get('language') if isinstance(a.get('language'),str) and len(a['language'])<=40 else None,
            *[(stamp.isoformat() if stamp else None) for stamp,status in (source_date(a.get(name)) if isinstance(a.get(name),str) else (None,'missing') for name in ('first_seen_at','fetched_at','processed_at'))],(location,) if location else ())


@dataclass(frozen=True)
class Decision:
    status:str
    reasons:tuple
    title_similarity:float
    summary_similarity:float


@lru_cache(maxsize=1024)
def features(c):
    title=tokens(c.title);words=set(normalize(c.title))
    return title,tokens(c.summary,512),words,entities(c.title),set(re.findall(r'\d+(?:[.,]\d+)?',c.title))


def compare(a,b):
    ta,sa,wa,ea,na=features(a);tb,sb,wb,eb,nb=features(b)
    title=jaccard(ta,tb);summary=jaccard(sa,sb)
    reasons=[];negative=[]
    if na!=nb:negative.append('different_headline_numbers')
    summary_numbers_a=set(re.findall(r'\d+(?:[.,]\d+)?',a.summary));summary_numbers_b=set(re.findall(r'\d+(?:[.,]\d+)?',b.summary))
    if summary_numbers_a and summary_numbers_b and summary_numbers_a!=summary_numbers_b:negative.append('different_summary_numbers')
    da,db=wa&DAYS,wb&DAYS
    if da and db and da!=db:negative.append('different_explicit_days')
    denied=bool(wa&DENIAL)!=bool(wb&DENIAL)
    if denied:negative.append('possible_denial_or_disagreement')
    aa,ab=ta&set(ACTIONS.values()),tb&set(ACTIONS.values())
    if aa and ab and not aa&ab:negative.append('different_actions')
    if a.published_at and b.published_at:
        delta=abs((datetime.fromisoformat(a.published_at)-datetime.fromisoformat(b.published_at)).total_seconds())
        if delta>18*3600:negative.append('publication_window_exceeded')
        else:reasons.append('aware_publications_within_18h')
    else:negative.append('publication_time_unknown_or_ambiguous')
    shared=ea&eb
    if shared:reasons.append('shared_lexical_anchors')
    if aa&ab:reasons.append('shared_action')
    if title>=.82:reasons.append('high_title_overlap')
    if summary>=.5:reasons.append('summary_overlap')
    if negative:return Decision('uncertain' if title>=.4 else 'separate',tuple(negative+reasons),round(title,4),round(summary,4))
    match=title>=.82 and len(ta&tb)>=6 and bool(shared) and bool(aa&ab) and summary>=.5 and min(len(sa),len(sb))>=6
    return Decision('group' if match else 'uncertain' if title>=.4 else 'separate',tuple(reasons or ['insufficient_evidence']),round(title,4),round(summary,4))


@dataclass(frozen=True)
class EditorialEvent:
    event_id:str
    title:str
    category:str
    secondary_categories:tuple
    article_hashes:tuple
    articles:tuple
    sources:tuple
    domains:tuple
    article_count:int
    domain_count:int
    first_discovered_at:str|None
    last_updated_at:str
    event_at:str|None
    latest_published_at:str|None
    status:str
    confidence:str
    disagreements:tuple
    algorithm:str=ALGORITHM
    independent_confirmation:bool=False
    reasons:tuple=()
    created_at:str=''

    def public(self):return asdict(self)


def group_coverages(coverages,*,previous=None,overrides=None,now=None):
    if len(coverages)>500:raise ValueError('article_limit')
    now=now or datetime.now(timezone.utc).isoformat();overrides=overrides or {}
    lookup={c.id:c for c in coverages};items=sorted(lookup.values(),key=lambda c:c.id)
    blocked={frozenset(p) for p in overrides.get('separate',[])};forced={frozenset(p) for p in overrides.get('merge',[])}
    if blocked&forced:raise ValueError('conflicting_overrides')
    decisions={};uncertain=[];groups=[]
    def decision(a,b):
        key=tuple(sorted((a.id,b.id)))
        if key not in decisions:decisions[key]=compare(a,b)
        return decisions[key]
    def allows(a,b):
        pair=frozenset((a.id,b.id))
        return pair not in blocked and (pair in forced or decision(a,b).status=='group')
    # Human explicit pairs form reviewed groups, with split constraints always winning.
    for pair in sorted(forced,key=lambda p:sorted(p)):
        members=[lookup[i] for i in pair if i in lookup]
        if len(members)!=2:continue
        indices=[i for i,g in enumerate(groups) if any(c.id in pair for c in g)]
        merged=members+[c for i in indices for c in groups[i] if c.id not in pair]
        if len(merged)>50:raise ValueError('group_limit')
        if any(frozenset((a.id,b.id)) in blocked for i,a in enumerate(merged) for b in merged[i+1:]):raise ValueError('conflicting_overrides')
        groups=[g for i,g in enumerate(groups) if i not in indices]+[sorted(merged,key=lambda c:c.id)]
    assigned={c.id for g in groups for c in g}
    for c in items:
        if c.id in assigned:continue
        matches=[g for g in groups if len(g)<50 and all(allows(c,member) for member in g)]
        if len(matches)==1:matches[0].append(c)
        else:groups.append([c])
    # Explanations are bounded; uncertain relations never become automatic edges.
    for i,a in enumerate(items):
        for b in items[i+1:]:
            d=decision(a,b)
            if d.status=='uncertain' and len(uncertain)<500:uncertain.append({'left':a.id,'right':b.id,**asdict(d)})
    previous=previous or [];used=set();events=[]
    # Assign each former event to one deterministic maximum-overlap descendant.
    group_sets=[{c.id for c in g} for g in groups];owners={}
    for old in previous:
        oldids={a['id'] for a in old.get('articles',[])}
        options=[(len(s&oldids),tuple(sorted(s)),i) for i,s in enumerate(group_sets) if s&oldids]
        if options:owners[old['event_id']]=max(options,key=lambda v:(v[0],v[1]))[2]
    for i,g in enumerate(groups):
        g=sorted(g,key=lambda c:c.id);old=[e for e in previous if owners.get(e['event_id'])==i]
        eventid=min((e['event_id'] for e in old),default='evt_'+sha(ALGORITHM+'\0'+'\0'.join(c.id for c in g))[:32])
        representative=max(g,key=lambda c:(len(c.summary),c.id));titles=overrides.get('titles',{})
        title=next((titles[c.id] for c in g if c.id in titles),representative.title)
        categories=tuple(dict.fromkeys(c.category for c in g));domains=tuple(sorted({c.domain for c in g if c.domain}));sources=tuple(sorted({c.source for c in g if c.source}))
        external_disagreement=any(p['left'] in {c.id for c in g} or p['right'] in {c.id for c in g} for p in uncertain if 'possible_denial_or_disagreement' in p['reasons'])
        disagree=tuple(sorted({reason for a in g for b in g if a.id<b.id for reason in decision(a,b).reasons if 'disagreement' in reason or reason.startswith('different_')}))
        if external_disagreement:disagree=tuple(sorted(set(disagree)|{'possible_denial_or_disagreement'}))
        manual=any(frozenset((a.id,b.id)) in forced for a in g for b in g if a.id!=b.id)
        evidence=tuple(sorted({r for a in g for b in g if a.id<b.id for r in decision(a,b).reasons}))
        previous_event=next((e for e in old if e['event_id']==eventid),None)
        unchanged=previous_event is not None and json.dumps(previous_event.get('articles'),sort_keys=True)==json.dumps([asdict(c) for c in g],sort_keys=True) and previous_event.get('title')==title
        events.append(EditorialEvent(eventid,title,representative.category,tuple(c for c in categories if c!=representative.category),tuple(c.article_hash for c in g),tuple(asdict(c) for c in g),sources,domains,len(g),len(domains),min((c.first_seen_at for c in g if c.first_seen_at),default=None),
            previous_event['last_updated_at'] if unchanged else now,None,max((c.published_at for c in g if c.published_at),default=None),
            'human_association' if manual else 'grouped' if len(g)>1 else 'single', 'human_reviewed' if manual else 'conservative_lexical' if len(g)>1 else 'insufficient_for_group',disagree,reasons=evidence,created_at=previous_event.get('created_at',now) if previous_event else now).public())
    events.sort(key=lambda e:e['event_id'])
    return events,uncertain


class VerificationAdvisor:
    """Future atlas-verify adapter. Never invoked or used as sole grouping evidence."""
    alias='atlas-verify'
    enabled=False
    def review(self,proposal):
        if not isinstance(proposal,dict) or len(json.dumps(proposal,ensure_ascii=True))>16384:raise ValueError('proposal_limit')
        if self.enabled:raise NotImplementedError('Provider activation requires a dedicated reviewed implementation')
        return {'status':'disabled','alias':self.alias,'decision':None}
