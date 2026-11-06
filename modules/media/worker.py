"""Isolated raster decoder: no HTTP, filesystem input, SVG or executable content."""
import base64
import hashlib
import io
import json
import resource
import sys
import time
import warnings
from PIL import Image,ImageOps,ImageFile

VERSION='pillow13-jpeg-v1'
MAX_BYTES=4*1024*1024
MAX_PIXELS=8_000_000
SIZES={320:(320,180),768:(768,432)}


def prepare(raw):
    if not isinstance(raw,bytes) or not 1<=len(raw)<=MAX_BYTES:raise ValueError('input_size')
    ImageFile.LOAD_TRUNCATED_IMAGES=False
    start=time.monotonic()
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw),formats=['JPEG','PNG','WEBP']) as check:
            if check.width*check.height>MAX_PIXELS or max(check.size)>8192 or min(check.size)<1:raise ValueError('pixel_limit')
            if getattr(check,'n_frames',1)!=1:raise ValueError('animation_refused')
            fmt=check.format;check.verify()
        if fmt=='JPEG' and not raw.endswith(b'\xff\xd9'):raise ValueError('truncated_jpeg')
        with Image.open(io.BytesIO(raw),formats=['JPEG','PNG','WEBP']) as source:
            source.load();oriented=ImageOps.exif_transpose(source)
            rgba=oriented.convert('RGBA');clean=Image.new('RGB',rgba.size,'white');clean.paste(rgba,mask=rgba.getchannel('A'))
    decoded=time.monotonic();variants=[]
    for size,bounds in SIZES.items():
        image=clean.copy();image.thumbnail(bounds,Image.Resampling.LANCZOS)
        output=io.BytesIO();image.save(output,'JPEG',quality=82,optimize=True,progressive=False)
        data=output.getvalue()
        with Image.open(io.BytesIO(data)) as verify:verify.load();assert verify.format=='JPEG' and not verify.getexif()
        variants.append({'size':size,'width':image.width,'height':image.height,'sha256':hashlib.sha256(data).hexdigest(),'data':base64.b64encode(data).decode(),'bytes':len(data)})
    return {'version':VERSION,'source_sha256':hashlib.sha256(raw).hexdigest(),'source_format':fmt,'variants':variants,'metrics':{'decode_ms':round((decoded-start)*1000,3),'thumbnail_ms':round((time.monotonic()-decoded)*1000,3),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}}


def main():
    resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024));resource.setrlimit(resource.RLIMIT_CPU,(5,5));resource.setrlimit(resource.RLIMIT_FSIZE,(2*1024*1024,2*1024*1024))
    try:
        raw=sys.stdin.buffer.read(MAX_BYTES+1);result=prepare(raw);sys.stdout.write(json.dumps(result));return 0
    except Exception:
        sys.stdout.write(json.dumps({'error':'image_decode_refused'}));return 2

if __name__=='__main__':raise SystemExit(main())
