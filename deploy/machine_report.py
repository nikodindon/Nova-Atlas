"""Non-identifying CPU-only operator inventory; no qualification is inferred."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys


def report(label='unclassified'):
    cpu='unknown';memory=None
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):cpu=line.split(':',1)[1].strip();break
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemTotal'):memory=int(line.split()[1]);break
    except (OSError,ValueError):pass
    root=Path(__file__).resolve().parents[1]
    return {'version':1,'operator_label':label,'qualification':'not_established','cpu_model':cpu,'logical_cpus':os.cpu_count(),'ram_total_kib':memory,'architecture':platform.machine(),'kernel':platform.release(),'python':sys.version.split()[0],
        'gpu_inference':'not_tested','voice_listening':'not_tested','network_and_24h_radio':'not_tested','multi_machine':'not_established',
        'lockfiles':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('requirements-runtime.lock','frontend/package-lock.json')}}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',choices=['A','B','unclassified'],default='unclassified');parser.add_argument('--output');args=parser.parse_args();raw=json.dumps(report(args.label),sort_keys=True,indent=2)+'\n'
    if args.output:
        fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as out:out.write(raw)
    print(raw)

if __name__=='__main__':main()
