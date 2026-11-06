"""Bounded RSS/Atom parser, no network/path interpretation by feedparser."""
from dataclasses import dataclass,field,asdict
import hashlib
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import io
from urllib.parse import urljoin,urlsplit
import feedparser
from bs4 import BeautifulSoup
from lxml import etree
from modules.http.external import parse_url,BlockedURL

VERSIONS={'rss','rss090','rss091n','rss091u','rss092','rss093','rss094','rss10','rss20','atom03','atom10'}


def source_date(raw):
    if not raw:return None,'missing'
    try:
        try:value=datetime.fromisoformat(raw.replace('Z','+00:00'))
        except ValueError:value=parsedate_to_datetime(raw)
        if value.tzinfo is None:return None,'ambiguous_local'
        return value.astimezone(timezone.utc),'aware'
    except (ValueError,TypeError,OverflowError):return None,'invalid'


@dataclass(frozen=True)
class FeedEntry:
    feed_url: str
    entry_id: str
    title: str
    link: str
    published_raw: str = ''
    published_at: datetime | None = None
    updated_raw: str = ''
    updated_at: datetime | None = None
    author: str = ''
    source: str = ''
    category: str = ''
    summary_raw: str = ''
    content_raw: str = ''
    retrieved_at: datetime | None = None
    published_status: str = 'missing'
    updated_status: str = 'missing'
    images: tuple = ()

    def to_dict(self):
        data=asdict(self)
        for key in ('published_at','updated_at','retrieved_at'):
            if data[key] is not None:data[key]=data[key].isoformat()
        return data

    @classmethod
    def from_dict(cls,data):
        data=dict(data)
        for key in ('published_at','updated_at','retrieved_at'):
            if data.get(key) is not None:
                value=datetime.fromisoformat(data[key])
                if value.tzinfo is None:raise ValueError('naive cache date')
                data[key]=value.astimezone(timezone.utc)
        if not isinstance(data.get('images',[]),(list,tuple)) or len(data.get('images',[]))>5:raise ValueError('invalid image candidates')
        for image in data.get('images',[]):
            from modules.media.candidates import image_url
            if not isinstance(image,dict) or set(image)!={'url','method'} or image_url(image.get('url'),'https://invalid.example/')!=image.get('url') or image.get('method') not in ('media:thumbnail','media:content','enclosure','itunes:image','atom:image'):raise ValueError('invalid image candidate')
        data['images']=tuple(data.get('images',()))
        entry=cls(**data)
        if any(not isinstance(getattr(entry,k),str) or len(getattr(entry,k))>limit for k,limit in (
            ('title',4096),('link',4096),('feed_url',4096),('entry_id',4096),('published_raw',1024),('updated_raw',1024),
            ('author',1024),('source',1024),('category',200),('summary_raw',16000),('content_raw',16000))):raise ValueError('invalid cache entry')
        parse_url(entry.link);parse_url(entry.feed_url)
        return entry

    def legacy(self):
        # Private journal only. The public article serializer keeps its existing fields.
        provenance=self.to_dict()
        for name in ('title','link','summary_raw','content_raw','feed_url'):provenance.pop(name)
        provenance['source_key']=hashlib.sha256(self.feed_url.encode()).hexdigest()
        # Bodies stay only in the bounded feed cache; avoid duplicating large RSS HTML in every retry record.
        provenance['author']=provenance['author'][:512];provenance['source']=provenance['source'][:512]
        return {'title':self.title,'link':self.link,'pub_date':self.published_raw,'ingestion':provenance}


@dataclass(frozen=True)
class FeedResult:
    status: str
    entries: tuple[FeedEntry,...] = ()
    reason: str = ''
    http_status: int | None = None
    warnings: tuple[str,...] = ()
    etag: str | None = None
    last_modified: str | None = None
    response_url: str = ''
    retrieved_at: datetime | None = None
    duration: float = 0
    retry_after: float | None = None
    deferred: bool = False
    rate_host: str = ""
    format: str = ""
    feed_title: str = ""
    description: str = ""
    language: str | None = None


def _raw_date(element,names,maximum=1024):
    for node in element:
        if isinstance(node.tag,str) and etree.QName(node).localname.lower() in names:
            return ''.join(node.itertext()).strip()[:maximum]
    return ''


def _plain(value):
    soup=BeautifulSoup(value,'html.parser')
    for node in soup(['script','style']):node.decompose()
    return soup.get_text(' ',strip=True)


