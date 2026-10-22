"""Real PostgreSQL Stage 4 workflow; uses the existing isolated migration fixture."""
import json
from sqlalchemy.orm import Session
from risklens.snapshots import InputSnapshot
from risklens.investigations import investigate
from risklens.comparison_storage import compare_saved_reports
from risklens.storage import save_snapshot,save_report
from test_stage3_postgres import pg,DATA,explicit_approval


def test_investigate_approve_correct_and_compare_in_postgres(pg):
    c,_=pg
    captured=InputSnapshot.capture(DATA/'split')
    evidence=investigate(captured)
    assert any(i['rule']=='potential_unadjusted_split' for i in evidence['issues'])
    with Session(c) as s,s.begin():
        parent=save_snapshot(s,captured)
        original=save_report(s,parent['id'])
        child=save_snapshot(s,InputSnapshot.capture(DATA/'clean'),parent['id'],explicit_approval(parent))
        corrected=save_report(s,child['id'])
        compared=compare_saved_reports(s,original['id'],corrected['id'])
        assert compared['correction_impact'] and compared['replay_verified']
        assert compared['original_input_hash']==captured.content_hash
        assert compared['corrected_input_hash']==child['input_hash']
        assert all(row['difference'] is not None for row in compared['metrics'])


def test_unrelated_saved_reports_do_not_claim_correction_impact(pg):
    c,_=pg
    with Session(c) as s,s.begin():
        a=save_snapshot(s,InputSnapshot.capture(DATA/'split'));ra=save_report(s,a['id'])
        b=save_snapshot(s,InputSnapshot.capture(DATA/'clean'));rb=save_report(s,b['id'])
        compared=compare_saved_reports(s,ra['id'],rb['id'])
        assert compared['compatibility_passed'] and not compared['correction_impact']
        assert 'approved_descendant_lineage' in compared['correction_impact_rejection_reasons']
