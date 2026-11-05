"""Immutable import snapshots. Operational JSON publication is deliberately not switched."""
from datetime import datetime,timezone
import sqlalchemy as sa
from modules.persistence import StorageError
from modules.persistence import schema
from modules.persistence.database import transaction
from modules.persistence.legacy import assemble,canonical,verify_sources


def import_snapshot(engine,root,plan,*,expected,quiesced=False,after_insert=None):
    if not quiesced or expected!=plan['id']:raise StorageError('reviewed_quiescent_snapshot_required')
    verify_sources(root,plan)
    now=datetime.now(timezone.utc)
    with transaction(engine) as connection:
        if connection.dialect.name=='postgresql':
            connection.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'),{'key':int(plan['id'][:15],16)})
        existing=connection.execute(sa.select(schema.runs).where(schema.runs.c.id==plan['id'])).mappings().first()
        if existing:
            verify_sources(root,plan)
            verify_snapshot(connection,plan['id'])
            return {'snapshot':plan['id'],'result':'already_imported','counts':existing['counts']}
        connection.execute(schema.runs.insert().values(id=plan['id'],source_version=1,manifest=plan['manifest'],counts=plan['counts'],result='succeeded',created_at=now))
        for doc in plan['documents']:connection.execute(schema.documents.insert().values(snapshot_id=plan['id'],**doc))
        for name,table in schema.PROJECTIONS.items():
            for row in plan['rows'][name]:
                record={'snapshot_id':plan['id'],'created_at':now,'updated_at':now,**row}
                if name=='sources':record['imported_at']=now
                connection.execute(table.insert().values(**record))
        if after_insert is not None:after_insert(connection) # Explicit test injection, never used by CLI.
        verify_sources(root,plan)
        verify_snapshot(connection,plan['id'])
    return {'snapshot':plan['id'],'result':'imported','counts':plan['counts']}


def _equivalent(actual,expected,dialect):
    if isinstance(expected,datetime):
        if not isinstance(actual,datetime):return False
        if actual.tzinfo is None and dialect=='sqlite':actual=actual.replace(tzinfo=timezone.utc)
        return actual==expected
    return actual==expected


def verify_snapshot(connection,snapshot_id):
    run=connection.execute(sa.select(schema.runs).where(schema.runs.c.id==snapshot_id)).mappings().first()
    if run is None:raise StorageError('snapshot_not_found')
    docs=[dict(row) for row in connection.execute(sa.select(schema.documents).where(schema.documents.c.snapshot_id==snapshot_id).order_by(schema.documents.c.path)).mappings()]
    for d in docs:d.pop('snapshot_id')
    from modules.persistence.legacy import digest,kind_for,parse_document
    for d in docs:
        reconstructed=parse_document(d['path'],d['source_text'].encode('utf-8'))
        if reconstructed!=d:raise StorageError('snapshot_raw_document_mismatch')
        if kind_for(d['path'])!=d['kind'] or digest(d['payload'])!=d['semantic_sha256']:raise StorageError('snapshot_document_mismatch')
    plan=assemble(docs)
    if plan['id']!=snapshot_id or plan['manifest']!=run['manifest'] or plan['counts']!=run['counts']:raise StorageError('snapshot_manifest_mismatch')
    for name,table in schema.PROJECTIONS.items():
        actual={row['id']:dict(row) for row in connection.execute(sa.select(table).where(table.c.snapshot_id==snapshot_id)).mappings()}
        if set(actual)!={r['id'] for r in plan['rows'][name]}:raise StorageError('projection_identity_mismatch')
        for expected in plan['rows'][name]:
            row=actual[expected['id']]
            if any(not _equivalent(row[k],v,connection.dialect.name) for k,v in expected.items()):raise StorageError('projection_field_mismatch')
    return plan


def read_snapshot(engine,snapshot_id):
    with transaction(engine) as connection:
        if connection.dialect.name=='postgresql':connection.execute(sa.text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        return verify_snapshot(connection,snapshot_id)


def select_storage(mode,*,root=None,engine=None,snapshot=None):
    if mode=='legacy':
        if root is None:raise StorageError('legacy_root_required')
        from modules.persistence.legacy import plan_import
        return plan_import(root)
    if mode=='postgres':
        if engine is None or not snapshot:raise StorageError('explicit_database_snapshot_required')
        return read_snapshot(engine,snapshot)
    raise StorageError('invalid_storage_mode')
