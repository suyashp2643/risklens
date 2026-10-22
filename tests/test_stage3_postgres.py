"""Actual PostgreSQL integration tests; never substitute SQLite/mocked SQL.

Set RISKLENS_TEST_DATABASE_URL to a local test database. Each test migrates
an isolated schema, exercises real triggers and drops only that test schema.
A missing variable skips; a configured but unreachable database fails.
"""
import json
import os
import shutil
import uuid
from pathlib import Path
from datetime import datetime, timezone
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text, select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from risklens.snapshots import InputSnapshot, canonical, sha256
from risklens.storage import (database_engine, SnapshotRow, ReportRow, save_snapshot,
    save_report, replay_report, read_snapshot, list_reports)

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'sample-data/demo'


@pytest.fixture
def pg(monkeypatch):
    url=os.environ.get('RISKLENS_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Actual PostgreSQL unavailable: RISKLENS_TEST_DATABASE_URL not set')
    engine=database_engine(url)
    schema='risklens_test_'+uuid.uuid4().hex
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA {schema}'))
    with engine.connect() as c:
        c.execute(text(f'SET search_path TO {schema}, public'))
        c.commit()
        cfg=Config(str(ROOT/'alembic.ini'))
        cfg.attributes['connection']=c
        command.upgrade(cfg,'head')
        try:
            yield c,cfg
        finally:
            c.rollback()
            c.execute(text('SET search_path TO public'))
            c.execute(text(f'DROP SCHEMA {schema} CASCADE'))
            c.commit()
    engine.dispose()


def make_saved(connection,fixture='clean'):
    with Session(connection) as s,s.begin():
        snapshot=save_snapshot(s,InputSnapshot.capture(DATA/fixture))
        report=save_report(s,snapshot['id'])
    return snapshot,report


def explicit_approval(parent=None, proposed=DATA/'clean'):
    return {'parent_snapshot_id':parent['id'] if parent else 'test-parent',
        'parent_input_hash':parent['input_hash'] if parent else '0'*64,
        'proposed_input_hash':InputSnapshot.capture(proposed).content_hash,
        'record_id':str(uuid.uuid4()),'approved_by':'simulated integration-test reviewer',
        'approved_at':datetime.now(timezone.utc).isoformat(),'decision':'approved',
        'reason':'Restore exact synthetic clean prices after deliberate split-fixture corruption',
        'evidence':'Compare sample-data/demo/split/fixture.json and deterministic clean prices; test-only approval'}


def test_migration_upgrade_downgrade_upgrade(pg):
    c,cfg=pg
    assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0002'
    c.commit()
    command.downgrade(cfg,'base')
    assert c.execute(text("SELECT to_regclass('dataset_snapshots')")).scalar() is None
    c.commit()
    command.upgrade(cfg,'head')
    snapshot,report=make_saved(c)
    with Session(c) as s:
        assert replay_report(s,report['id'])['numerical_agreement']
    c.rollback()


@pytest.mark.parametrize('table',['dataset_snapshots','saved_reports'])
@pytest.mark.parametrize('operation',['update','delete','truncate'])
def test_database_level_immutability(pg,table,operation):
    c,_=pg
    snapshot,report=make_saved(c)
    query={'update':f"UPDATE {table} SET body=body",'delete':f'DELETE FROM {table}',
        'truncate':'TRUNCATE saved_reports, legacy_correction_approvals, dataset_snapshots'}[operation]
    with pytest.raises(DBAPIError,match='immutable'):
        with c.begin():
            c.execute(text(query))
    with Session(c) as s:
        assert replay_report(s,report['id'])['numerical_agreement']
    c.rollback()


def test_replay_without_original_source_and_correction_lineage(pg,tmp_path):
    c,_=pg
    source=tmp_path/'source';shutil.copytree(DATA/'split',source)
    with Session(c) as s,s.begin():
        parent=save_snapshot(s,InputSnapshot.capture(source))
        original=save_report(s,parent['id'])
    shutil.rmtree(source)
    with Session(c) as s,s.begin():
        replay=replay_report(s,original['id'])
        assert replay['kind']=='exact_replay'
        assert replay['input_byte_integrity'] and replay['result_bytes_identical']
        assert not replay['source_directory_read']
        child=save_snapshot(s,InputSnapshot.capture(DATA/'clean'),parent['id'],explicit_approval(parent))
        corrected=save_report(s,child['id'])
        assert child['version']==2 and child['parent_id']==parent['id']
        assert corrected['kind']=='corrected_report'
        assert corrected['corrections'][0]['approval']==child['approval']
        assert corrected['result']['metrics'] != original['result']['metrics']
        assert replay_report(s,corrected['id'])['numerical_agreement']
        assert replay_report(s,original['id'])['numerical_agreement']
        assert len(list_reports(s))==2


