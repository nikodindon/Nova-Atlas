"""Synthetic files and SQLite unit transactions; real PostgreSQL/Alembic in CI smoke."""
import copy
from datetime import datetime,timezone
import io
import json
import os
from pathlib import Path
import subprocess
_REAL_POPEN=subprocess.Popen
import sqlalchemy as sa
import yaml
import pytest
from alembic import command
from alembic.config import Config
from modules.persistence import StorageError,runtime_mode,schema
from modules.persistence.legacy import plan_import,verify_sources,date_value,canonical,safe_read
from modules.persistence.repository import import_snapshot,read_snapshot,select_storage
from modules.persistence.export import export_snapshot
from modules.persistence.cli import main as cli

ROOT=Path(__file__).resolve().parents[1]


def synthetic(root):
    for folder in ['config','data/articles','data/reports','data/editions','data/posts']:(root/folder).mkdir(parents=True,exist_ok=True)
    article={'hash':'synthetic-hash','link':'https://example.invalid/story','title':'Été 🌍','title_original':'夏','summary':'Résumé validé',
             'source':'Synthetic','category':'tech','pub_date':'Sun, 01 Nov 2026 10:00:00 GMT','timestamp':'2026-11-01T11:10:00','extra':{'unknown':['preserved',1]}}
    files={'data/articles/20261101_articles.json':[article],
           'data/seen_hashes.json':{'synthetic-hash':'20261101','future':'20990101','bad':'20261345'},
           'data/article_processing.json':{'version':1,'unknown_root':'kept','records':{'synthetic-hash':{'status':'processing','attempts':2,'discovered_at':1793527200,'next_attempt_at':1793530800,'prepared':article,'target_day':'20261101','unknown':'preserved'}}},
           'data/posts/20261101_1200_posts.json':[{'slot':'20261101_1200','post_x':'Texte','validated':False,'extra':3}]}
    for name,value in files.items():(root/name).write_text(json.dumps(value,ensure_ascii=False))
    (root/'config/feeds.yaml').write_text('tech:\n  - https://example.invalid/feed\n  - url: https://example.invalid/other\n    enabled: false\n    name: Source 🌍\n    custom: [a, b]\n')
    (root/'data/reports/20261101_report.md').write_text('# Rapport\n\nÉté 🌍\n')
    (root/'data/editions/20261101_matin.md').write_text('# Matin\n\nInformation\n')
    (root/'data/posts/20261101_1200_posts.txt').write_text('Posts lisibles\n')
    return root


@pytest.fixture
def legacy(tmp_path):return synthetic(tmp_path/'legacy')


@pytest.fixture
def engine():
    engine=sa.create_engine('sqlite://',hide_parameters=True)
    @sa.event.listens_for(engine,'connect')
    def foreign_keys(connection,record):connection.execute('PRAGMA foreign_keys=ON')
    # Unit fixture only. Production uses the checked-in Alembic revision.
    schema.metadata.create_all(engine)
    yield engine
    engine.dispose()


def apply(engine,root,**kwargs):
    plan=plan_import(root)
    return import_snapshot(engine,root,plan,expected=plan['id'],quiesced=True,**kwargs)


def test_dry_run_has_no_db_or_source_side_effects(legacy,monkeypatch,capsys):
    from modules.persistence import database
    monkeypatch.setattr(database,'engine_from_env',lambda *a:pytest.fail('Dry run contacted database'))
    before={p.relative_to(legacy):p.read_bytes() for p in legacy.rglob('*') if p.is_file()}
    assert cli(['import','--root',str(legacy)])==0
    result=json.loads(capsys.readouterr().out);assert result['result']=='dry_run'
    assert result['counts']['articles']==1 and result['counts']['files']==8
    assert before=={p.relative_to(legacy):p.read_bytes() for p in legacy.rglob('*') if p.is_file()}
    assert 'Résumé' not in json.dumps(result)


def test_import_idempotent_checkpoint_and_unknown_fields(engine,legacy):
    first=apply(engine,legacy);second=apply(engine,legacy)
    assert first['result']=='imported' and second['result']=='already_imported'
    plan=read_snapshot(engine,first['snapshot'])
    assert plan['rows']['articles'][0]['payload']['extra']['unknown']==['preserved',1]
    assert plan['rows']['processing_attempts'][0]['attempts']==2
    assert plan['rows']['processing_attempts'][0]['checkpoint']['summary']=='Résumé validé'
    assert plan['rows']['articles'][0]['processed_at'] is None
    assert plan['rows']['articles'][0]['first_seen_at'].tzinfo is not None
    with engine.connect() as connection:assert connection.execute(sa.select(sa.func.count()).select_from(schema.runs)).scalar()==1


