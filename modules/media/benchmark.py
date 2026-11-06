"""Reproducible synthetic decoding measurements, no network or data directory."""
import io
import json
from PIL import Image
from modules.media.store import decode_image


def main():
    results=[]
    for fmt in ('JPEG','PNG','WEBP'):
        source=io.BytesIO();Image.new('RGB',(1280,720),'#123456').save(source,fmt)
        row=decode_image(source.getvalue())
        results.append({'input_format':fmt,'input_bytes':len(source.getvalue()),'dimensions':[1280,720],
            'derivatives':[{k:v[k] for k in ('size','width','height','bytes')} for v in row['variants']],'metrics':row['metrics']})
    print(json.dumps({'scope':'3 synthetic flat-colour images, one isolated process each; not representative of editorial coverage','results':results},indent=2))

if __name__=='__main__':main()
