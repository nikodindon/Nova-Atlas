"""Explicit initialization of new PostgreSQL files; no DB/legacy changes at startup."""
import argparse
import os
from pathlib import Path
import secrets
from deploy.init import private_write
from modules.persistence import StorageError


def initialize(root):
    root=Path(root).absolute()
    if os.getuid()==0 or not (root/'compose.env').is_file() or root.is_symlink():raise StorageError('private_deployment_required')
    names=['postgres_admin_password','postgres_app_password','postgres_migrator_password']
    paths=[root/'secrets'/n for n in names]+[root/n for n in ['postgresql','migration_input','database_exports']]
    if any(p.exists() or p.is_symlink() for p in paths):raise StorageError('database_initialization_requires_new_paths')
    previous=os.umask(0o077)
    try:
        for name in names:private_write(root/'secrets'/name,secrets.token_hex(32)+'\n')
        for name in ['postgresql','migration_input','database_exports']:(root/name).mkdir(mode=0o700)
    finally:os.umask(previous)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',default='deploy/local');args=parser.parse_args()
    try:initialize(args.directory)
    except Exception:raise SystemExit('Database initialization refused; use new paths in a private deployment.') from None
    print('Private database files initialized. No database, migration or import started.')

if __name__=='__main__':main()
