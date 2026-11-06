"""Best-effort metadata capture only; never fetch image bytes during ingestion."""
from modules.media.candidates import html_images
from modules.media.store import MediaStore
from modules.media.rights import digest


def capture(data,body,final_url,article_hash,config):
    mode=config.get('media',{}).get('mode','disabled')
    if mode=='disabled':return
    store=MediaStore(data,mode=mode)
    for candidate in html_images(body,final_url):
        # Binding to a concrete published coverage is an explicit admin operation later.
        store.add(candidate,{'kind':'article_reference','id':article_hash,'fingerprint':digest([article_hash,final_url]),'title':''})
