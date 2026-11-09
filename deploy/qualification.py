"""Evidence ledger: no deployment, automatic PASS promotion or distribution permission."""
import argparse
from datetime import datetime,timezone
import hashlib,json,os,re,stat
from pathlib import Path
from deploy.recovery import checked

STATUSES={'PASS','FAIL','WARNING','NOT_TESTED','BLOCKED'}
GATES=('code_ci','installation_a','installation_b','frontend','rss','events','images','backup','restore','radio_short','radio_24h','android_emulator','android_device_1','android_device_2','android_device_3','tts_real','licenses','vulnerabilities','secrets','public_distribution')
KINDS={'fixture','ci','emulator','physical','operator'}
PHYSICAL={'installation_a','installation_b','android_device_1','android_device_2','android_device_3'}

def fresh(server,android):
    if any(not re.fullmatch('[0-9a-f]{40}',s) for s in (server,android)):raise ValueError('commit_required')
    return {'version':1,'references':{'server':server,'android':android},'gates':{g:{'status':'NOT_TESTED','evidence':[]} for g in GATES}}

def validate(value):
    if not isinstance(value,dict) or set(value)!={'version','references','gates'} or value['version']!=1:raise ValueError('invalid_ledger')
    if set(value['references'])!={'server','android'} or any(not re.fullmatch('[0-9a-f]{40}',s) for s in value['references'].values()):raise ValueError('invalid_references')
    if set(value['gates'])!=set(GATES):raise ValueError('invalid_gates')
    for gate,row in value['gates'].items():
        if not isinstance(row,dict) or set(row)!={'status','evidence'} or row['status'] not in STATUSES or not isinstance(row['evidence'],list) or len(row['evidence'])>32:raise ValueError('invalid_gate')
        if row['status']=='PASS' and not row['evidence']:raise ValueError('pass_requires_evidence')
        for proof in row['evidence']:
            fields={'commit','environment','kind','protocol','date','result','limits','artifact','sha256'}
            if not isinstance(proof,dict) or set(proof)!=fields or any(not isinstance(v,str) or not 1<=len(v)<=1000 for v in proof.values()):raise ValueError('invalid_evidence')
            if proof['kind'] not in KINDS or proof['result'] not in STATUSES or not re.fullmatch('[0-9a-f]{40}',proof['commit']) or not re.fullmatch('[0-9a-f]{64}',proof['sha256']):raise ValueError('invalid_proof')
            try:
                date=datetime.fromisoformat(proof['date'])
                if date.tzinfo is None:raise ValueError()
            except ValueError:raise ValueError('aware_evidence_date_required') from None
            # Reports are private; summaries never print these free-text fields.
            if any(c in ''.join(proof.values()) for c in ('\x00','\r')):raise ValueError('invalid_evidence_text')
            if row['status']=='PASS':
                if proof['result']!='PASS':raise ValueError('incompatible_pass')
                if gate in PHYSICAL and proof['kind']!='physical':raise ValueError('physical_evidence_required')
                if gate=='android_emulator' and proof['kind']!='emulator':raise ValueError('emulator_evidence_required')
                if gate in ('licenses','secrets','public_distribution') and proof['kind']!='operator':raise ValueError('operator_attestation_required')
                if gate=='radio_24h' and proof['kind'] not in ('physical','operator'):raise ValueError('endurance_evidence_required')
                expected=value['references']['android' if gate.startswith('android_') else 'server']
                if proof['commit']!=expected:raise ValueError('stale_commit_cannot_qualify')
    return value

def assess(value):
    validate(value)
    statuses={g:row['status'] for g,row in value['gates'].items()}
    return {'version':1,'gates':statuses,'public':'READY_FOR_OPERATOR_REVIEW' if all(s=='PASS' for s in statuses.values()) else 'NO_GO',
        'private':'BLOCKED_BY_RECORDED_FAILURE' if any(s=='FAIL' for s in statuses.values()) else 'CONDITIONAL_OPERATOR_AUTHORIZATION_REQUIRED',
        'unresolved':[g for g,s in statuses.items() if s!='PASS'],'deployment_authorized':False,'physical_tests_inferred':False}

def read(path):
    path=checked(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>1024*1024:raise ValueError('unsafe_report')
        raw=os.read(fd,1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('oversized_report')
        return json.loads(raw),hashlib.sha256(raw).hexdigest()
    finally:os.close(fd)

def write_new(path,value):
    path=checked(path);raw=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    if len(raw)>1024*1024:raise ValueError('report_limit')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    except BaseException:
        # Preserve partial reports as failed evidence; never replace an existing report.
        raise
    return hashlib.sha256(raw).hexdigest()

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='operation',required=True)
    init=sub.add_parser('init');init.add_argument('--server',required=True);init.add_argument('--android',required=True);init.add_argument('--output',required=True)
    report=sub.add_parser('assess');report.add_argument('--report',required=True)
    rec=sub.add_parser('record');rec.add_argument('--report',required=True);rec.add_argument('--expected',required=True);rec.add_argument('--gate',choices=GATES,required=True);rec.add_argument('--status',choices=sorted(STATUSES),required=True);rec.add_argument('--evidence',required=True);rec.add_argument('--output',required=True)
    a=p.parse_args()
    try:
        if a.operation=='init':print(json.dumps({'sha256':write_new(a.output,fresh(a.server,a.android)),'status':'created_not_qualified'}))
        else:
            value,version=read(a.report);validate(value)
            if a.operation=='assess':print(json.dumps(assess(value),sort_keys=True))
            else:
                if version!=a.expected:raise ValueError('version_conflict')
                evidence,_=read(a.evidence);value['gates'][a.gate]={'status':a.status,'evidence':[evidence]};validate(value)
                print(json.dumps({'sha256':write_new(a.output,value),'status':'recorded_not_authorized'}))
    except (OSError,ValueError,TypeError,KeyError):print(json.dumps({'status':'FAIL','reason':'qualification_refused_originals_preserved'}));raise SystemExit(1)
if __name__=='__main__':main()
