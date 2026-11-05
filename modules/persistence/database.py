"""Explicit engines, no password URL diagnostics, bounded PostgreSQL transactions."""
from contextlib import contextmanager
import logging
import os
import re
import sqlalchemy as sa
from sqlalchemy.engine import URL
from modules.persistence import StorageError


def engine_from_env(role='app'):
    from deploy.runtime import read_secret
    if role not in ('app','migrator','admin'):raise StorageError('invalid_database_role')
    host=os.environ.get('ATLAS_DB_HOST','postgres');name=os.environ.get('ATLAS_DB_NAME','nova_atlas')
    if not re.fullmatch('[A-Za-z0-9.-]{1,253}',host) or not re.fullmatch('[a-z][a-z0-9_]{0,62}',name):raise StorageError('invalid_database_configuration')
    try:
        port=int(os.environ.get('ATLAS_DB_PORT','5432'))
        if not 1<=port<=65535:raise ValueError()
        password=read_secret('postgres_'+role+'_password')
        user={'app':'atlas_app','migrator':'atlas_migrator','admin':'atlas_admin'}[role]
        url=URL.create('postgresql+psycopg',username=user,password=password,host=host,port=port,database=name)
        logging.getLogger('sqlalchemy.engine').setLevel(logging.CRITICAL)
        return sa.create_engine(url,hide_parameters=True,pool_size=2,max_overflow=0,pool_timeout=5,pool_recycle=300,pool_pre_ping=True,
            connect_args={'connect_timeout':5,'application_name':'nova_atlas_snapshot','options':'-c statement_timeout=15000 -c lock_timeout=5000 -c timezone=UTC -c search_path=atlas,pg_catalog'})
    except Exception:raise StorageError('database_configuration_unavailable') from None


@contextmanager
def transaction(engine):
    try:
        with engine.begin() as connection:yield connection
    except StorageError:raise
    except Exception:raise StorageError('database_transaction_failed') from None


def available(engine):
    with transaction(engine) as connection:return connection.execute(sa.text('SELECT 1')).scalar_one()==1
