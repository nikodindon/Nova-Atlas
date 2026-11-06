"""Explicit derived reconstruction and operator overrides; dry-run by default."""
import argparse
import json
from pathlib import Path
from modules.editorial.index import EventIndex,IndexError,read,decode


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    rebuild=sub.add_parser('rebuild');rebuild.add_argument('--apply',action='store_true');rebuild.add_argument('--expect-input');rebuild.add_argument('--days',type=int,default=14)
    overrides=sub.add_parser('overrides');overrides.add_argument('--patch',type=Path,required=True);overrides.add_argument('--apply',action='store_true');overrides.add_argument('--expect-sha');overrides.add_argument('--reason',default='')
    args=parser.parse_args(argv)
    try:
        index=EventIndex(args.root/'data/articles',args.root/'data',days=getattr(args,'days',14))
        if args.command=='rebuild':
            result=index.rebuild(apply=args.apply,expected=args.expect_input)
            output={'dry_run':not args.apply,'input_sha':result['input_sha'],'status':result['status'],'events':len(result['events']),'articles':sum(e['article_count'] for e in result['events']),'uncertain_pairs':len(result['uncertain']),'diagnostics':result['diagnostics']}
        else:
            raw=read(args.patch,256*1024)
            if raw is None:raise IndexError('patch_missing')
            result=index.update_overrides(decode(raw),apply=args.apply,expected=args.expect_sha,reason=args.reason)
            output={'dry_run':not args.apply,'current_sha':result['current_sha'],'requires_rebuild':args.apply}
        print(json.dumps(output,ensure_ascii=False));return 0
    except (ValueError,OSError,TypeError,KeyError):
        print(json.dumps({'status':'error','reason':'operation_refused_originals_preserved'}));return 2

if __name__=='__main__':raise SystemExit(main())
