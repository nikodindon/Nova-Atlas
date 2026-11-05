"""CI-only PostgreSQL integration: migration, snapshots, concurrency, dump/restore.
No business service or external source/provider is started.
"""
import json
import hashlib
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import uuid
from deploy import smoke
from deploy.init import initialize
from deploy.database import initialize as initialize_database


def fixture(root):
    for folder in ['config','data/articles','data/posts','data/reports','data/editions']:(root/folder).mkdir(parents=True,exist_ok=True)
    legacy_hash=hashlib.md5(b'https://example.invalid/article').hexdigest()
    article={'hash':legacy_hash,'link':'https://example.invalid/article','title':'Été 🌍','title_original':'夏','summary':'Résumé synthétique','timestamp':'2026-10-25T02:30:00','pub_date':'2024-02-29T12:00:00+01:00','extra':{'preserved':True}}
    (root/'config/feeds.yaml').write_text('tech: [https://example.invalid/feed]\n')
    (root/'data/articles/20261025_articles.json').write_text(json.dumps([article],ensure_ascii=False))
    (root/'data/seen_hashes.json').write_text(json.dumps({legacy_hash:'20261025','future':'20990101'}))
    (root/'data/article_processing.json').write_text(json.dumps({'version':1,'records':{legacy_hash:{'status':'processing','attempts':2,'item':{'link':article['link'],'title':article['title'],'pub_date':''},'category':'tech','updated_at':1792886500,'next_attempt_at':1792886500,'prepared':article,'target_day':'20261025','discovered_at':1792886400}}}))
    (root/'data/reports/20261025_report.md').write_text('# Rapport\nÉté\n')
    (root/'data/editions/20261025_matin.md').write_text('# Matin\nTexte\n')
    (root/'data/posts/20261025_1200_posts.json').write_text('[{"post_x":"Été","validated":false,"unknown":3}]')
    (root/'data/posts/20261025_1200_posts.txt').write_text('Posts lisibles\n')


