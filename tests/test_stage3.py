"""Snapshot math/serialization checks need no database. PostgreSQL is separate."""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from risklens.snapshots import InputSnapshot, canonical, sha256, INPUT_FILES
from risklens.reporting import calculate_from_snapshot, calculate_report
from risklens.storage import Approval, numerical_agreement, database_engine, _verified, ReportRow

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT/'sample-data/demo/clean'


def test_capture_validate_and_calculate_same_bytes(tmp_path):
    import shutil
    source = tmp_path/'source'
    shutil.copytree(CLEAN,source)
    snapshot = InputSnapshot.capture(source)
    original = calculate_from_snapshot(snapshot)
    (source/'prices.csv').write_text('broken source after capture')
    assert calculate_from_snapshot(snapshot) == original
    assert original == calculate_report(CLEAN)
    shutil.rmtree(source)
    assert calculate_from_snapshot(snapshot) == original


def test_snapshot_immutable_mapping():
    snapshot = InputSnapshot.capture(CLEAN)
    with pytest.raises(TypeError):
        snapshot.files['prices.csv'] = b'changed'
    with pytest.raises(ValueError):
        InputSnapshot({'prices.csv':b''})


def test_canonical_hash_reference_and_key_order():
    assert canonical({'z':1, 'a':'é'}) == b'{"a":"\xc3\xa9","z":1}'
    assert sha256(b'abc') == 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
    assert sha256(canonical({'z':1,'a':2})) == sha256(canonical({'a':2,'z':1}))
    with pytest.raises(ValueError):
        canonical({'bad':float('nan')})


def test_snapshot_roundtrip_and_changed_whitespace_hash():
    snapshot = InputSnapshot.capture(CLEAN)
    restored = InputSnapshot.restore(snapshot.payload(), snapshot.content_hash)
    assert restored.files == snapshot.files
    assert calculate_from_snapshot(restored) == calculate_from_snapshot(snapshot)
    files = dict(snapshot.files)
    files['portfolio.json'] += b'\n'
    changed = InputSnapshot(files)
    assert changed.content_hash != snapshot.content_hash
    assert calculate_from_snapshot(changed) == calculate_from_snapshot(snapshot)


def test_outer_and_per_file_integrity_are_separate():
    snapshot = InputSnapshot.capture(CLEAN)
    payload = snapshot.payload()
    payload['files']['prices.csv']['size'] += 1
    with pytest.raises(ValueError,match='content hash'):
        InputSnapshot.restore(payload,snapshot.content_hash)
    # Even recomputing the outer hash cannot hide an inconsistent file manifest.
    with pytest.raises(ValueError,match='integrity'):
        InputSnapshot.restore(payload,sha256(canonical(payload)))


@pytest.mark.parametrize('change', ['missing','format','base64','sha256'])
def test_invalid_snapshot_payload(change):
    snapshot=InputSnapshot.capture(CLEAN)
    payload=snapshot.payload()
    if change=='missing':
        del payload['files']['prices.csv']
    elif change=='format':
        payload['format']='future-v99'
    elif change=='base64':
        payload['files']['prices.csv']['base64']='not base64!'
    else:
        payload['files']['prices.csv']['sha256']='0'*64
    with pytest.raises(ValueError):
        InputSnapshot.restore(payload,sha256(canonical(payload)))


def approval(parent=None, proposed=CLEAN):
    return {'parent_snapshot_id':parent['id'] if parent else 'unit-parent',
        'parent_input_hash':parent['input_hash'] if parent else '0'*64,
        'proposed_input_hash':InputSnapshot.capture(proposed).content_hash,
        'record_id':'unit-test-review', 'approved_by':'simulated test reviewer',
        'approved_at':'2026-01-01T00:00:00+00:00','decision':'approved',
        'reason':'Synthetic fixture comparison reviewed', 'evidence':'Offline fixture manifest'}


