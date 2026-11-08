"""Explicit stopped legacy snapshots. No service control; never a live PGDATA backup."""
import argparse
from contextlib import ExitStack,contextmanager
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import stat
import tempfile
from modules.editorial.index import decode

ROOTS={'config','data','site','audio_queue','music','background_music','logs','secrets','compose.env','database_exports'}
MAX_FILES=100000
MAX_BYTES=32*1024**3
class RecoveryError(ValueError):pass


def checked(path):
    path=Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):raise RecoveryError('unsafe_path')
    return path


def inventory(root,*,hashes=False,exclude_pgdata=False):
    root=checked(root)
    if not root.is_dir():raise RecoveryError('directory_required')
    result={};size=0
    def failed_walk(_):raise RecoveryError('unreadable_tree')
    for current,dirs,files in os.walk(root,followlinks=False,onerror=failed_walk):
        if Path(current)==root and exclude_pgdata:dirs[:]=[d for d in dirs if d!='postgresql']
        for name in sorted(dirs+files):
            path=Path(current)/name;info=path.lstat();relative=path.relative_to(root).as_posix()
            if len(result)>=MAX_FILES or stat.S_ISLNK(info.st_mode) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):raise RecoveryError('unsafe_or_excessive_tree')
            if stat.S_ISREG(info.st_mode) and info.st_nlink!=1:raise RecoveryError('hardlink_refused')
            row={'kind':'directory' if stat.S_ISDIR(info.st_mode) else 'file','mode':stat.S_IMODE(info.st_mode),'size':info.st_size if stat.S_ISREG(info.st_mode) else 0,'mtime_ns':info.st_mtime_ns}
            if row['mode']&0o7000:raise RecoveryError('special_permissions_refused')
            size+=row['size']
            if size>MAX_BYTES:raise RecoveryError('snapshot_size_limit')
            if hashes and row['kind']=='file':
                fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(fd,'rb') as stream:
                    before=os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or (before.st_ino,before.st_size,before.st_mtime_ns)!=(info.st_ino,info.st_size,info.st_mtime_ns):raise RecoveryError('source_changed')
                    row['sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
                    after=os.fstat(stream.fileno())
                    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RecoveryError('source_changed')
            result[relative]=row
    return result


def sync(directory):
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def publish(stage,target):
    # No replacement, even if another process creates the target after preview.
    function=getattr(ctypes.CDLL(None,use_errno=True),'renameat2',None)
    if function is None or function(-100,os.fsencode(stage),-100,os.fsencode(target),1):raise RecoveryError('target_exists_or_atomic_publish_unavailable')
    sync(target.parent)


def copy_tree(source,target,rows):
    target.mkdir(mode=0o700)
    for name,row in rows.items():
        src=source/name;dst=target/name
        if row['kind']=='directory':dst.mkdir(mode=0o700)
        else:
            fd=os.open(src,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream,dst.open('xb') as out:
                shutil.copyfileobj(stream,out,1024*1024);out.flush();os.fsync(out.fileno())
            os.chmod(dst,row['mode']);os.utime(dst,ns=(row['mtime_ns'],row['mtime_ns']))
    for name,row in reversed(list(rows.items())):
        if row['kind']=='directory':
            os.chmod(target/name,row['mode']);os.utime(target/name,ns=(row['mtime_ns'],row['mtime_ns']));sync(target/name)
    sync(target)


@contextmanager
def stopped_locks(root,rows):
    with ExitStack() as stack:
        for path in sorted(root/name for name,row in rows.items() if row['kind']=='file' and name.endswith('.lock')):
            checked(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK);stack.callback(os.close,fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):raise RecoveryError('unsafe_lock')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RecoveryError('producer_active') from None
        yield


def validate_rows(rows):
    if not isinstance(rows,dict) or len(rows)>MAX_FILES:raise RecoveryError('invalid_manifest')
    for name,row in rows.items():
        path=PurePosixPath(name)
        if not name or path.is_absolute() or '..' in path.parts or '\\' in name or path.as_posix()!=name or path.parts[0] not in ROOTS:raise RecoveryError('unsafe_manifest_path')
        if not isinstance(row,dict) or row.get('kind') not in ('file','directory') or type(row.get('mode')) is not int or row['mode']&~0o777 or type(row.get('size')) is not int or row['size']<0:raise RecoveryError('invalid_manifest')
        if type(row.get('mtime_ns')) is not int or not -(2**63)<=row['mtime_ns']<2**63:raise RecoveryError('invalid_manifest')
        if row['kind']=='file' and (not isinstance(row.get('sha256'),str) or len(row['sha256'])!=64):raise RecoveryError('invalid_manifest')


def manifest(snapshot):
    snapshot=checked(snapshot);file=checked(snapshot/'manifest.json')
    info=file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>32*1024**2:raise RecoveryError('invalid_manifest')
    value=decode(file.read_bytes())
    if not isinstance(value,dict) or set(value)!={'version','scope','entries'} or value['version']!=1 or value['scope']!='stopped_legacy':raise RecoveryError('invalid_manifest')
    validate_rows(value['entries'])
    if inventory(snapshot/'payload',hashes=True)!=value['entries']:raise RecoveryError('snapshot_integrity_failed')
    return value['entries']


def snapshot(source,destination,*,apply=False,stopped=False,include_secrets=False,exclude_pgdata=False):
    source=checked(source);destination=checked(destination)
    if source==destination or source.is_relative_to(destination) or destination.is_relative_to(source) or destination.exists():raise RecoveryError('new_disjoint_destination_required')
    pg=source/'postgresql'
    if pg.exists():
        checked(pg)
        if not exclude_pgdata or not pg.is_dir():raise RecoveryError('pgdata_requires_explicit_exclusion_and_separate_dump')
    if not (source/'config').is_dir() or not (source/'data').is_dir() or not (source/'config/config.yaml').is_file():raise RecoveryError('managed_installation_required')
    rows=inventory(source,exclude_pgdata=exclude_pgdata)
    if set(p.parts[0] for p in map(PurePosixPath,rows))-ROOTS:raise RecoveryError('unsupported_root_pg_dump_separate')
    if 'secrets' in rows and not include_secrets:raise RecoveryError('explicit_secret_backup_required')
    report={'status':'pass','dry_run':not apply,'files':sum(r['kind']=='file' for r in rows.values()),'bytes':sum(r['size'] for r in rows.values()),'scope':'stopped_legacy','postgresql':'not_included_pg_dump_required'}
    if not apply:return report
    if not stopped:raise RecoveryError('explicit_stopped_ack_required')
    with stopped_locks(source,rows):
        rows=inventory(source,hashes=True,exclude_pgdata=exclude_pgdata)
        if shutil.disk_usage(destination.parent).free<report['bytes']+64*1024**2:raise RecoveryError('insufficient_space')
        stage=Path(tempfile.mkdtemp(prefix='.atlas-snapshot-',dir=destination.parent));stage.chmod(0o700)
        try:
            copy_tree(source,stage/'payload',rows)
            value={'version':1,'scope':'stopped_legacy','entries':rows}
            with (stage/'manifest.json').open('x') as out:os.fchmod(out.fileno(),0o600);json.dump(value,out,sort_keys=True);out.flush();os.fsync(out.fileno())
            if inventory(source,hashes=True,exclude_pgdata=exclude_pgdata)!=rows:raise RecoveryError('source_changed')
            manifest(stage);sync(stage);publish(stage,destination)
        finally:
            if stage.exists():shutil.rmtree(stage)
    return report


def restore(source,destination,*,apply=False):
    source=checked(source);destination=checked(destination)
    if destination.exists() or source==destination or destination.is_relative_to(source) or source.is_relative_to(destination):raise RecoveryError('new_disjoint_destination_required')
    rows=manifest(source)
    report={'status':'pass','dry_run':not apply,'files':sum(r['kind']=='file' for r in rows.values()),'deployment_env':'manual_retarget_required','services_started':False}
    if not apply:return report
    needed=sum(r['size'] for r in rows.values())+64*1024**2
    if shutil.disk_usage(destination.parent).free<needed:raise RecoveryError('insufficient_space')
    stage=Path(tempfile.mkdtemp(prefix='.atlas-restore-',dir=destination.parent));stage.rmdir()
    try:
        copy_tree(source/'payload',stage,rows)
        if inventory(stage,hashes=True)!=rows:raise RecoveryError('restore_integrity_failed')
        publish(stage,destination)
    finally:
        if stage.exists():shutil.rmtree(stage)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['snapshot','restore','verify']);parser.add_argument('--source',required=True);parser.add_argument('--destination');parser.add_argument('--apply',action='store_true');parser.add_argument('--stopped',action='store_true');parser.add_argument('--include-secrets',action='store_true');parser.add_argument('--exclude-pgdata',action='store_true');args=parser.parse_args()
    try:
        if args.action=='verify':report={'status':'pass','files':len(manifest(args.source))}
        else:
            if not args.destination:raise RecoveryError('destination_required')
            report=snapshot(args.source,args.destination,apply=args.apply,stopped=args.stopped,include_secrets=args.include_secrets,exclude_pgdata=args.exclude_pgdata) if args.action=='snapshot' else restore(args.source,args.destination,apply=args.apply)
    except (ValueError,OSError,TypeError,KeyError):print(json.dumps({'status':'fail','reason':'snapshot_or_restore_refused'}));raise SystemExit(1) from None
    print(json.dumps(report,sort_keys=True))

if __name__=='__main__':main()