def parse_feed(body,feed_url,response_url,*,retrieved_at=None,max_bytes=4*1024*1024):
    now=retrieved_at or datetime.now(timezone.utc)
    def invalid(reason):return FeedResult('invalid_feed',reason=reason,retrieved_at=now,response_url=response_url)
    if not isinstance(body,bytes) or not body or len(body)>max_bytes:return invalid('empty_or_oversized_body')
    try:
        parser=etree.XMLParser(resolve_entities=False,load_dtd=False,no_network=True,huge_tree=False,recover=False)
        root=etree.fromstring(body,parser=parser,base_url=response_url)
        if root.getroottree().docinfo.doctype:return invalid('dtd_forbidden')
        nodes=[];count=0
        for node in root.iter():
            count+=1
            if count>25000:return invalid('xml_node_limit')
            if isinstance(node.tag,str) and etree.QName(node).localname in ('item','entry'):nodes.append(node)
        if len(nodes)>1000:return invalid('entry_limit')
        data=feedparser.parse(io.BytesIO(body),response_headers={'content-type':'application/xml','content-location':response_url},
                              sanitize_html=False,resolve_relative_uris=False)
    except (etree.XMLSyntaxError,ValueError,TypeError,RecursionError):return invalid('invalid_xml')
    if data.get('version') not in VERSIONS:return invalid('unrecognized_format')
    if data.get('bozo'):
        # XML was independently validated. Encoding overrides are explicit, not silent repair.
        name=type(data.get('bozo_exception')).__name__
        if name not in ('CharacterEncodingOverride','NonXMLContentType'):return invalid('parser_error')
        warnings=['encoding_or_content_type_override']
    else:warnings=[]
    parsed=data.get('entries',[])
    if len(parsed)!=len(nodes):return invalid('entry_structure_mismatch')
    entries=[];bad=0;truncated=0
    for entry,node in zip(parsed,nodes):
        title=_plain(dict.get(entry,'title',''))
        candidates=[]
        rss=data['version'].startswith('rss')
        for child in node:
            if not isinstance(child.tag,str) or etree.QName(child).localname!='link':continue
            if child.get('rel','alternate')!='alternate':continue
            if child.get('type','text/html').split(';',1)[0].lower() not in ('text/html','application/xhtml+xml',''):continue
            raw_link=child.get('href') or ''.join(child.itertext()).strip()
            if raw_link:candidates.append((raw_link,child.base or node.base or response_url))
        if not candidates and rss:
            for child in node:
                if isinstance(child.tag,str) and etree.QName(child).localname=='guid' and child.get('isPermaLink','true').lower()=='true':
                    raw_guid=''.join(child.itertext()).strip()
                    try:
                        if urlsplit(raw_guid).scheme in ('http','https'):candidates.append((raw_guid,node.base or response_url))
                    except ValueError:continue
        link=''
        for candidate,base in candidates:
            candidate=candidate.strip()
            # An absolute URI is already the historical identity. urljoin/feedparser
            # may lowercase its scheme, silently changing the MD5 used by legacy seen.
            try:
                proposed=candidate if urlsplit(candidate).scheme else urljoin(base,candidate)
                parse_url(proposed)
            except (ValueError,BlockedURL):continue
            link=proposed;break
        if not title or not link or len(title)>4096 or len(link)>4096:bad+=1;continue
        pub=_raw_date(node,{'published','pubdate','issued'})
        if not pub and data['version'].startswith('rss'):pub=_raw_date(node,{'date'})
        updated=_raw_date(node,{'updated','modified'})
        published_at,published_status=source_date(pub);updated_at,updated_status=source_date(updated)
        summary=dict.get(entry,'summary','');content='\n'.join(c.get('value','') for c in entry.get('content',[]))
        if len(summary)>16000 or len(content)>16000:truncated+=1
        entry_id=_raw_date(node,{'id','guid'},4096) or node.get('{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about','')[:4096]
        entries.append(FeedEntry(feed_url=feed_url,entry_id=entry_id,title=title,link=link,
            published_raw=pub,published_at=published_at,updated_raw=updated,updated_at=updated_at,
            author=str(entry.get('author',''))[:1024],source=str(data.get('feed',{}).get('title',''))[:1024],
            category=str(entry.get('tags',[{}])[0].get('term',''))[:200] if entry.get('tags') else '',
            summary_raw=summary[:16000],content_raw=content[:16000],retrieved_at=now,
            published_status=published_status,updated_status=updated_status,
            images=__import__('modules.media.candidates',fromlist=['rss_images']).rss_images(node,response_url)))
    invalid_dates=sum(e.published_status=='invalid' or e.updated_status=='invalid' for e in entries)
    ambiguous_dates=sum(e.published_status=='ambiguous_local' or e.updated_status=='ambiguous_local' for e in entries)
    if invalid_dates:warnings.append('invalid_dates:'+str(invalid_dates))
    if ambiguous_dates:warnings.append('ambiguous_dates:'+str(ambiguous_dates))
    if bad:warnings.append('invalid_entries:'+str(bad))
    if truncated:warnings.append('raw_content_truncated:'+str(truncated))
    if nodes and not entries:return invalid('no_usable_entries')
    return FeedResult('success' if entries else 'empty',tuple(entries),warnings=tuple(warnings),response_url=response_url,retrieved_at=now,format=data['version'],feed_title=_plain(data.get('feed',{}).get('title',''))[:300],
        description=_plain(data.get('feed',{}).get('subtitle',data.get('feed',{}).get('description','')))[:500],
        language=str(data.get('feed',{}).get('language',''))[:40] or None)