@pytest.mark.parametrize('field,value', [('decision','pending'),('reason',' '),('evidence',''),
    ('approved_by',''),('approved_at','2026-01-01T00:00:00'),
    ('approved_at','2999-01-01T00:00:00+00:00')])
def test_explicit_approval_validation(field,value):
    data=approval(); data[field]=value
    with pytest.raises(ValueError):
        Approval.model_validate(data)


def test_valid_approval_preserved():
    parsed=Approval.model_validate(approval())
    data=parsed.model_dump(mode='json')
    assert datetime.fromisoformat(data.pop('approved_at').replace('Z','+00:00')) == parsed.approved_at
    expected=approval();expected.pop('approved_at')
    assert data==expected


def test_numerical_tolerance_is_not_byte_integrity():
    a={'daily':[.01], 'mode':'synthetic'}
    b={'daily':[.01+1e-13], 'mode':'synthetic'}
    assert canonical(a)!=canonical(b)
    assert numerical_agreement(a,b)
    assert not numerical_agreement(a,{'daily':[.011], 'mode':'synthetic'})
    assert not numerical_agreement(a,{'daily':[.01], 'mode':'historical_csv'})
    assert not numerical_agreement([True],[1])
    assert not numerical_agreement([1],[1,2])


def test_saved_record_hash_and_metadata_checks():
    timestamp=datetime.now(timezone.utc)
    data={'format':'risklens-canonical-json-v1', 'id':'unit-report','created_at':timestamp.isoformat()}
    raw=canonical(data)
    row=ReportRow(id='unit-report',snapshot_id='unit-snapshot',created_at=timestamp,
        body=raw.decode(),record_hash=sha256(raw))
    assert _verified(row)==data
    row.body+=' '
    with pytest.raises(ValueError,match='hash'):
        _verified(row)
    row.record_hash=sha256(row.body.encode())
    with pytest.raises(ValueError,match='noncanonical'):
        _verified(row)


def test_postgres_only_and_missing_database_url(monkeypatch):
    monkeypatch.delenv('RISKLENS_DATABASE_URL',raising=False)
    with pytest.raises(ValueError):
        database_engine()
    with pytest.raises(ValueError):
        database_engine('sqlite://')


class MemoryRecords:
    """Unit-test record repository; provides no database/transaction guarantees."""
    def __init__(self):
        self.rows={}
    def add(self,row):
        self.rows[(type(row),row.id)]=row
    def flush(self):
        pass
    def scalar(self,statement):
        values=statement.compile().params.values()
        from risklens.storage import SnapshotRow
        for (model,ident),row in self.rows.items():
            if model is SnapshotRow:
                a=json.loads(row.body).get('approval')
                if a and a['record_id'] in values:
                    return ident
        return None
    def get(self,model,ident):
        return self.rows.get((model,ident))


def saved_records(source=CLEAN):
    from risklens.storage import save_snapshot,save_report
    records=MemoryRecords()
    snapshot=save_snapshot(records,InputSnapshot.capture(source))
    report=save_report(records,snapshot['id'])
    return records,snapshot,report


def test_replay_service_after_deleted_source(tmp_path):
    from risklens.storage import replay_report
    import shutil
    source=tmp_path/'source';shutil.copytree(CLEAN,source)
    records,snapshot,report=saved_records(source)
    shutil.rmtree(source)
    result=replay_report(records,report['id'])
    assert result['kind']=='exact_replay'
    assert result['input_byte_integrity']
    assert result['result_bytes_identical']
    assert result['numerical_agreement']
    assert result['source_directory_read'] is False


def change_record(records,report,change):
    row=records.get(ReportRow,report['id'])
    body=json.loads(row.body);change(body)
    row.body=canonical(body).decode()
    row.record_hash=sha256(row.body.encode())


def test_replay_service_unsupported_engine():
    from risklens.storage import replay_report
    records,_,report=saved_records()
    change_record(records,report,lambda b:b.update(engine_version='unavailable-version'))
    with pytest.raises(ValueError,match='exact replay unsupported'):
        replay_report(records,report['id'])


