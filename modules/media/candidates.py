"""Bounded inert candidate extraction; no image or page retrieval."""
from dataclasses import dataclass,asdict,field
from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin,urlsplit
from modules.core.source_manager import source_url
from modules.media.rights import digest
from modules.discovery.source_discovery import plain


@dataclass(frozen=True)
class ImageCandidate:
    url:str
    method:str
    source_page:str
    title:str=''
    provider:str|None=None
    resource_id:str|None=None
    proof:dict=field(default_factory=dict)

    def public(self):return asdict(self)
    @property
    def id(self):return digest([self.url,self.method,self.resource_id])


def image_url(value,base):
    if not isinstance(value,str) or not value or len(value)>2048:return None
    try:
        url=value if urlsplit(value).scheme else urljoin(base,value)
        source_url(url);return url
    except (ValueError,TypeError):return None


def rss_images(node,base):
    rows=[]
    for child in node.iter():
        tag=child.tag if isinstance(child.tag,str) else ''
        local=tag.rsplit('}',1)[-1].lower();namespace=tag.split('}')[0].lstrip('{')
        mime=(child.get('type') or '').lower();value=None;method=''
        if namespace=='http://search.yahoo.com/mrss/' and local in ('thumbnail','content'):
            if local=='thumbnail' or mime.startswith('image/') or child.get('medium')=='image':value=child.get('url');method='media:'+local
        elif local=='enclosure' and mime.startswith('image/'):value=child.get('url');method='enclosure'
        elif 'itunes' in namespace and local=='image':value=child.get('href');method='itunes:image'
        elif local=='link' and mime.startswith('image/') and child.get('rel') in ('enclosure','related'):value=child.get('href');method='atom:image'
        if value:
            url=image_url(value,child.base or base)
            if url and not any(row['url']==url for row in rows):rows.append({'url':url,'method':method})
        if len(rows)>=5:break
    return tuple(rows)


class HTMLImages(HTMLParser):
    def __init__(self,base):super().__init__(convert_charrefs=True);self.base=base;self.results=[];self.nodes=0;self.json=False;self.buffer=[]
    def add(self,value,method,title=''):
        url=image_url(value,self.base)
        if url and len(self.results)<5 and not any(c.url==url for c in self.results):self.results.append(ImageCandidate(url,method,self.base,plain(title,300)))
    def handle_starttag(self,tag,attrs):
        self.nodes+=1
        if self.nodes>5000:return
        attrs=dict(attrs)
        if tag=='meta' and (attrs.get('property') or attrs.get('name') or '').lower() in ('og:image','og:image:url','twitter:image','twitter:image:src'):
            self.add(attrs.get('content'),'html:'+str(attrs.get('property') or attrs.get('name')))
        if tag=='img' and attrs.get('itemprop') in ('image','primaryImageOfPage'):
            self.add(attrs.get('src'),'html:primary',attrs.get('alt') or '')
        if tag=='script' and attrs.get('type')=='application/ld+json':self.json=True;self.buffer=[]
    def handle_data(self,value):
        if self.json and sum(map(len,self.buffer))+len(value)<=16000:self.buffer.append(value)
    def handle_endtag(self,tag):
        if tag!='script' or not self.json:return
        self.json=False
        try:
            data=json.loads(''.join(self.buffer))
            rows=data if isinstance(data,list) else [data]
            for row in rows[:5]:
                if not isinstance(row,dict) or row.get('@type') not in ('Article','NewsArticle','ReportageNewsArticle'):continue
                images=row.get('image',[]);images=images if isinstance(images,list) else [images]
                for image in images[:5]:self.add(image.get('url') or image.get('contentUrl') if isinstance(image,dict) else image,'html:jsonld')
        except (ValueError,TypeError,RecursionError):pass


def html_images(body,final_url):
    if not isinstance(body,bytes) or len(body)>4*1024*1024:return ()
    parser=HTMLImages(final_url)
    try:parser.feed(body.decode('utf-8',errors='replace'));parser.close()
    except (ValueError,RecursionError):return ()
    return tuple(parser.results)
