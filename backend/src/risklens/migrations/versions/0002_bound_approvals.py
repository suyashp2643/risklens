"""Bind new approvals; preserve and explicitly classify pre-existing history."""
from alembic import op
import sqlalchemy as sa
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('legacy_correction_approvals',
        sa.Column('snapshot_id',sa.Text(),sa.ForeignKey('dataset_snapshots.id'),primary_key=True),
        sa.Column('record_hash',sa.Text(),nullable=False))
    # Prevent concurrent inserts during classification/guard installation.
    op.execute('LOCK TABLE dataset_snapshots IN SHARE ROW EXCLUSIVE MODE')
    op.execute("""INSERT INTO legacy_correction_approvals(snapshot_id,record_hash)
        SELECT id,record_hash FROM dataset_snapshots WHERE parent_id IS NOT NULL
        AND NOT ((body::jsonb->'approval') ?& ARRAY['parent_snapshot_id','parent_input_hash','proposed_input_hash'])""")
    for event in ('INSERT','UPDATE','DELETE'):
        op.execute(f'CREATE TRIGGER legacy_no_{event.lower()} BEFORE {event} ON legacy_correction_approvals FOR EACH ROW EXECUTE FUNCTION risklens_reject_mutation()')
        op.execute(f'ALTER TABLE legacy_correction_approvals ENABLE ALWAYS TRIGGER legacy_no_{event.lower()}')
    op.execute('CREATE TRIGGER legacy_no_truncate BEFORE TRUNCATE ON legacy_correction_approvals FOR EACH STATEMENT EXECUTE FUNCTION risklens_reject_mutation()')
    op.execute('ALTER TABLE legacy_correction_approvals ENABLE ALWAYS TRIGGER legacy_no_truncate')
    op.execute("""CREATE FUNCTION risklens_check_bound_approval() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE a jsonb; parent_hash text;
        BEGIN
          IF NEW.parent_id IS NULL THEN RETURN NEW; END IF;
          a := NEW.body::jsonb->'approval';
          SELECT body::jsonb->>'input_hash' INTO parent_hash FROM dataset_snapshots WHERE id=NEW.parent_id;
          IF a->>'parent_snapshot_id' IS DISTINCT FROM NEW.parent_id
            OR a->>'parent_input_hash' IS DISTINCT FROM parent_hash
            OR a->>'proposed_input_hash' IS DISTINCT FROM NEW.body::jsonb->>'input_hash'
            OR a->>'parent_input_hash' IS NULL OR a->>'proposed_input_hash' IS NULL
            OR NOT (a->>'parent_input_hash' ~ '^[0-9a-f]{64}$')
            OR NOT (a->>'proposed_input_hash' ~ '^[0-9a-f]{64}$') THEN
            RAISE EXCEPTION 'Correction approval binding mismatch';
          END IF;
          RETURN NEW;
        END $$""")
    op.execute('CREATE TRIGGER bound_approval BEFORE INSERT ON dataset_snapshots FOR EACH ROW EXECUTE FUNCTION risklens_check_bound_approval()')
    op.execute('ALTER TABLE dataset_snapshots ENABLE ALWAYS TRIGGER bound_approval')
    # Existing unique approval-id index from 0001 remains authoritative under concurrency.


def downgrade():
    op.execute('DROP TRIGGER bound_approval ON dataset_snapshots')
    op.execute('DROP FUNCTION risklens_check_bound_approval()')
    op.drop_table('legacy_correction_approvals')
