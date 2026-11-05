"""Snapshot projections, not a second live source of truth. Revision 0001 mirrors this."""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

metadata=sa.MetaData()
JSON=sa.JSON().with_variant(JSONB(),'postgresql')

def columns():
    return [sa.Column('snapshot_id',sa.String(64),sa.ForeignKey('import_runs.id',ondelete='RESTRICT'),primary_key=True),
            sa.Column('id',sa.String(64),primary_key=True),
            sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
            sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
            sa.Column('payload',JSON,nullable=False)]

runs=sa.Table('import_runs',metadata,
    sa.Column('id',sa.String(64),primary_key=True),sa.Column('source_version',sa.Integer,nullable=False),
    sa.Column('manifest',JSON,nullable=False),sa.Column('counts',JSON,nullable=False),
    sa.Column('result',sa.String(20),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
    sa.CheckConstraint("result = 'succeeded'",name='ck_import_succeeded'),sa.CheckConstraint('source_version = 1',name='ck_import_version'))
documents=sa.Table('legacy_documents',metadata,
    sa.Column('snapshot_id',sa.String(64),sa.ForeignKey('import_runs.id',ondelete='RESTRICT'),primary_key=True),
    sa.Column('path',sa.String(512),primary_key=True),sa.Column('kind',sa.String(32),nullable=False),
    sa.Column('raw_sha256',sa.String(64),nullable=False),sa.Column('semantic_sha256',sa.String(64),nullable=False),
    sa.Column('payload',JSON,nullable=False),sa.Column('source_text',sa.Text,nullable=False))
sources=sa.Table('sources',metadata,*columns(),sa.Column('feed_url',sa.Text,nullable=False),
    sa.Column('canonical_url',sa.Text),sa.Column('category',sa.String(200),nullable=False),sa.Column('name',sa.Text),
    sa.Column('state',sa.String(32),nullable=False),sa.Column('imported_at',sa.DateTime(timezone=True),nullable=False))
articles=sa.Table('articles',metadata,*columns(),sa.Column('legacy_hash',sa.String(512),nullable=False),
    sa.Column('editorial_day',sa.String(8),nullable=False),sa.Column('original_url',sa.Text,nullable=False),
    sa.Column('title_original',sa.Text),sa.Column('title',sa.Text,nullable=False),sa.Column('source',sa.Text),
    sa.Column('category',sa.Text),sa.Column('summary',sa.Text),sa.Column('published_raw',sa.Text),
    sa.Column('dates',JSON,nullable=False),sa.Column('published_at',sa.DateTime(timezone=True)),
    sa.Column('first_seen_at',sa.DateTime(timezone=True)),sa.Column('fetched_at',sa.DateTime(timezone=True)),
    sa.Column('processed_at',sa.DateTime(timezone=True)),sa.Column('publication_state',sa.String(24),nullable=False),
    sa.UniqueConstraint('snapshot_id','editorial_day','legacy_hash',name='uq_article_day_hash'),
    sa.CheckConstraint("publication_state IN ('published','incomplete')",name='ck_article_state'))
attempts=sa.Table('processing_attempts',metadata,*columns(),sa.Column('article_id',sa.String(64)),
    sa.Column('legacy_hash',sa.String(512),nullable=False),sa.Column('state',sa.String(24),nullable=False),
    sa.Column('attempts',sa.Integer,nullable=False),sa.Column('reason',sa.Text),
    sa.Column('next_attempt_at',sa.DateTime(timezone=True)),sa.Column('checkpoint',JSON),sa.Column('source_version',sa.Integer,nullable=False),
    sa.ForeignKeyConstraint(['snapshot_id','article_id'],['articles.snapshot_id','articles.id'],ondelete='RESTRICT'),
    sa.UniqueConstraint('snapshot_id','legacy_hash',name='uq_attempt_hash'),
    sa.CheckConstraint('attempts >= 0',name='ck_attempt_count'),
    sa.CheckConstraint("state IN ('discovered','processing','retryable','succeeded','failed','ignored')",name='ck_attempt_state'))
outputs=sa.Table('editorial_outputs',metadata,*columns(),sa.Column('kind',sa.String(16),nullable=False),
    sa.Column('editorial_day',sa.String(8),nullable=False),sa.Column('content',JSON,nullable=False),
    sa.Column('status',sa.String(24),nullable=False),sa.Column('provenance',sa.String(512),nullable=False),
    sa.CheckConstraint("kind IN ('report','edition','post')",name='ck_output_kind'))
seen=sa.Table('seen_entries',metadata,*columns(),sa.Column('legacy_hash',sa.String(512),nullable=False),
    sa.Column('date_raw',JSON,nullable=False),sa.Column('date_status',sa.String(24),nullable=False),
    sa.UniqueConstraint('snapshot_id','legacy_hash',name='uq_seen_hash'))
PROJECTIONS={'sources':sources,'articles':articles,'processing_attempts':attempts,'editorial_outputs':outputs,'seen_entries':seen}
