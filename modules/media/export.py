"""Explicit local static projection, atomically replacing a complete image tree."""
import ctypes
import os
from pathlib import Path
import shutil
import tempfile
from modules.media.store import checked,MediaError


def export_images(service,site):
    site=checked(site);site.mkdir(parents=True,exist_ok=True)
    media=checked(site/'media');media.mkdir(exist_ok=True)
    destination=checked(media/'images')
    stage=Path(tempfile.mkdtemp(prefix='.media-export-',dir=media));stage.chmod(0o755)
    try:
        try:state,_=service.store.load() if service.store.mode=='approved_only' else ({'records':{}},None)
        except (ValueError,OSError):state={'records':{}}
        for row in state['records'].values():
            if service.valid_target(row['target']) and service.store.record_projection(row):
                directory=stage/row['approval']['id'];directory.mkdir(mode=0o755)
                for size in (320,768):
                    with open(directory/f'{size}.jpg','xb') as stream:
                        stream.write(service.store.bytes(row,size));stream.flush();os.fsync(stream.fileno());os.fchmod(stream.fileno(),0o644)
        # The staging tree is on the exported media mount, including bind mounts.
        for directory in [*stage.iterdir(),stage]:
            if directory.is_dir():
                fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:os.fsync(fd)
                finally:os.close(fd)
        if destination.exists():
            # Linux POSIX local storage: exchange entire tree; old approved copies
            # cannot survive a revocation in a newly generated export.
            function=getattr(ctypes.CDLL(None,use_errno=True),'renameat2',None)
            if function is None or function(-100,os.fsencode(stage),-100,os.fsencode(destination),2):
                raise MediaError('atomic_export_exchange_unavailable')
        else:os.rename(stage,destination)
        fd=os.open(media,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if stage.exists():shutil.rmtree(stage)
