"""Scan committed blobs only; never open private/untracked files or print matches."""
import argparse
import json
from pathlib import Path
import re
import subprocess

PATTERNS=(re.compile(rb'sk-or-v1-[A-Za-z0-9_-]{32,}'),re.compile(rb'sk-proj-[A-Za-z0-9_-]{32,}'),re.compile(rb'AKIA[A-Z0-9]{16}'))
TEXT={'.py','.md','.js','.ts','.svelte','.json','.yaml','.yml','.toml','.xml','.example','.cjs','.txt','.sh'}


def inspect(blobs):
    findings=[]
    for name,raw in blobs:
        encoded=name.encode()
        found=any(pattern.search(raw) or pattern.search(encoded) for pattern in PATTERNS)
        for pattern in PATTERNS:encoded=pattern.sub(b'[REDACTED]',encoded)
        if found:findings.append({'file':encoded.decode(),'status':'fail','reason':'credential_pattern_candidate_value_never_displayed'})
    return {'status':'fail' if findings else 'pass','scope':'committed_blobs_only_known_patterns_not_a_complete_secret_audit','findings':findings,'historical_revocation':'owner_confirmation_required_not_performed'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--repo',default=str(Path(__file__).resolve().parents[1]));args=parser.parse_args()
    def blobs():
        names=subprocess.check_output(['git','-C',args.repo,'ls-tree','-rz','--name-only','HEAD'],stderr=subprocess.DEVNULL).split(b'\0')
        for raw_name in names:
            if not raw_name:continue
            name=raw_name.decode('utf-8')
            if Path(name).suffix not in TEXT and 'Dockerfile' not in name:continue
            raw=subprocess.check_output(['git','-C',args.repo,'show','HEAD:'+name],stderr=subprocess.DEVNULL)
            if len(raw)<=2*1024**2:yield name,raw
    try:report=inspect(blobs())
    except (OSError,ValueError,subprocess.SubprocessError):report={'status':'fail','reason':'committed_scan_unavailable'}
    print(json.dumps(report,sort_keys=True));raise SystemExit(1 if report['status']=='fail' else 0)

if __name__=='__main__':main()
