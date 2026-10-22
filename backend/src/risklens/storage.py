"""Transactional PostgreSQL persistence and offline-from-source report replay."""
import json
import os
import uuid
from datetime import datetime, timezone
from math import isclose
from sqlalchemy import create_engine, Text, DateTime, ForeignKey, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, Session
from sqlalchemy.engine import make_url
from sqlalchemy.dialects.postgresql import JSONB
from pydantic import Field, model_validator
from .schemas import Model
from .snapshots import InputSnapshot, canonical, sha256, HASH_FORMAT
from .reporting import calculate_from_snapshot
from .calculations import ENGINE_VERSION

REPLAY_RTOL = 1e-12
REPLAY_ATOL = 1e-12


class Base(DeclarativeBase):
    pass


class SnapshotRow(Base):
    __tablename__ = 'dataset_snapshots'
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey('dataset_snapshots.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    body: Mapped[str] = mapped_column(Text)
    record_hash: Mapped[str] = mapped_column(Text)


class ReportRow(Base):
    __tablename__ = 'saved_reports'
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey('dataset_snapshots.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    body: Mapped[str] = mapped_column(Text)
    record_hash: Mapped[str] = mapped_column(Text)


class LegacyApprovalRow(Base):
    __tablename__ = 'legacy_correction_approvals'
    snapshot_id: Mapped[str] = mapped_column(ForeignKey('dataset_snapshots.id'), primary_key=True)
    record_hash: Mapped[str] = mapped_column(Text)


class Approval(Model):
    parent_snapshot_id: str = Field(min_length=1)
    parent_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposed_input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_id: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    approved_at: datetime
    decision: str = Field(pattern='^approved$')
    reason: str = Field(min_length=1)
    evidence: str = Field(min_length=1)

    @model_validator(mode='after')
    def explicit_record(self):
        if self.approved_at.tzinfo is None:
            raise ValueError('approval timestamp must have a timezone')
        if self.approved_at > datetime.now(timezone.utc):
            raise ValueError('approval timestamp cannot be in the future')
        for value in (self.parent_snapshot_id, self.record_id, self.approved_by, self.reason, self.evidence):
            if not value.strip():
                raise ValueError('approval fields cannot be blank')
        return self


def database_engine(url=None):
    url = url or os.environ.get('RISKLENS_DATABASE_URL')
    if not url:
        raise ValueError('set RISKLENS_DATABASE_URL')
    if make_url(url).drivername != 'postgresql+psycopg':
        raise ValueError('Stage 3 requires postgresql+psycopg')
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


def _now():
    return datetime.now(timezone.utc)


def _verified(row):
    if row is None:
        raise ValueError('saved record not found')
    raw = row.body.encode('utf-8')
    if sha256(raw) != row.record_hash:
        raise ValueError('record hash mismatch')
    body = json.loads(raw)
    if canonical(body) != raw or body.get('format') != HASH_FORMAT:
        raise ValueError('noncanonical or unsupported saved record')
    if body['id'] != row.id or datetime.fromisoformat(body['created_at']) != row.created_at:
        raise ValueError('record metadata mismatch')
    return body


def read_snapshot(session: Session, snapshot_id):
    row = session.get(SnapshotRow, snapshot_id)
    body = _verified(row)
    if body['parent_id'] != row.parent_id:
        raise ValueError('snapshot lineage mismatch')
    snapshot = InputSnapshot.restore(body['inputs'], body['input_hash'])
    if body['parent_id']:
        fields=('parent_snapshot_id','parent_input_hash','proposed_input_hash')
        if all(field in body['approval'] for field in fields):
            parent, _ = read_snapshot(session,body['parent_id'])
            _validate_binding(body['approval'], body['parent_id'], parent.content_hash, snapshot.content_hash)
        else:
            legacy=session.get(LegacyApprovalRow,body['id'])
            if legacy is None or legacy.record_hash != row.record_hash:
                raise ValueError('unbound approval is not registered migration-era history')
    return snapshot, body


def _validate_binding(approval, parent_id, parent_hash, proposed_hash):
    expected={'parent_snapshot_id':parent_id, 'parent_input_hash':parent_hash,
              'proposed_input_hash':proposed_hash}
    for key,value in expected.items():
        if approval.get(key) != value:
            raise ValueError(f'approval {key} mismatch')


def save_snapshot(session: Session, snapshot: InputSnapshot, parent_id=None, approval=None):
    snapshot.validated_dataset()  # Block integrity/schema failures before insertion.
    if bool(parent_id) != bool(approval):
        raise ValueError('corrections require both parent and explicit approval')
    version = 1
    if parent_id:
        parent, parent_body = read_snapshot(session, parent_id)
        if parent.content_hash == snapshot.content_hash:
            raise ValueError('correction must change captured input bytes')
        approval = Approval.model_validate(approval).model_dump(mode='json')
        _validate_binding(approval, parent_id, parent.content_hash, snapshot.content_hash)
        used = session.scalar(select(SnapshotRow.id).where(
            SnapshotRow.body.cast(JSONB).op("->", return_type=JSONB)("approval").op("->>", return_type=Text)("record_id") == approval['record_id']).limit(1))
        if used is not None:
            raise ValueError('approval record_id already used')
        version = parent_body['version'] + 1
    ident, timestamp = str(uuid.uuid4()), _now()
    body = {'format': HASH_FORMAT, 'id': ident, 'created_at': timestamp.isoformat(),
            'parent_id': parent_id, 'version': version, 'approval': approval,
            'inputs': snapshot.payload(), 'input_hash': snapshot.content_hash}
    raw = canonical(body)
    session.add(SnapshotRow(id=ident, parent_id=parent_id, created_at=timestamp,
                            body=raw.decode('utf-8'), record_hash=sha256(raw)))
    session.flush()
    return body


def save_report(session: Session, snapshot_id, confidence=.95, trading_days=252):
    snapshot, snapshot_body = read_snapshot(session, snapshot_id)
    result = calculate_from_snapshot(snapshot, confidence, trading_days)
    ident, timestamp = str(uuid.uuid4()), _now()
    body = {'format': HASH_FORMAT, 'id': ident, 'created_at': timestamp.isoformat(),
            'snapshot_id': snapshot_id, 'input_hash': snapshot.content_hash,
            'snapshot_record_hash': sha256(canonical(snapshot_body)),
            'kind': 'corrected_report' if snapshot_body['parent_id'] else 'original_report',
            'engine_version': ENGINE_VERSION,
            'configuration': result['configuration'],
            'configuration_hash': sha256(canonical(result['configuration'])),
            'result': result, 'result_hash': sha256(canonical(result)),
            'scenario_definitions': [], 'corrections': _correction_chain(session, snapshot_body)}
    raw = canonical(body)
    session.add(ReportRow(id=ident, snapshot_id=snapshot_id, created_at=timestamp,
                         body=raw.decode('utf-8'), record_hash=sha256(raw)))
    session.flush()
    return body


def _correction_chain(session, body):
    chain = []
    while body['parent_id']:
        chain.append({'snapshot_id': body['id'], 'parent_id': body['parent_id'],
                      'approval': body['approval']})
        _, body = read_snapshot(session, body['parent_id'])
    return list(reversed(chain))


def list_reports(session):
    return [{'id': row.id, 'snapshot_id': row.snapshot_id,
             'created_at': row.created_at.isoformat(),
             'kind': _verified(row)['kind'], 'record_hash': row.record_hash}
            for row in session.scalars(select(ReportRow).order_by(ReportRow.created_at, ReportRow.id))]


def numerical_agreement(old, new, path='result'):
    """Full recursive comparison: exact structure/text, tolerant finite numbers."""
    if isinstance(old, bool) or isinstance(new, bool):
        return old == new and type(old) is type(new)
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return isclose(old, new, rel_tol=REPLAY_RTOL, abs_tol=REPLAY_ATOL)
    if type(old) is not type(new):
        return False
    if isinstance(old, dict):
        return old.keys() == new.keys() and all(numerical_agreement(old[k], new[k], path+'.'+k) for k in old)
    if isinstance(old, list):
        return len(old) == len(new) and all(numerical_agreement(a,b,path) for a,b in zip(old,new))
    return old == new


def replay_report(session: Session, report_id):
    row = session.get(ReportRow, report_id)
    body = _verified(row)
    if body['snapshot_id'] != row.snapshot_id:
        raise ValueError('report snapshot metadata mismatch')
    snapshot, snapshot_body = read_snapshot(session, row.snapshot_id)
    if body['input_hash'] != snapshot.content_hash or body['snapshot_record_hash'] != sha256(canonical(snapshot_body)):
        raise ValueError('report input integrity mismatch')
    for key in ('configuration', 'result'):
        if sha256(canonical(body[key])) != body[key+'_hash']:
            raise ValueError(f'{key} hash mismatch')
    if body['engine_version'] != ENGINE_VERSION:
        raise ValueError(f"exact replay unsupported: saved engine {body['engine_version']}; available {ENGINE_VERSION}")
    if body['scenario_definitions'] or body['corrections'] != _correction_chain(session, snapshot_body):
        raise ValueError('unsupported scenarios or correction lineage mismatch')
    config = body['configuration']
    result = calculate_from_snapshot(snapshot, config['confidence'], config['trading_days_per_year'])
    if result['configuration'] != config:
        raise ValueError('saved configuration unsupported by available engine')
    agrees = numerical_agreement(body['result'], result)
    return {'kind': 'exact_replay' if agrees else 'replay_mismatch', 'report_id': report_id,
            'input_byte_integrity': True, 'saved_record_integrity': True,
            'numerical_agreement': agrees,
            'result_bytes_identical': canonical(result) == canonical(body['result']),
            'rtol': REPLAY_RTOL, 'atol': REPLAY_ATOL,
            'engine_version': ENGINE_VERSION, 'source_directory_read': False,
            'approval_binding_status': ['bound' if 'parent_snapshot_id' in item['approval'] else 'legacy_unbound'
                for item in body['corrections']]}
