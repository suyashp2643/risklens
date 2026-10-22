"""Immutable captured inputs and versioned report records.

Revision ID: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('dataset_snapshots',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('parent_id', sa.Text(), sa.ForeignKey('dataset_snapshots.id')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('record_hash', sa.Text(), nullable=False),
        sa.CheckConstraint("record_hash = encode(sha256(convert_to(body, 'UTF8')), 'hex')", name='snapshot_record_hash'),
        sa.CheckConstraint("(body::jsonb->>'format') IS NOT DISTINCT FROM 'risklens-canonical-json-v1'", name='snapshot_format'),
        sa.CheckConstraint("(body::jsonb->>'id') IS NOT NULL AND body::jsonb->>'id' = id", name='snapshot_id'),
        sa.CheckConstraint("(body::jsonb->>'parent_id') IS NOT DISTINCT FROM parent_id", name='snapshot_parent'))
    op.create_table('saved_reports',
        sa.Column('id', sa.Text(), primary_key=True),
        sa.Column('snapshot_id', sa.Text(), sa.ForeignKey('dataset_snapshots.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('record_hash', sa.Text(), nullable=False),
        sa.CheckConstraint("record_hash = encode(sha256(convert_to(body, 'UTF8')), 'hex')", name='report_record_hash'),
        sa.CheckConstraint("(body::jsonb->>'format') IS NOT DISTINCT FROM 'risklens-canonical-json-v1'", name='report_format'),
        sa.CheckConstraint("(body::jsonb->>'id') IS NOT NULL AND body::jsonb->>'id' = id", name='report_id'),
        sa.CheckConstraint("(body::jsonb->>'snapshot_id') IS NOT NULL AND body::jsonb->>'snapshot_id' = snapshot_id", name='report_snapshot'))
    op.execute("""CREATE FUNCTION risklens_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'RiskLens saved records are immutable; create a new version';
        END $$""")
    op.execute("""CREATE FUNCTION risklens_check_insert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE b jsonb; parent jsonb;
        BEGIN
          b := NEW.body::jsonb;
          IF (b->>'created_at') IS NULL OR (b->>'created_at')::timestamptz <> NEW.created_at THEN
            RAISE EXCEPTION 'Creation timestamp mismatch';
          END IF;
          IF TG_TABLE_NAME = 'dataset_snapshots' THEN
            IF b->>'input_hash' IS NULL OR b->'inputs' IS NULL THEN
              RAISE EXCEPTION 'Missing snapshot inputs';
            END IF;
            IF NEW.parent_id IS NULL THEN
              IF (b->>'version')::int IS DISTINCT FROM 1 OR b->'approval' IS DISTINCT FROM 'null'::jsonb THEN
                RAISE EXCEPTION 'Invalid initial snapshot';
              END IF;
            ELSE
              SELECT body::jsonb INTO parent FROM dataset_snapshots WHERE id = NEW.parent_id;
              IF parent IS NULL OR (b->>'version')::int IS DISTINCT FROM (parent->>'version')::int + 1 THEN
                RAISE EXCEPTION 'Invalid correction lineage';
              END IF;
              IF (b->>'input_hash') IS NOT DISTINCT FROM (parent->>'input_hash') THEN
                RAISE EXCEPTION 'Correction must change inputs';
              END IF;
              IF b->'approval'->>'decision' IS DISTINCT FROM 'approved'
                OR COALESCE(length(trim(b->'approval'->>'record_id')),0)=0
                OR COALESCE(length(trim(b->'approval'->>'approved_by')),0)=0
                OR COALESCE(length(trim(b->'approval'->>'reason')),0)=0
                OR COALESCE(length(trim(b->'approval'->>'evidence')),0)=0
                OR b->'approval'->>'approved_at' IS NULL
                OR NOT (b->'approval'->>'approved_at' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$')
                OR (b->'approval'->>'approved_at')::timestamptz > NEW.created_at THEN
                RAISE EXCEPTION 'Explicit correction approval is required';
              END IF;
            END IF;
          ELSE
            SELECT body::jsonb INTO parent FROM dataset_snapshots WHERE id=NEW.snapshot_id;
            IF parent IS NULL OR b->>'input_hash' IS DISTINCT FROM parent->>'input_hash'
              OR b->>'engine_version' IS NULL OR b->'configuration' IS NULL OR b->'result' IS NULL THEN
              RAISE EXCEPTION 'Incomplete report or snapshot mismatch';
            END IF;
            IF b->>'kind' IS DISTINCT FROM (CASE WHEN parent->>'parent_id' IS NULL THEN 'original_report' ELSE 'corrected_report' END) THEN
              RAISE EXCEPTION 'Report kind mismatch';
            END IF;
          END IF;
          RETURN NEW;
        END $$""")
    op.execute("CREATE UNIQUE INDEX correction_approval_record_unique ON dataset_snapshots ((body::jsonb->'approval'->>'record_id')) WHERE parent_id IS NOT NULL")
    for table in ('dataset_snapshots', 'saved_reports'):
        op.execute(f'CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION risklens_reject_mutation()')
        op.execute(f'CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION risklens_reject_mutation()')
        op.execute(f'CREATE TRIGGER validate_insert BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION risklens_check_insert()')
        for trigger in ('immutable_rows','immutable_truncate','validate_insert'):
            op.execute(f'ALTER TABLE {table} ENABLE ALWAYS TRIGGER {trigger}')


def downgrade():
    # Administrative downgrade deliberately removes persisted data; never used by app.
    op.drop_table('saved_reports')
    op.drop_table('dataset_snapshots')
    op.execute('DROP FUNCTION risklens_check_insert()')
    op.execute('DROP FUNCTION risklens_reject_mutation()')