def test_correction_requires_approval_and_changed_inputs(pg):
    c,_=pg;parent,_=make_saved(c)
    with Session(c) as s,s.begin():
        with pytest.raises(ValueError,match='approval'):
            save_snapshot(s,InputSnapshot.capture(DATA/'split'),parent['id'])
        with pytest.raises(ValueError,match='change'):
            save_snapshot(s,InputSnapshot.capture(DATA/'clean'),parent['id'],explicit_approval(parent))


def test_rollback_removes_snapshot_and_report(pg):
    c,_=pg
    with pytest.raises(RuntimeError):
        with Session(c) as s,s.begin():
            parent=save_snapshot(s,InputSnapshot.capture(DATA/'clean'))
            save_report(s,parent['id'])
            raise RuntimeError('simulate failure before commit')
    with Session(c) as s:
        assert s.scalar(select(func.count()).select_from(SnapshotRow))==0
        assert s.scalar(select(func.count()).select_from(ReportRow))==0
    c.rollback()


def test_db_rejects_bad_hash_and_forged_lineage(pg):
    c,_=pg;parent,_=make_saved(c)
    with Session(c) as s:
        row=s.get(SnapshotRow,parent['id'])
        values={k:getattr(row,k) for k in ('id','parent_id','created_at','body','record_hash')}
    c.rollback()
    ident=str(uuid.uuid4());b=json.loads(values['body']);b['id']=ident
    values.update(id=ident,body=canonical(b).decode(),record_hash='0'*64)
    with pytest.raises(DBAPIError,match='snapshot_record_hash'):
        with Session(c) as s,s.begin():
            s.add(SnapshotRow(**values));s.flush()
    b['parent_id']=parent['id'];b['version']=2
    values.update(parent_id=parent['id'],body=canonical(b).decode(),record_hash=sha256(canonical(b)))
    with pytest.raises(DBAPIError):
        with Session(c) as s,s.begin():
            s.add(SnapshotRow(**values));s.flush()


def insert_changed_report(c, original, mutate):
    b=json.loads(c.execute(text('SELECT body FROM saved_reports WHERE id=:id'),{'id':original['id']}).scalar_one())
    c.rollback()
    b['id']=str(uuid.uuid4());mutate(b)
    raw=canonical(b)
    with Session(c) as s,s.begin():
        s.add(ReportRow(id=b['id'],snapshot_id=b['snapshot_id'],created_at=datetime.fromisoformat(b['created_at']),
            body=raw.decode(),record_hash=sha256(raw)))
    return b['id']


def test_unsupported_engine_explicitly_rejected(pg):
    c,_=pg;_,r=make_saved(c)
    ident=insert_changed_report(c,r,lambda b:b.update(engine_version='unavailable-99'))
    with Session(c) as s:
        with pytest.raises(ValueError,match='exact replay unsupported'):
            replay_report(s,ident)
    c.rollback()


def test_result_hash_and_numerical_mismatch_separate(pg):
    c,_=pg;_,r=make_saved(c)
    def change(b):
        b['result']['metrics']['historical_one_day_var']+=.01
    ident=insert_changed_report(c,r,change)
    with Session(c) as s:
        with pytest.raises(ValueError,match='result hash'):
            replay_report(s,ident)
    c.rollback()
    def change_and_rehash(b):
        change(b);b['result_hash']=sha256(canonical(b['result']))
    ident=insert_changed_report(c,r,change_and_rehash)
    with Session(c) as s:
        replay=replay_report(s,ident)
        assert replay['input_byte_integrity']
        assert not replay['numerical_agreement']
        assert replay['kind']=='replay_mismatch'
    c.rollback()


def test_snapshot_inner_integrity_verification(pg):
    c,_=pg;parent,_=make_saved(c)
    b=dict(parent);b['id']=str(uuid.uuid4())
    b['inputs']=json.loads(json.dumps(b['inputs']))
    b['inputs']['files']['prices.csv']['size']+=1
    raw=canonical(b)
    with Session(c) as s,s.begin():
        s.add(SnapshotRow(id=b['id'],parent_id=None,created_at=datetime.fromisoformat(b['created_at']),
            body=raw.decode(),record_hash=sha256(raw)))
    with Session(c) as s:
        with pytest.raises(ValueError,match='content hash'):
            read_snapshot(s,b['id'])
    c.rollback()


