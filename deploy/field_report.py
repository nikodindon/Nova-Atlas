"""Summarize private observation logs without inferring uptime or qualification."""
import argparse,hashlib,json,os,stat
from pathlib import Path
from deploy.recovery import checked
from deploy.field_metrics import MAX_REPORT

def summarize(rows):
    if not isinstance(rows,list) or not rows or rows[0].get('kind')!='start':raise ValueError('incomplete_start')
    previous=-1;channels={};max_gap=0;last=None;samples=0
    for row in rows[1:]:
        if row.get('version')!=1 or row.get('kind') not in ('sample','end'):raise ValueError('unknown_schema')
        elapsed=row.get('elapsed_seconds')
        if not isinstance(elapsed,(float,int)) or not 0<=elapsed<=172800 or elapsed<previous:raise ValueError('non_monotonic_log')
        previous=elapsed
        if row['kind']=='end':continue
        if last is not None:max_gap=max(max_gap,elapsed-last)
        last=elapsed;samples+=1
        for name,value in row.get('observation',{}).get('channels',{}).items():
            if name not in ('primary','android'):raise ValueError('unknown_channel')
            target=channels.setdefault(name,{'mount_pass_samples':0,'failed_or_unobserved_samples':0,'bytes_received':0,'decode_pass_samples':0,'observed_failure_windows':[],'_open':None})
            success=value.get('mount')=='PASS' and value.get('client_bytes',0)>0 and value.get('sample_decode')=='PASS'
            target['mount_pass_samples']+=int(value.get('mount')=='PASS');target['decode_pass_samples']+=int(value.get('sample_decode')=='PASS')
            count=value.get('client_bytes',0)
            if not isinstance(count,int) or not 0<=count<=65536:raise ValueError('invalid_byte_count')
            target['bytes_received']+=count
            if not success:
                target['failed_or_unobserved_samples']+=1
                if target['_open'] is None:target['_open']=elapsed
            elif target['_open'] is not None:
                target['observed_failure_windows'].append({'first_failed_sample_seconds':target['_open'],'first_successful_sample_seconds':elapsed});target['_open']=None
    for value in channels.values():
        if value['_open'] is not None:value['observed_failure_windows'].append({'first_failed_sample_seconds':value['_open'],'first_successful_sample_seconds':None})
        del value['_open']
    ended=rows[-1].get('kind')=='end'
    return {'version':1,'status':'WARNING','samples':samples,'end_record_present':ended,'elapsed_seconds':previous,'maximum_sample_gap_seconds':max_gap,'channels':channels,
        'radio_24h':'NOT_TESTED','continuous_uptime':'NOT_TESTED','audience_delivery':'NOT_TESTED','controlled_restart':'NOT_TESTED','limits':'failure windows are observations, not exact outage durations; no automatic gate promotion'}

def load(path):
    fd=os.open(checked(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_REPORT:raise ValueError('unsafe_log')
        raw=stream.read(MAX_REPORT+1)
        if len(raw)>MAX_REPORT:raise ValueError('oversized_log')
        rows=[json.loads(row) for row in raw.splitlines()]
    if len(rows)>90000:raise ValueError('sample_limit')
    result=summarize(rows);result['source_sha256']=hashlib.sha256(raw).hexdigest();return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',required=True);a=p.parse_args()
    try:print(json.dumps(load(a.report),sort_keys=True))
    except (ValueError,OSError,KeyError,TypeError):print(json.dumps({'status':'FAIL','reason':'invalid_report_preserved'}));raise SystemExit(1)
if __name__=='__main__':main()
