"""Fixed official API adapter; no arbitrary provider URL or credentials."""
import json
import re
from urllib.parse import urlencode,urlsplit
from modules.core.source_manager import source_url
from modules.discovery.source_discovery import plain,query
from modules.media.candidates import ImageCandidate

API='https://commons.wikimedia.org/w/api.php'


def records(payload):
    try:
        data=json.loads(payload)
        if not isinstance(data,dict) or 'error' in data:raise ValueError()
        pages=data.get('query',{}).get('pages',[])
        if not isinstance(pages,list) or len(pages)>5:raise ValueError()
        result=[]
        for page in pages:
            if not isinstance(page,dict) or page.get('ns')!=6 or 'missing' in page:continue
            info=page.get('imageinfo',[{}])[0];url=info.get('url','');source=info.get('descriptionurl','')
            source_url(url);source_url(source)
            if urlsplit(url).scheme!='https' or urlsplit(source).scheme!='https':continue
            if urlsplit(url).netloc!='upload.wikimedia.org' or not urlsplit(url).path.startswith('/wikipedia/commons/'):continue
            if urlsplit(source).netloc!='commons.wikimedia.org' or not urlsplit(source).path.startswith('/wiki/File:'):continue
            sha1=info.get('sha1','')
            if not re.fullmatch('[0-9a-f]{40}',sha1):continue
            if type(page.get('pageid')) is not int or type(page.get('lastrevid')) is not int:continue
            if type(info.get('size')) is not int or not 1<=info['size']<=4*1024*1024:continue
            if info.get('mime') not in ('image/jpeg','image/png','image/webp'):continue
            meta=info.get('extmetadata',{})
            if not isinstance(meta,dict) or len(json.dumps(meta))>32000:continue
            def value(name):
                v=meta.get(name,{})
                return v.get('value','') if isinstance(v,dict) and isinstance(v.get('value',''),str) else ''
            proof={'provider':'commons','page_id':page['pageid'],'page_revision':page['lastrevid'],'file_sha1':sha1,'file_timestamp':info.get('timestamp',''),
                'size':info['size'],'mime':info['mime'],'source_page':source,'license':plain(value('LicenseShortName'),100),'license_url':value('LicenseUrl'),
                'author':plain(value('Artist'),300),'credit':plain(value('Credit'),500),'restrictions':plain(value('Restrictions'),500),
                'attribution_required':value('AttributionRequired').lower()=='true','copyrighted':value('Copyrighted').lower()=='true'}
            result.append(ImageCandidate(url,'commons',source,plain(page.get('title',''),300),'commons',str(page['pageid']),proof))
        return result
    except (ValueError,TypeError,KeyError,IndexError,AttributeError):raise ValueError('invalid_commons_metadata') from None


class Commons:
    def __init__(self,getter):self.get=getter
    def fetch(self,*,term=None,page_id=None):
        params={'action':'query','format':'json','formatversion':2,'prop':'info|imageinfo','iiprop':'url|sha1|timestamp|extmetadata|size|mime','iimetadataversion':'latest','iiextmetadatalanguage':'en','iilimit':1,'maxlag':5}
        if term is not None:params.update(generator='search',gsrsearch=query(term),gsrnamespace=6,gsrlimit=5)
        elif isinstance(page_id,str) and re.fullmatch('[1-9][0-9]{0,11}',page_id):params['pageids']=page_id
        else:raise ValueError('invalid_commons_request')
        response=self.get(API+'?'+urlencode(params))
        if response.status_code!=200:raise ValueError('commons_unavailable')
        return records(response.content)
