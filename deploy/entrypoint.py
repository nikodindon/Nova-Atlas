"""Validate before exec; Tini forwards SIGTERM directly to the existing supervisor."""
import json
import os
import re
from pathlib import Path
import sys
from deploy.runtime import DeploymentError,read_secret,load_container_config,check_storage
from deploy import runtime


def prepare(role):
    if role not in ('web','news','radio') or os.getuid()==0:
        raise DeploymentError('One explicit role and a non-root UID are required')
    os.umask(0o077)
    os.environ['ATLAS_CONTAINER']='1'
    os.environ['ATLAS_ROLE']=role
    if role=='web':
        from werkzeug.security import check_password_hash
        username=read_secret('admin_username');hashed=read_secret('admin_password_hash');secret=read_secret('session_secret')
        if len(secret)<32 or len(username)>128 or not re.fullmatch(r'scrypt:32768:8:1\$[A-Za-z0-9]{16,64}\$[0-9a-f]{128}',hashed):
            raise DeploymentError('Configured administrator identity required')
        try:check_password_hash(hashed,'validation-only')
        except (ValueError,TypeError):raise DeploymentError('Invalid administrator hash') from None
        os.environ.update(ATLAS_ADMIN_USERNAME=username,ATLAS_ADMIN_PASSWORD_HASH=hashed,ATLAS_SESSION_SECRET=secret)
    load_container_config(runtime.ROOT/'config/config.yaml')
    check_storage(role)
    runtime.acquire_role(role)
    runtime.PID_FILE.write_text(json.dumps({'pid':os.getpid(),'role':role}))
    return [sys.executable,str(runtime.ROOT/'main.py'),'--'+role,'--config',str(runtime.ROOT/'config/config.yaml')]


def main():
    try:
        argv=prepare(sys.argv[1] if len(sys.argv)==2 else '')
        os.execv(sys.executable,argv)
    except (DeploymentError,OSError):
        print('Container preflight failed: check role, configuration, secrets and storage permissions.',file=sys.stderr)
        raise SystemExit(78)


if __name__=='__main__':main()
