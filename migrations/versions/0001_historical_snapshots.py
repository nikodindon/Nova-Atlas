"""Immutable historical snapshots and typed projections. No startup migration."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='0001_snapshots'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
# Versioned DDL; never create_all at application startup.
    op.create_table('import_runs',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('source_version', sa.Integer(), nullable=False),
    sa.Column('manifest', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('counts', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('result', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("result = 'succeeded'", name='ck_import_succeeded'),
    sa.CheckConstraint('source_version = 1', name='ck_import_version'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('articles',
    sa.Column('snapshot_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('legacy_hash', sa.String(length=512), nullable=False),
    sa.Column('editorial_day', sa.String(length=8), nullable=False),
    sa.Column('original_url', sa.Text(), nullable=False),
    sa.Column('title_original', sa.Text(), nullable=True),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=True),
    sa.Column('category', sa.Text(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('published_raw', sa.Text(), nullable=True),
    sa.Column('dates', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('publication_state', sa.String(length=24), nullable=False),
    sa.CheckConstraint("publication_state IN ('published','incomplete')", name='ck_article_state'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['import_runs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('snapshot_id', 'id'),
    sa.UniqueConstraint('snapshot_id', 'editorial_day', 'legacy_hash', name='uq_article_day_hash')
    )
    op.create_table('editorial_outputs',
    sa.Column('snapshot_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('editorial_day', sa.String(length=8), nullable=False),
    sa.Column('content', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('provenance', sa.String(length=512), nullable=False),
    sa.CheckConstraint("kind IN ('report','edition','post')", name='ck_output_kind'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['import_runs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('snapshot_id', 'id')
    )
    op.create_table('legacy_documents',
    sa.Column('snapshot_id', sa.String(length=64), nullable=False),
    sa.Column('path', sa.String(length=512), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('raw_sha256', sa.String(length=64), nullable=False),
    sa.Column('semantic_sha256', sa.String(length=64), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('source_text', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['snapshot_id'], ['import_runs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('snapshot_id', 'path')
    )
    op.create_table('seen_entries',
    sa.Column('snapshot_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('legacy_hash', sa.String(length=512), nullable=False),
    sa.Column('date_raw', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('date_status', sa.String(length=24), nullable=False),
    sa.ForeignKeyConstraint(['snapshot_id'], ['import_runs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('snapshot_id', 'id'),
    sa.UniqueConstraint('snapshot_id', 'legacy_hash', name='uq_seen_hash')
    )
    op.create_table('sources',
    sa.Column('snapshot_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('feed_url', sa.Text(), nullable=False),
    sa.Column('canonical_url', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=200), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['snapshot_id'], ['import_runs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('snapshot_id', 'id')
    )
    op.create_table('processing_attempts',
    sa.Column('snapshot_id', sa.String(length=64), nullable=False),
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('article_id', sa.String(length=64), nullable=True),
    sa.Column('legacy_hash', sa.String(length=512), nullable=False),
    sa.Column('state', sa.String(length=24), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('checkpoint', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('source_version', sa.Integer(), nullable=False),
    sa.CheckConstraint("state IN ('discovered','processing','retryable','succeeded','failed','ignored')", name='ck_attempt_state'),
    sa.CheckConstraint('attempts >= 0', name='ck_attempt_count'),
    sa.ForeignKeyConstraint(['snapshot_id', 'article_id'], ['articles.snapshot_id', 'articles.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['snapshot_id'], ['import_runs.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('snapshot_id', 'id'),
    sa.UniqueConstraint('snapshot_id', 'legacy_hash', name='uq_attempt_hash')
    )
    

def downgrade():
# Versioned DDL; never create_all at application startup.
    op.drop_table('processing_attempts')
    op.drop_table('sources')
    op.drop_table('seen_entries')
    op.drop_table('legacy_documents')
    op.drop_table('editorial_outputs')
    op.drop_table('articles')
    op.drop_table('import_runs')
    
