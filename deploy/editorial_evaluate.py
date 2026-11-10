"""Offline evaluation of deterministic review signals, never a factual truth score."""
import argparse
import json
from pathlib import Path
import time
from modules.fetch.editorial import review


def evaluate(cases):
    if not isinstance(cases,list) or not 1<=len(cases)<=200:raise ValueError('invalid_corpus')
    result=[];started=time.perf_counter()
    for row in cases:
        if not isinstance(row,dict) or set(row)!={'id','title','content','summary','expected_anomaly'} or type(row['expected_anomaly']) is not bool or any(not isinstance(row[k],str) or len(row[k])>10000 for k in ('id','title','content','summary')):raise ValueError('invalid_case')
        check=review(row['title'],row['content'],row['summary'])
        result.append({'id':row['id'],'expected_anomaly':row['expected_anomaly'],
            'flagged':check['status']=='needs_review','strict_numeric_block':'numeric_disagreement' in check['reasons'],
            'reasons':check['reasons']})
    tp=sum(r['flagged'] and r['expected_anomaly'] for r in result)
    fp=sum(r['flagged'] and not r['expected_anomaly'] for r in result)
    negatives=sum(not r['expected_anomaly'] for r in result)
    positives=len(result)-negatives
    return {'version':1,'scope':'supplied_annotated_corpus_not_global_quality',
        'cases':len(result),'true_flags':tp,'false_flags':fp,'missed_anomalies':positives-tp,
        'false_positive_rate':fp/negatives if negatives else None,
        'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/positives if positives else None,
        'strict_numeric_false_blocks':sum(r['strict_numeric_block'] and not r['expected_anomaly'] for r in result),
        'elapsed_seconds':time.perf_counter()-started,'factual_verification':False,'results':result}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--corpus',required=True,type=Path);args=p.parse_args()
    try:
        if args.corpus.is_symlink() or args.corpus.stat().st_size>256*1024:raise ValueError()
        value=json.loads(args.corpus.read_text())
        if not isinstance(value,dict) or value.get('version')!=1:raise ValueError()
        print(json.dumps(evaluate(value['cases']),ensure_ascii=False,allow_nan=False))
    except (ValueError,OSError,TypeError,KeyError,RecursionError):
        print(json.dumps({'status':'refused','reason':'invalid_corpus'}));raise SystemExit(1)


if __name__=='__main__':main()