def test_replay_service_rejects_changed_configuration():
    from risklens.storage import replay_report
    records,_,report=saved_records()
    def change(b):
        b['configuration']['quantile_method']='linear'
        b['configuration_hash']=sha256(canonical(b['configuration']))
    change_record(records,report,change)
    with pytest.raises(ValueError,match='configuration unsupported'):
        replay_report(records,report['id'])


def test_replay_service_reports_numerical_mismatch():
    from risklens.storage import replay_report
    records,_,report=saved_records()
    def change(b):
        b['result']['daily'][1]['portfolio_return']+=.01
        b['result_hash']=sha256(canonical(b['result']))
    change_record(records,report,change)
    result=replay_report(records,report['id'])
    assert result['saved_record_integrity']
    assert not result['numerical_agreement']
    assert result['kind']=='replay_mismatch'


def test_correction_service_preserves_lineage_and_original():
    from risklens.storage import save_snapshot,save_report,replay_report,SnapshotRow
    records,parent,original=saved_records(ROOT/'sample-data/demo/split')
    original_row=records.get(SnapshotRow,parent['id']).body
    child=save_snapshot(records,InputSnapshot.capture(CLEAN),parent['id'],approval(parent))
    corrected=save_report(records,child['id'])
    assert child['version']==2
    assert corrected['kind']=='corrected_report'
    assert corrected['corrections'][0]['parent_id']==parent['id']
    assert corrected['corrections'][0]['approval']==child['approval']
    assert records.get(SnapshotRow,parent['id']).body==original_row
    assert corrected['result']['metrics']!=original['result']['metrics']
    assert replay_report(records,original['id'])['numerical_agreement']
    assert replay_report(records,corrected['id'])['numerical_agreement']


def test_correction_service_blocks_unapproved_and_unchanged():
    from risklens.storage import save_snapshot
    records,parent,_=saved_records()
    with pytest.raises(ValueError,match='approval'):
        save_snapshot(records,InputSnapshot.capture(ROOT/'sample-data/demo/split'),parent['id'])
    with pytest.raises(ValueError,match='change'):
        save_snapshot(records,InputSnapshot.capture(CLEAN),parent['id'],approval(parent))


@pytest.mark.parametrize('field',['parent_snapshot_id','parent_input_hash','proposed_input_hash'])
def test_bound_approval_mismatch_rejected(field):
    from risklens.storage import save_snapshot
    records,parent,_=saved_records(ROOT/'sample-data/demo/split')
    data=approval(parent)
    data[field]='wrong-parent' if field=='parent_snapshot_id' else 'f'*64
    with pytest.raises(ValueError,match=field+' mismatch'):
        save_snapshot(records,InputSnapshot.capture(CLEAN),parent['id'],data)


def test_proposed_dataset_changed_after_approval_rejected(tmp_path):
    import shutil
    from risklens.storage import save_snapshot
    records,parent,_=saved_records(ROOT/'sample-data/demo/split')
    source=tmp_path/'proposed';shutil.copytree(CLEAN,source)
    data=approval(parent,source)
    # Even a valid whitespace-only edit changes the approved byte set.
    with (source/'portfolio.json').open('ab') as f:
        f.write(b'\n')
    with pytest.raises(ValueError,match='proposed_input_hash mismatch'):
        save_snapshot(records,InputSnapshot.capture(source),parent['id'],data)


def test_reused_approval_identifier_rejected():
    from risklens.storage import save_snapshot
    records,parent,_=saved_records(ROOT/'sample-data/demo/split')
    data=approval(parent)
    child=save_snapshot(records,InputSnapshot.capture(CLEAN),parent['id'],data)
    other=approval(child,ROOT/'sample-data/demo/stale')
    with pytest.raises(ValueError,match='record_id already used'):
        save_snapshot(records,InputSnapshot.capture(ROOT/'sample-data/demo/stale'),child['id'],other)


