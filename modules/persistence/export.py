"""Deterministic semantic export, published as one new directory, never replace originals."""
import ctypes
import errno
import json
import os
from pathlib import Path,PurePosixPath
import secrets
import shutil
import yaml
from modules.persistence import StorageError
from modules.persistence.legacy import canonical,digest,plan_import


def _parent_fd(path):
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.absolute().parts[1:]:
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=new
        return fd
    except OSError:os.close(fd);raise StorageError('unsafe_export_parent') from None


def _write(stage_fd,path,raw):
    fd=os.dup(stage_fd)
    try:
        parts=PurePosixPath(path).parts
        for part in parts[:-1]:
            try:os.mkdir(part,mode=0o700,dir_fd=fd)
            except FileExistsError:pass
            new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.fsync(fd);os.close(fd);fd=new
        target=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        with os.fdopen(target,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
        os.fsync(fd)
    finally:os.close(fd)


def export_snapshot(plan,destination):
    destination=Path(destination).absolute()
    if '..' in destination.parts:raise StorageError('unsafe_export_parent')
    if destination.exists() or destination.is_symlink():raise StorageError('export_destination_exists')
    parent=_parent_fd(destination.parent);stage='.atlas-export-'+secrets.token_hex(12);created=False
    try:
        os.mkdir(stage,0o700,dir_fd=parent);created=True
        fd=os.open(stage,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:
            for doc in plan['documents']:
                path=doc['path'];payload=doc['payload']
                raw=doc['source_text'].encode('utf-8')
                _write(fd,path,raw)
            # Reparse before publication, including every projection and unknown field.
            os.fsync(fd)
            exported=plan_import(destination.parent/stage)
        finally:os.close(fd)
        expected={d['path']:d['semantic_sha256'] for d in plan['documents']}
        actual={d['path']:d['semantic_sha256'] for d in exported['documents']}
        if expected!=actual or plan['counts']!=exported['counts']:raise StorageError('semantic_export_mismatch')
        fd=os.open(stage,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        try:_write(fd,'SNAPSHOT_REPORT.json',canonical({'source_snapshot':plan['id'],'export_snapshot':exported['id'],'counts':exported['counts'],'semantic_files':expected})+b'\n');os.fsync(fd)
        finally:os.close(fd)
        libc=ctypes.CDLL(None,use_errno=True)
        if not hasattr(libc,'renameat2'):raise StorageError('atomic_noreplace_unavailable')
        result=libc.renameat2(parent,os.fsencode(stage),parent,os.fsencode(destination.name),1)
        if result!=0:raise StorageError('atomic_export_publication_failed')
        created=False;os.fsync(parent)
        return {'result':'exported','snapshot':plan['id'],'counts':exported['counts'],'semantic_equivalence':True}
    except OSError:raise StorageError('export_io_failed') from None
    finally:
        if created:shutil.rmtree(stage,dir_fd=parent)
        os.close(parent)