def main():
    if os.environ.get('GITHUB_ACTIONS')!='true':raise SystemExit('CI-only smoke')
    with tempfile.TemporaryDirectory(prefix='atlas-database-ci-') as temporary:
        state=Path(temporary)/'state';initialize(state,'synthetic-admin',secrets.token_urlsafe(32));initialize_database(state)
        fixture(state/'migration_input')
        smoke._REDACTIONS[:]=[p.read_text().strip() for p in (state/'secrets').iterdir() if p.stat().st_size]
        command=['docker','compose','-p','atlas-database-ci-'+uuid.uuid4().hex[:8],'-f',str(smoke.ROOT/'docker-compose.yml'),'-f',str(smoke.ROOT/'docker-compose.database.yml'),'--env-file',str(state/'compose.env'),'--profile','database','--profile','database-tools']
        def tool(*args,check=True):return smoke.run(command+['run','--rm','--no-deps','dbtools',*args],check=check)
        def python(code,check=True):return smoke.run(command+['run','--rm','--no-deps','--entrypoint','python','dbtools','-c',code],check=check)
        try:
            print('Database smoke: build, isolated PostgreSQL bootstrap',flush=True)
            smoke.run(command+['config','--quiet']);smoke.run(command+['build','dbtools'])
            smoke.run(command+['up','-d','--wait','--wait-timeout','120','postgres'])
            # Startup creates credentials/schemas only, never operational/Alembic tables.
            python("from modules.persistence.database import engine_from_env;import sqlalchemy as s;e=engine_from_env();c=e.connect();assert c.execute(s.text(\"select count(*) from information_schema.tables where table_schema='atlas'\")).scalar()==0")
            tool('migrate','head');tool('health')
            # Schema drift against metadata must be empty (ignore Alembic's private schema).
            python("""from modules.persistence.database import engine_from_env
from modules.persistence.schema import metadata
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
with engine_from_env('migrator').connect() as c:
 assert compare_metadata(MigrationContext.configure(c),metadata)==[]
""")
            raw=tool('import','--root','/imports').stdout
            data=json.loads(next(line for line in raw.splitlines() if line.startswith('{')));snapshot=data['snapshot']
            tool('import','--root','/imports','--apply','--expect-snapshot',snapshot,'--quiesced')
            tool('import','--root','/imports','--apply','--expect-snapshot',snapshot,'--quiesced')
            tool('verify','--snapshot',snapshot)
            print('Database smoke: SQL rights, rollback, checkpoint and interrupted import',flush=True)
            python("""import sqlalchemy as sa
from modules.persistence.database import engine_from_env,transaction
from modules.persistence.repository import read_snapshot
from modules.persistence import schema,StorageError
from modules.persistence.legacy import plan_import
app=engine_from_env()
with app.connect() as c:
 assert not c.execute(sa.text('select rolsuper from pg_roles where rolname=current_user')).scalar()
 assert not c.execute(sa.text(\"select has_schema_privilege(current_user,'atlas','CREATE')\")).scalar()
 assert not c.execute(sa.text(\"select has_table_privilege(current_user,'atlas.articles','DELETE')\")).scalar()
 assert not c.execute(sa.text(\"select has_schema_privilege(current_user,'atlas_meta','USAGE')\")).scalar()
try:
 with transaction(app) as c:c.execute(sa.text('CREATE TABLE atlas.forbidden(id int)'))
except StorageError:pass
else:raise AssertionError('app role acquired DDL rights')
snapshot=plan_import('/imports')['id'];plan=read_snapshot(app,snapshot)
assert plan['rows']['processing_attempts'][0]['checkpoint']['summary']=='Résumé synthétique'
assert plan['rows']['processing_attempts'][0]['attempts']==2
migrator=engine_from_env('migrator')
try:
 with transaction(migrator) as c:
  c.execute(schema.attempts.update().values(attempts=-1))
except StorageError:pass
else:raise AssertionError('check constraint bypassed')
assert read_snapshot(app,snapshot)['rows']['processing_attempts'][0]['attempts']==2
""")
            # A changed, explicitly reviewed synthetic snapshot, two concurrent importers.
            (state/'migration_input/data/reports/20261025_report.md').write_text('# Nouveau rapport\n')
            raw=tool('import','--root','/imports').stdout;new_snapshot=json.loads(next(l for l in raw.splitlines() if l.startswith('{')))['snapshot']
            crash_code="""import os,sqlalchemy as sa
from modules.persistence.database import engine_from_env
from modules.persistence.repository import import_snapshot
from modules.persistence.legacy import plan_import
plan=plan_import('/imports')
import_snapshot(engine_from_env(),'/imports',plan,expected=plan['id'],quiesced=True,after_insert=lambda c:os._exit(17))
"""
            assert python(crash_code,check=False).returncode==17
            args=command+['run','--rm','--no-deps','dbtools','import','--root','/imports','--apply','--expect-snapshot',new_snapshot,'--quiesced']
            first=subprocess.Popen(args,cwd=smoke.ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            second=subprocess.Popen(args,cwd=smoke.ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            for process in [first,second]:
                output,_=process.communicate(timeout=120)
                if process.returncode:smoke.diagnostic(output)
                assert process.returncode==0
            tool('verify','--snapshot',new_snapshot)
            # Export into a fresh directory and semantically re-plan it via the tools mount.
            tool('export','--snapshot',snapshot,'--destination','/exports/roundtrip')
            python("""from modules.persistence.legacy import plan_import
from modules.persistence.repository import read_snapshot
from modules.persistence.database import engine_from_env
import json
import hashlib
report=json.load(open('/exports/roundtrip/SNAPSHOT_REPORT.json'))
assert plan_import('/exports/roundtrip')['counts']==read_snapshot(engine_from_env(),report['source_snapshot'])['counts']
""")
            print('Database smoke: backup, restart, disconnect/reconnect and restore',flush=True)
            backup=state/'database_exports/backup.dump'
            # Binary dump goes directly to a private file, never logs or a command argument.
            with backup.open('wb') as out:
                os.chmod(backup,0o600)
                subprocess.run(command+['exec','-T','postgres','pg_dump','-U','atlas_admin','-d','nova_atlas','-Fc'],cwd=smoke.ROOT,stdout=out,check=True,timeout=90)
            smoke.run(command+['stop','postgres'])
            assert tool('health',check=False).returncode!=0
            smoke.run(command+['up','-d','--wait','--wait-timeout','120','postgres']);tool('verify','--snapshot',snapshot)
            smoke.run(command+['exec','-T','postgres','createdb','-U','atlas_admin','nova_restore'])
            with backup.open('rb') as source:
                result=subprocess.run(command+['exec','-T','postgres','pg_restore','-U','atlas_admin','--exit-on-error','--dbname','nova_restore'],cwd=smoke.ROOT,stdin=source,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
            if result.returncode:smoke.diagnostic(result.stdout.decode())
            assert result.returncode==0
            restored=smoke.run(command+['run','--rm','--no-deps','-e','ATLAS_DB_NAME=nova_restore','dbtools','verify','--snapshot',snapshot],check=False)
            if restored.returncode:smoke.diagnostic(restored.stdout)
            assert restored.returncode==0
            # Explicit downgrade only on disposable restored DB, then upgrade again.
            smoke.run(command+['run','--rm','--no-deps','-e','ATLAS_DB_NAME=nova_restore','dbtools','migrate','base','--allow-drop'])
            smoke.run(command+['run','--rm','--no-deps','-e','ATLAS_DB_NAME=nova_restore','dbtools','migrate','head'])
            logs=smoke.run(command+['logs','--no-color']).stdout
            for value in smoke._REDACTIONS:assert value not in logs
            print('Database smoke PASS: Alembic/schema, private roles, idempotent/concurrent imports, crash rollback, checkpoint, semantic export, volume restart, disconnect, dump/restore and downgrade/upgrade. No business service started.',flush=True)
        except BaseException:
            smoke.diagnostic(smoke.run(command+['logs','--no-color','--tail','100'],check=False).stdout);raise
        finally:smoke.run(command+['down','--remove-orphans'],check=False)

if __name__=='__main__':main()
