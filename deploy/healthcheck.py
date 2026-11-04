"""Readiness and storage checks, not proof of RSS/LLM/TTS business success."""
import json
from pathlib import Path
import sys
import urllib.request
from deploy.runtime import check_storage
from deploy import runtime


def process_alive(pid):
    try:
        state=Path(f'/proc/{pid}/stat').read_text().split(') ',1)[1].split()[0]
        return state not in ('Z','X')
    except (OSError,IndexError):return False


def check(role):
    metadata=json.loads(runtime.PID_FILE.read_text())
    pid=metadata['pid']
    if metadata['role']!=role or not process_alive(pid):return False
    children=Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
    if not any(process_alive(int(child)) for child in children):return False
    check_storage(role)
    if role=='web':
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:5055/api/preferences',timeout=3) as response:
            if response.status!=200:return False
    return True


if __name__=='__main__':
    try:result=check(sys.argv[1])
    except Exception:result=False
    raise SystemExit(0 if result else 1)
