"""Measure only an explicitly provided, manually annotated offline corpus."""
import argparse
from difflib import SequenceMatcher
import json
import time
import hashlib
from copy import deepcopy
from modules.editorial.events import Coverage,compare,tokens,group_coverages
from modules.editorial.index import read,decode


def measure(corpus):
    started=time.monotonic();results={}
    for name,predict in [('historical_two_words',lambda a,b:len(tokens(a.title)&tokens(b.title))>=2),
        ('historical_summary_prefix',lambda a,b:SequenceMatcher(None,a.summary[:200].lower(),b.summary[:200].lower()).ratio()>.7),
        ('conservative_v1',lambda a,b:compare(a,b).status=='group')]:
        tp=fp=fn=tn=ambiguous=0
        for pair in corpus['pairs']:
            a,b=Coverage.from_article(pair['left']),Coverage.from_article(pair['right']);proposed=predict(a,b)
            if pair['label']=='ambiguous':ambiguous+=int(proposed);continue
            tp+=int(proposed and pair['label']=='same');fp+=int(proposed and pair['label']=='different')
            fn+=int(not proposed and pair['label']=='same');tn+=int(not proposed and pair['label']=='different')
        results[name]={'true_positive':tp,'false_merge':fp,'missed_group':fn,'true_negative':tn,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,'ambiguous_proposed':ambiguous}
    sample=next(p for p in corpus['pairs'] if p['label']=='same')
    a,b=Coverage.from_article(sample['left']),Coverage.from_article(sample['right'])
    before,_=group_coverages([a,b],now='2026-11-06T12:00:00+00:00')
    restarted,_=group_coverages([b,a],previous=before,now='2026-11-06T13:00:00+00:00')
    additional=deepcopy(sample['right']);additional['link']='https://additional.invalid/followup';additional['hash']=hashlib.md5(additional['link'].encode()).hexdigest()
    expanded,_=group_coverages([a,b,Coverage.from_article(additional)],previous=before,now='2026-11-06T13:00:00+00:00')
    stability={'restart_same_ids':[e['event_id'] for e in before]==[e['event_id'] for e in restarted],'addition_keeps_id':len(expanded)==1 and expanded[0]['event_id']==before[0]['event_id']}
    return {'stability_checks':stability,'scope':corpus['scope'],'pairs':len(corpus['pairs']),'algorithms':results,'elapsed_seconds':round(time.monotonic()-started,6)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('corpus');parser.add_argument('--benchmark',type=int,default=0);args=parser.parse_args()
    corpus=decode(read(args.corpus));result=measure(corpus)
    if args.benchmark:
        if not 1<=args.benchmark<=500:parser.error('benchmark: 1–500')
        values=[]
        for i in range(args.benchmark):
            row=deepcopy(corpus['pairs'][0]['left']);row['link']=f'https://benchmark{i}.invalid/article';row['hash']=hashlib.md5(row['link'].encode()).hexdigest();values.append(Coverage.from_article(row))
        start=time.monotonic();events,_=group_coverages(values);result['benchmark']={'synthetic_articles':len(values),'events':len(events),'elapsed_seconds':round(time.monotonic()-start,6)}
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