def test_atomic_export_round_trip_and_stable_ids(engine,legacy,tmp_path):
    result=apply(engine,legacy);plan=read_snapshot(engine,result['snapshot']);destination=tmp_path/'export'
    assert export_snapshot(plan,destination)['semantic_equivalence'] is True
    exported=plan_import(destination)
    assert {d['path']:canonical(d['payload']) for d in plan['documents']}=={d['path']:canonical(d['payload']) for d in exported['documents']}
    assert plan['counts']==exported['counts']
    assert {a['id'] for a in plan['rows']['articles']}=={a['id'] for a in exported['rows']['articles']}
    second=apply(engine,destination);assert read_snapshot(engine,second['snapshot'])['counts']==plan['counts']
    with pytest.raises(StorageError,match='exists'):export_snapshot(plan,destination)


def test_failed_transaction_rolls_back_and_can_restart(engine,legacy):
    def crash(connection):raise RuntimeError('synthetic crash')
    with pytest.raises(StorageError,match='database_transaction_failed'):apply(engine,legacy,after_insert=crash)
    with engine.connect() as connection:
        assert connection.execute(sa.select(sa.func.count()).select_from(schema.runs)).scalar()==0
        assert connection.execute(sa.select(sa.func.count()).select_from(schema.articles)).scalar()==0
    assert apply(engine,legacy)['result']=='imported'


def test_source_mutation_before_commit_aborts_whole_snapshot(engine,legacy):
    plan=plan_import(legacy)
    def changed(connection):(legacy/'data/reports/20261101_report.md').write_text('Changed')
    with pytest.raises(StorageError,match='source_changed'):import_snapshot(engine,legacy,plan,expected=plan['id'],quiesced=True,after_insert=changed)
    with engine.connect() as connection:assert connection.execute(sa.select(sa.func.count()).select_from(schema.runs)).scalar()==0

@pytest.mark.parametrize('mutation',['corrupt','duplicate_keys','negative_attempts','wrong_version','invalid_date_name','conflicting_article','symlink_file','symlink_folder','hardlink'])
def test_bad_input_never_destroys_existing_snapshot(engine,legacy,tmp_path,mutation):
    first=apply(engine,legacy);path=legacy/'data/articles/20261101_articles.json'
    if mutation=='corrupt':path.write_text('{broken')
    if mutation=='duplicate_keys':path.write_text('[{"title":"one","title":"two"}]')
    if mutation in ('negative_attempts','wrong_version'):
        state=legacy/'data/article_processing.json';data=json.loads(state.read_text())
        if mutation=='negative_attempts':data['records']['synthetic-hash']['attempts']=-1
        else:data['version']=2
        state.write_text(json.dumps(data))
    if mutation=='invalid_date_name':path.rename(path.with_name('20260230_articles.json'))
    if mutation=='conflicting_article':
        values=json.loads(path.read_text());values.append({**values[0],'summary':'Conflicting summary'});path.write_text(json.dumps(values))
    if mutation=='symlink_file':path.rename(tmp_path/'outside');path.symlink_to(tmp_path/'outside')
    if mutation=='symlink_folder':(legacy/'data/articles').rename(tmp_path/'outside');(legacy/'data/articles').symlink_to(tmp_path/'outside',target_is_directory=True)
    if mutation=='hardlink':os.link(path,tmp_path/'linked')
    with pytest.raises(StorageError):plan_import(legacy)
    assert read_snapshot(engine,first['snapshot'])['counts']['articles']==1

@pytest.mark.parametrize('raw,status,expected',[
    ('2026-10-25T02:30:00','ambiguous_local',None),('2026-03-29T02:30:00','ambiguous_local',None),
    ('2024-02-29T00:00:00+01:00','aware','2024-02-28T23:00:00+00:00'),
    ('2026-12-31T23:30:00-02:00','aware','2027-01-01T01:30:00+00:00'),
    ('2026-02-29T10:00:00','invalid',None),('2026-11-01T00:00:00Z','aware','2026-11-01T00:00:00+00:00'),
    ('nonsense','invalid',None),(None,'missing',None),(0,'aware','1970-01-01T00:00:00+00:00')])
def test_dates_never_invent_a_timezone(raw,status,expected):
    result=date_value(raw);assert result=={'raw':raw,'status':status,'utc':expected}


def test_duplicates_are_counted_and_preserved_in_document(engine,legacy):
    path=legacy/'data/articles/20261101_articles.json';data=json.loads(path.read_text());data.append(data[0]);path.write_text(json.dumps(data))
    plan=plan_import(legacy);assert plan['counts']['articles']==1 and plan['counts']['duplicates']==1
    result=apply(engine,legacy);assert len(read_snapshot(engine,result['snapshot'])['documents'][1]['payload'])>=1