def test_review_is_pending_and_captured_bytes_are_inspectable(tmp_path):
    from risklens.reviews import prepare_review
    from risklens.storage import save_snapshot
    records,parent,_=saved_records(ROOT/'sample-data/demo/split')
    out=tmp_path/'review'
    result=prepare_review(records,parent['id'],InputSnapshot.capture(CLEAN),out)
    data=json.loads((out/'approval.json').read_text())
    assert data['decision']=='pending'
    assert data['parent_snapshot_id']==parent['id']
    assert data['parent_input_hash']==parent['input_hash']
    assert data['proposed_input_hash']==InputSnapshot.capture(out/'proposed-inputs').content_hash
    assert result['changed_files']
    assert 'parent/prices.csv' in (out/'changes.diff').read_text()
    with pytest.raises(ValueError):
        save_snapshot(records,InputSnapshot.capture(out/'proposed-inputs'),parent['id'],data)


def test_legacy_history_must_be_registered_and_not_rewritten():
    from risklens.storage import LegacyApprovalRow,SnapshotRow,read_snapshot,save_snapshot
    records,parent,_=saved_records(ROOT/'sample-data/demo/split')
    child=save_snapshot(records,InputSnapshot.capture(CLEAN),parent['id'],approval(parent))
    row=records.get(SnapshotRow,child['id'])
    body=json.loads(row.body)
    for field in ('parent_snapshot_id','parent_input_hash','proposed_input_hash'):
        del body['approval'][field]
    row.body=canonical(body).decode();row.record_hash=sha256(row.body.encode())
    original=row.body
    with pytest.raises(ValueError,match='not registered'):
        read_snapshot(records,child['id'])
    records.rows[(LegacyApprovalRow,child['id'])]=LegacyApprovalRow(snapshot_id=child['id'],record_hash=row.record_hash)
    assert read_snapshot(records,child['id'])[0].content_hash==child['input_hash']
    assert row.body==original


def test_unbound_legacy_json_cannot_authorize_new_correction():
    from risklens.storage import save_snapshot
    records,parent,_=saved_records(ROOT/'sample-data/demo/split')
    data=approval(parent)
    for field in ('parent_snapshot_id','parent_input_hash','proposed_input_hash'):
        del data[field]
    with pytest.raises(ValueError):
        save_snapshot(records,InputSnapshot.capture(CLEAN),parent['id'],data)


def test_registered_legacy_report_replay_keeps_old_envelopes():
    from risklens.storage import LegacyApprovalRow,SnapshotRow,save_snapshot,save_report,replay_report
    records,parent,_=saved_records(ROOT/'sample-data/demo/split')
    child=save_snapshot(records,InputSnapshot.capture(CLEAN),parent['id'],approval(parent))
    report=save_report(records,child['id'])
    snapshot_row=records.get(SnapshotRow,child['id']);snapshot_body=json.loads(snapshot_row.body)
    for field in ('parent_snapshot_id','parent_input_hash','proposed_input_hash'):
        del snapshot_body['approval'][field]
    snapshot_row.body=canonical(snapshot_body).decode();snapshot_row.record_hash=sha256(snapshot_row.body.encode())
    records.rows[(LegacyApprovalRow,child['id'])]=LegacyApprovalRow(snapshot_id=child['id'],record_hash=snapshot_row.record_hash)
    def old_report(b):
        b['corrections'][0]['approval']=snapshot_body['approval']
        b['snapshot_record_hash']=snapshot_row.record_hash
    change_record(records,report,old_report)
    report_row=records.get(ReportRow,report['id'])
    originals=(snapshot_row.body,report_row.body)
    result=replay_report(records,report['id'])
    assert result['approval_binding_status']==['legacy_unbound']
    assert result['numerical_agreement']
    assert (snapshot_row.body,report_row.body)==originals
