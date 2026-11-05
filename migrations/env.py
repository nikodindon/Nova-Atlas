from alembic import context
from modules.persistence.schema import metadata
from modules.persistence.database import engine_from_env

config=context.config

def migrate(connection):
    context.configure(connection=connection,target_metadata=metadata,compare_type=True,transaction_per_migration=True,version_table_schema="atlas_meta" if connection.dialect.name=="postgresql" else None)
    with context.begin_transaction():context.run_migrations()

if context.is_offline_mode():
    context.configure(dialect_name='postgresql',target_metadata=metadata,literal_binds=True,version_table_schema='atlas_meta')
    with context.begin_transaction():context.run_migrations()
elif config.attributes.get('connection') is not None:
    migrate(config.attributes['connection'])
else:
    engine=engine_from_env('migrator')
    try:
        with engine.connect() as connection:migrate(connection)
    finally:engine.dispose()
