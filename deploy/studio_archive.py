"""Operator-only archive, dry-run by default; never deletes MP3 or receipts."""
import argparse
from datetime import datetime
import json
from modules.radio.studio import StudioJournal
from modules.radio.studio_archive import archive


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--data',required=True);parser.add_argument('--queue',required=True);parser.add_argument('--before',required=True);parser.add_argument('--version');parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    try:result=archive(StudioJournal(args.data,args.queue),before=datetime.fromisoformat(args.before),expected=args.version,apply=args.apply)
    except (ValueError,OSError,KeyError,TypeError):print(json.dumps({'status':'fail','reason':'archive_refused_originals_preserved'}));raise SystemExit(1) from None
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
