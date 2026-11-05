"""Explicit snapshot operations. Import is a read-only dry run unless --apply."""
import argparse
import json
from pathlib import Path
import sys
from modules.persistence import StorageError


def main(argv=None):
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    imp=sub.add_parser('import');imp.add_argument('--root',required=True);imp.add_argument('--apply',action='store_true')
    imp.add_argument('--expect-snapshot');imp.add_argument('--quiesced',action='store_true')
    for name in ['verify','export']:
        cmd=sub.add_parser(name);cmd.add_argument('--snapshot',required=True)
        if name=='export':cmd.add_argument('--destination',required=True)
    mig=sub.add_parser('migrate');mig.add_argument('revision',choices=['head','base']);mig.add_argument('--allow-drop',action='store_true')
    sub.add_parser('health')
    args=parser.parse_args(argv);engine=None
    try:
        from modules.persistence.legacy import plan_import
        from modules.persistence.database import engine_from_env,available
        if args.command=='import':
            plan=plan_import(args.root)
            if not args.apply:
                print(json.dumps({'result':'dry_run','snapshot':plan['id'],'manifest':plan['manifest'],'counts':plan['counts']},sort_keys=True));return 0
            from modules.persistence.repository import import_snapshot
            if not args.quiesced or args.expect_snapshot!=plan['id']:raise StorageError('reviewed_quiescent_snapshot_required')
            engine=engine_from_env();result=import_snapshot(engine,args.root,plan,expected=args.expect_snapshot,quiesced=True)
        elif args.command=='migrate':
            from alembic import command
            from alembic.config import Config
            if args.revision=='base' and not args.allow_drop:raise StorageError('explicit_allow_drop_required')
            root=Path(__file__).resolve().parents[2];cfg=Config(str(root/'alembic.ini'))
            engine=engine_from_env('migrator')
            with engine.begin() as connection:
                cfg.attributes['connection']=connection
                if args.revision=='head':command.upgrade(cfg,'head')
                else:command.downgrade(cfg,'base')
            result={'result':'migrated','revision':args.revision}
        else:
            engine=engine_from_env()
            if args.command=='health':result={'result':'available' if available(engine) else 'unavailable'}
            else:
                from modules.persistence.repository import read_snapshot
                plan=read_snapshot(engine,args.snapshot)
                if args.command=='verify':result={'result':'verified','snapshot':plan['id'],'counts':plan['counts']}
                else:
                    from modules.persistence.export import export_snapshot
                    result=export_snapshot(plan,args.destination)
        print(json.dumps(result,sort_keys=True));return 0
    except StorageError as exc:
        print(json.dumps({'result':'failed','reason':str(exc)}),file=sys.stderr);return 2
    except Exception:
        print(json.dumps({'result':'failed','reason':'database_or_input_operation_failed'}),file=sys.stderr);return 2
    finally:
        if engine is not None:engine.dispose()

if __name__=='__main__':raise SystemExit(main())