@pytest.mark.parametrize('field',['parent_snapshot_id','parent_input_hash','proposed_input_hash'])
def test_database_rejects_approval_binding_mismatch(pg,field):
    c,_=pg;parent,_=make_saved(c,'split')
    with Session(c) as s,s.begin():
        child=save_snapshot(s,InputSnapshot.capture(DATA/'clean'),parent['id'],explicit_approval(parent))
    b=json.loads(json.dumps(child));b['id']=str(uuid.uuid4())
    b['approval']['record_id']=str(uuid.uuid4())
    b['approval'][field]='other-parent' if field=='parent_snapshot_id' else 'f'*64
    raw=canonical(b)
    with pytest.raises(DBAPIError,match='binding mismatch'):
        with Session(c) as s,s.begin():
            s.add(SnapshotRow(id=b['id'],parent_id=b['parent_id'],created_at=datetime.fromisoformat(b['created_at']),
                body=raw.decode(),record_hash=sha256(raw)));s.flush()


def test_database_rejects_reused_review_id_and_postapproval_change(pg,tmp_path):
    c,_=pg;parent,_=make_saved(c,'split')
    approval=explicit_approval(parent)
    source=tmp_path/'proposal';shutil.copytree(DATA/'clean',source)
    with (source/'portfolio.json').open('ab') as f:
        f.write(b'\n')
    with Session(c) as s,s.begin():
        with pytest.raises(ValueError,match='proposed_input_hash mismatch'):
            save_snapshot(s,InputSnapshot.capture(source),parent['id'],approval)
        child=save_snapshot(s,InputSnapshot.capture(DATA/'clean'),parent['id'],approval)
    b=json.loads(json.dumps(child));b['id']=str(uuid.uuid4());raw=canonical(b)
    with pytest.raises(DBAPIError,match='correction_approval_record_unique'):
        with Session(c) as s,s.begin():
            s.add(SnapshotRow(id=b['id'],parent_id=b['parent_id'],created_at=datetime.fromisoformat(b['created_at']),
                body=raw.decode(),record_hash=sha256(raw)));s.flush()


def test_migration_preserves_legacy_approval_history(pg):
    from risklens.storage import LegacyApprovalRow
    c,cfg=pg
    command.downgrade(cfg,'0001')
    parent,original=make_saved(c,'split')
    # Produce a valid old-format correction under the old database contract.
    with Session(c) as s,s.begin():
        child=save_snapshot(s,InputSnapshot.capture(DATA/'clean'),parent['id'],explicit_approval(parent))
        saved=save_report(s,child['id'])
    legacy=json.loads(json.dumps(child));legacy['id']=str(uuid.uuid4())
    legacy['approval']['record_id']=str(uuid.uuid4())
    for field in ('parent_snapshot_id','parent_input_hash','proposed_input_hash'):
        del legacy['approval'][field]
    raw=canonical(legacy)
    with Session(c) as s,s.begin():
        s.add(SnapshotRow(id=legacy['id'],parent_id=parent['id'],created_at=datetime.fromisoformat(legacy['created_at']),
            body=raw.decode(),record_hash=sha256(raw)))
    c.rollback()
    before=c.execute(text('SELECT id,body,record_hash FROM dataset_snapshots ORDER BY id')).all()
    reports_before=c.execute(text('SELECT id,body,record_hash FROM saved_reports ORDER BY id')).all();c.commit()
    command.upgrade(cfg,'head')
    after=c.execute(text('SELECT id,body,record_hash FROM dataset_snapshots ORDER BY id')).all();c.commit()
    assert before==after
    reports_after=c.execute(text('SELECT id,body,record_hash FROM saved_reports ORDER BY id')).all();c.commit()
    assert reports_before==reports_after
    with Session(c) as s,s.begin():
        assert s.get(LegacyApprovalRow,legacy['id']).record_hash==sha256(raw)
        assert read_snapshot(s,legacy['id'])[1]['approval']==legacy['approval']
        report=save_report(s,legacy['id'])
        assert replay_report(s,report['id'])['approval_binding_status']==['legacy_unbound']
        assert replay_report(s,saved['id'])['numerical_agreement']
    with pytest.raises(DBAPIError,match='immutable'):
        with c.begin():
            c.execute(text('INSERT INTO legacy_correction_approvals VALUES (:id,:hash)'),
                {'id':child['id'],'hash':sha256(canonical(child))})
