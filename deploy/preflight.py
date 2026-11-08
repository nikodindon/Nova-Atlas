"""Read-only deployment preflight. Secret files are stat'ed, never opened."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from deploy.runtime import require_local_filesystem,DeploymentError
from deploy.recovery import checked

REQUIRED_DIRS=('config','data','data/articles','data/reports','data/editions','data/posts','site','audio_queue','audio_queue/android_cache','music','background_music','logs')
SECRETS=('admin_username','admin_password_hash','session_secret','icecast_source_password')
REPO=Path(__file__).resolve().parents[1]


def check(root,*,inspect_data=False,compose=False,min_free=4*1024**3,profiles=()):
    if set(profiles)-{'frontend','gateway','database'}:raise ValueError('unknown_profile')
    rows=[]
    def add(name,status,reason):rows.append({'check':name,'status':status,'reason':reason})
    try:root=checked(root)
    except ValueError:return {'status':'fail','checks':[{'check':'storage','status':'fail','reason':'unsafe_storage_path'}]}
    if not root.is_dir():return {'status':'fail','checks':[{'check':'storage','status':'fail','reason':'storage_missing'}]}
    for relative in REQUIRED_DIRS:
        path=root/relative
        try:
            checked(path);info=path.lstat()
            okay=stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and bool(info.st_mode&stat.S_IWUSR)
            add('directory:'+relative,'pass' if okay else 'fail','metadata_owner_write' if okay else 'missing_or_unsafe_permissions')
        except (ValueError,OSError):add('directory:'+relative,'fail','missing_or_unsafe_directory')
    configs=('config/config.yaml','config/feeds.yaml','config/bulletins.yaml')+(('config/llm_routes.yaml',) if 'gateway' in profiles else ())
    for relative in configs:
        try:
            path=checked(root/relative);info=path.lstat();okay=stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size>0
            add('file:'+relative,'pass' if okay else 'fail','regular_nonempty_file' if okay else 'invalid_file')
        except (ValueError,OSError):add('file:'+relative,'fail','missing_or_unsafe_file')
    extra=('litellm_master_key','llm_egress_key') if 'gateway' in profiles else ()
    if 'database' in profiles:extra+=('postgres_admin_password','postgres_app_password','postgres_migrator_password')
    for name in (*SECRETS,*extra):
        try:
            path=checked(root/'secrets'/name);info=path.lstat()
            okay=stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid() and not info.st_mode&0o077 and 0<info.st_size<=4096
            add('secret:'+name,'pass' if okay else 'fail','metadata_only_content_not_read' if okay else 'missing_or_unsafe_secret_metadata')
        except (ValueError,OSError):add('secret:'+name,'fail','missing_or_unsafe_secret_metadata')
    try:require_local_filesystem(root);add('filesystem','pass','local_linux_filesystem_metadata')
    except (DeploymentError,OSError):add('filesystem','fail','unsupported_or_unknown_filesystem')
    free=shutil.disk_usage(root).free
    add('free_space','pass' if free>=min_free else 'fail','minimum_4gib' if min_free==4*1024**3 else 'operator_threshold')
    add('flock_atomic_replace','not_tested','no_write_probe_performed')
    add('secret_values','not_tested','never_read_requires_runtime_validation')
    add('deployment_env','warning','operator_must_verify_storage_paths_uid_gid_and_proxy_peers')
    add('ports','not_tested','no_bind_or_network_probe_performed')
    for file in ('Dockerfile','frontend/Dockerfile','deploy/frontend/Dockerfile','deploy/Dockerfile.icecast','deploy/Dockerfile.litellm','docker-compose.database.yml'):
        text=(REPO/file).read_text()
        pins=[p for p in re.findall(r'(?:FROM |image: )([^\s]+)',text) if not p.startswith('${')]
        okay=bool(pins) and all('@sha256:' in p and ':latest' not in p for p in pins)
        add('image_pin:'+file,'pass' if okay else 'fail','versioned_digest' if okay else 'unpinned_image')
    add('registry_vulnerabilities','not_tested','offline_pin_check_is_not_a_vulnerability_scan')
    if inspect_data:
        from modules.editorial.index import read,decode
        for name,limit in (('seen_hashes.json',4*1024**2),('article_processing.json',8*1024**2),('feed_ingestion_state.json',4*1024**2)):
            try:
                raw=read(checked(root/'data'/name),limit)
                if raw is None:add('schema:'+name,'not_tested','absent_optional_state');continue
                value=decode(raw)
                if not isinstance(value,dict):raise ValueError()
                if name!='seen_hashes.json' and value.get('version')!=1:raise ValueError()
                add('schema:'+name,'pass','bounded_json_envelope_only')
            except (ValueError,OSError,TypeError):add('schema:'+name,'fail','invalid_or_unsafe_state_preserved')
        try:
            from modules.radio.studio import StudioJournal
            journal=StudioJournal(root/'data',root/'audio_queue');data,_=journal.load();count=len(data['jobs'])
            from modules.radio.studio_archive import verify
            verify(journal.root)
            add('radio_journal','fail' if count>=1024 else 'warning' if count>=900 else 'pass','quota_reached' if count>=1024 else 'near_quota_archive_required' if count>=900 else 'validated_schema')
        except (ValueError,OSError):add('radio_journal','fail','invalid_journal_preserved')
        add('audio_receipts','not_tested','use_explicit_bounded_radio_diagnostic_or_integrity_tests')
    else:add('data_schemas','not_tested','explicit_inspect_data_required')
    if compose:
        command=['docker','compose','--env-file','/dev/null','-f',str(REPO/'docker-compose.yml'),'--profile','web']
        for profile in profiles:
            command+=['-f',str(REPO/f'docker-compose.{profile}.yml')]
            command+=['--profile',profile]
        command+=['config','--quiet']
        env={k:v for k,v in os.environ.items() if k in ('PATH','LANG','DOCKER_HOST','XDG_RUNTIME_DIR')}
        env.update(ATLAS_STORAGE=str(root),ATLAS_UID=str(os.getuid()),ATLAS_GID=str(os.getgid()))
        try:
            result=subprocess.run(command,cwd=REPO,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
            add('compose','pass' if result.returncode==0 else 'fail','structural_validation_defaults_only_no_private_env')
        except (OSError,subprocess.TimeoutExpired):add('compose','not_tested','docker_unavailable_or_timed_out')
    else:add('compose','not_tested','explicit_compose_check_required')
    return {'version':1,'status':'fail' if any(r['status']=='fail' for r in rows) else 'warning' if any(r['status'] in ('warning','not_tested') for r in rows) else 'pass','free_bytes':free,'checks':rows,'production_readiness':'not_established'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--storage',required=True);parser.add_argument('--inspect-data',action='store_true');parser.add_argument('--compose',action='store_true');parser.add_argument('--profiles',default='');args=parser.parse_args()
    try:report=check(args.storage,inspect_data=args.inspect_data,compose=args.compose,profiles=tuple(filter(None,args.profiles.split(','))))
    except (OSError,ValueError):report={'status':'fail','reason':'preflight_refused'}
    print(json.dumps(report,sort_keys=True));raise SystemExit(1 if report['status']=='fail' else 0)

if __name__=='__main__':main()
