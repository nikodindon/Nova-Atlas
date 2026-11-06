"""Explicit offline maintenance. No source scan, HTTP or production defaults."""
import argparse
import json
from modules.media.store import MediaStore,MediaError


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('cleanup','rebuild'))
    parser.add_argument('--data',required=True,type=__import__('pathlib').Path)
    parser.add_argument('--id');parser.add_argument('--apply',action='store_true');parser.add_argument('--expected')
    args=parser.parse_args();store=MediaStore(args.data,mode='suggest')
    try:
        if args.action=='cleanup':result=store.cleanup(apply=args.apply,expected=args.expected)
        else:
            state,version=store.load();row=state['records'].get(args.id)
            if not row or not row.get('artifact'):raise MediaError('prepared_candidate_required')
            result={'dry_run':not args.apply,'version':version,'candidate':args.id}
            if args.apply:
                from modules.editorial.index import read
                from modules.media.store import checked
                raw=read(checked(store.root/'originals'/(row['artifact']['source_sha256']+'.bin')),4*1024*1024)
                store.prepare(args.id,raw,expected=args.expected)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (ValueError,OSError):
        print(json.dumps({'error':'maintenance_refused_originals_preserved'}));return 2

if __name__=='__main__':raise SystemExit(main())