def test_future_and_invalid_seen_dates_are_not_expired(engine,legacy):
    plan=read_snapshot(engine,apply(engine,legacy)['snapshot'])
    values={r['legacy_hash']:r for r in plan['rows']['seen_entries']}
    assert values['future']['date_raw']=='20990101'
    assert values['bad']['date_status']=='invalid_preserved'


def test_export_deterministic_and_noreplace_failure_cleans_stage(legacy,tmp_path,monkeypatch):
    plan=plan_import(legacy);one=tmp_path/'one';two=tmp_path/'two'
    export_snapshot(plan,one);export_snapshot(plan,two)
    assert {p.relative_to(one):p.read_bytes() for p in one.rglob('*') if p.is_file()}=={p.relative_to(two):p.read_bytes() for p in two.rglob('*') if p.is_file()}
    from modules.persistence import export
    monkeypatch.setattr(export.ctypes,'CDLL',lambda *a,**kw:type('Lib',(),{'renameat2':lambda *a: -1})())
    with pytest.raises(StorageError):export_snapshot(plan,tmp_path/'failure')
    assert not (tmp_path/'failure').exists() and not list(tmp_path.glob('.atlas-export-*'))


def test_projection_tampering_detected(engine,legacy):
    snapshot=apply(engine,legacy)['snapshot']
    with engine.begin() as connection:connection.execute(schema.articles.update().values(summary='Tampered'))
    with pytest.raises(StorageError,match='projection_field_mismatch'):read_snapshot(engine,snapshot)


def test_explicit_review_required_and_legacy_mode_has_no_database(engine,legacy):
    plan=plan_import(legacy)
    with pytest.raises(StorageError):import_snapshot(engine,legacy,plan,expected=plan['id'])
    with pytest.raises(StorageError):import_snapshot(engine,legacy,plan,expected='wrong',quiesced=True)
    assert select_storage('legacy',root=legacy)['id']==plan['id']
    with pytest.raises(StorageError):select_storage('postgres')
    assert runtime_mode({})=='legacy'
    with pytest.raises(StorageError,match='not_implemented'):runtime_mode({'storage':{'mode':'postgres'}})


def test_sql_constraints_and_no_create_all_in_runtime(engine,legacy):
    snapshot=apply(engine,legacy)['snapshot']
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as connection:connection.execute(schema.attempts.update().values(attempts=-1))
    assert read_snapshot(engine,snapshot)['rows']['processing_attempts'][0]['attempts']==2
    for path in list((ROOT/'modules/persistence').glob('*.py'))+list((ROOT/'migrations').rglob('*.py')):
        assert 'metadata.create_all(' not in path.read_text()


def test_offline_alembic_is_versioned_postgres_ddl_without_credentials():
    config=Config(str(ROOT/'alembic.ini'));stream=io.StringIO();config.output_buffer=stream
    command.upgrade(config,'head',sql=True);ddl=stream.getvalue()
    assert 'CREATE TABLE articles' in ddl and 'JSONB' in ddl and 'TIMESTAMP WITH TIME ZONE' in ddl
    assert 'ck_attempt_count' in ddl and 'atlas_meta.alembic_version' in ddl
    assert 'password' not in ddl.lower()


def test_secret_engine_url_is_hidden_and_pool_bounded(tmp_path,monkeypatch):
    from deploy import runtime
    from modules.persistence import database
    secret=tmp_path/'postgres_app_password';secret.write_text('synthetic-private-db-password');secret.chmod(0o400)
    monkeypatch.setattr(runtime,'SECRETS',tmp_path)
    engine=database.engine_from_env()
    try:
        assert 'synthetic-private' not in repr(engine.url)
        assert engine.hide_parameters is True and engine.pool.size()==2 and engine.pool._max_overflow==0
    finally:engine.dispose()


def test_partial_bootstrap_is_refused_before_postgres_can_restart(tmp_path):
    secret_root=tmp_path/'secrets';secret_root.mkdir()
    for role in ['admin','app','migrator']:
        p=secret_root/('postgres_'+role+'_password');p.write_text('a'*64+'\n');p.chmod(0o400)
    data=tmp_path/'pgdata';data.mkdir();(data/'PG_VERSION').write_text('18\n')
    process=_REAL_POPEN(['sh',str(ROOT/'deploy/postgres-entrypoint.sh'),'postgres'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={**os.environ,'PGDATA':str(data),'ATLAS_DB_SECRETS':str(secret_root)})
    out,err=process.communicate(timeout=5)
    assert process.returncode==78 and b'bootstrap completion' in err
    assert b'a'*64 not in out+err
