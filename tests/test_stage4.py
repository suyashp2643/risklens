"""Rule evidence, boundary references, compatibility and approved lineage tests."""
import copy
import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path
import pytest
from risklens.investigations import investigate,InvestigationPolicy
from risklens.snapshots import InputSnapshot,canonical
from risklens.reporting import calculate_from_snapshot,calculate_report
from risklens.comparisons import compare_reports,export_analysis
from risklens.comparison_storage import compare_saved_reports
from risklens.storage import save_snapshot,save_report
from test_stage3 import MemoryRecords,approval

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'sample-data/demo'


def rules(result,name):
    return [issue for issue in result['issues'] if issue['rule']==name]


@pytest.mark.parametrize('fixture,rule,blocked',[('missing','missing_observation',True),
    ('duplicate','duplicate_observation',True),('stale','unchanged_price_sequence',False),
    ('split','potential_unadjusted_split',False)])
def test_existing_fixture_detection_without_oracle(fixture,rule,blocked,tmp_path):
    source=tmp_path/'input';shutil.copytree(DATA/fixture,source)
    truth=json.loads((source/'fixture.json').read_text());(source/'fixture.json').unlink()
    result=investigate(InputSnapshot.capture(source))
    found=rules(result,rule)
    assert found and any(i['instrument']==truth['instrument'] for i in found)
    assert any(set(i['dates'])&set(truth['affected_dates']) for i in found)
    assert result['calculation_blocked']==blocked
    assert result['mode']=='synthetic'
    assert all(i['suggested_review_action'] for i in result['issues'])
    assert result==investigate(InputSnapshot.capture(source))
    if blocked:
        with pytest.raises(ValueError):
            calculate_from_snapshot(InputSnapshot.capture(source))


def test_clean_has_no_findings():
    result=investigate(InputSnapshot.capture(DATA/'clean'))
    assert result['dataset_valid'] and result['issues']==[]


def altered_prices(tmp_path,values):
    source=tmp_path/'dataset';shutil.copytree(DATA/'clean',source)
    with (source/'prices.csv').open() as f:
        raw=list(csv.DictReader(f))
    name='SYN_BANK';selected=[r for r in raw if r['instrument']==name]
    for row,value in zip(selected,values):
        row['adjusted_close']=str(value)
    with (source/'prices.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['date','instrument','adjusted_close']);w.writeheader();w.writerows(raw)
    return source,selected


@pytest.mark.parametrize('value',['0','-1','NaN','Infinity',''])
def test_invalid_price_evidence_blocks_risk(tmp_path,value):
    source,_=altered_prices(tmp_path,[value])
    result=investigate(InputSnapshot.capture(source))
    issue=rules(result,'invalid_price')[0]
    assert issue['observed_values']['raw_price']==value
    assert issue['category']=='confirmed_integrity_error' and issue['calculation_blocking']
    with pytest.raises(ValueError):
        calculate_report(source)


@pytest.mark.parametrize('move,flag',[(119.999,False),(120,True),(120.001,True),(80,True)])
def test_large_return_inclusive_boundary(tmp_path,move,flag):
    source,selected=altered_prices(tmp_path,[100,move])
    issues=rules(investigate(InputSnapshot.capture(source)),'large_return')
    first=[i for i in issues if i['dates'][-1]==selected[1]['date']]
    assert bool(first)==flag


def test_legitimate_market_jump_is_review_not_confirmed_error(tmp_path):
    source,selected=altered_prices(tmp_path,[100,150])
    result=investigate(InputSnapshot.capture(source))
    issue=[i for i in rules(result,'large_return') if i['dates'][-1]==selected[1]['date']][0]
    assert issue['category']=='market_movement_review'
    assert not issue['calculation_blocking']
    assert not result['calculation_blocked']
    assert calculate_report(source)['observations']==755


@pytest.mark.parametrize('count,flag',[(4,False),(5,True),(6,True)])
def test_unchanged_sequence_boundary(tmp_path,count,flag):
    source,selected=altered_prices(tmp_path,[100]*count)
    issues=rules(investigate(InputSnapshot.capture(source)),'unchanged_price_sequence')
    first=[i for i in issues if selected[0]['date'] in i['dates']]
    assert bool(first)==flag
    if flag:
        assert first[0]['category']=='suspected_data_issue' and not first[0]['calculation_blocking']
        assert first[0]['observed_values']['sessions']==count


@pytest.mark.parametrize('price,flag',[(51.5,True),(51.5001,False),(48.5,True)])
def test_split_relative_boundary(tmp_path,price,flag):
    source,selected=altered_prices(tmp_path,[100,price])
    found=[i for i in rules(investigate(InputSnapshot.capture(source)),'potential_unadjusted_split')
        if i['dates'][-1]==selected[1]['date']]
    assert bool(found)==flag
    if found:
        assert found[0]['observed_values']['same_date_split_ledger']==[]
        assert not found[0]['calculation_blocking']


def test_no_returns_bridge_a_missing_session(tmp_path):
    source,selected=altered_prices(tmp_path,[100,101,1000])
    with (source/'prices.csv').open() as f:
        raw=list(csv.DictReader(f))
    raw=[r for r in raw if not(r['instrument']=='SYN_BANK' and r['date']==selected[1]['date'])]
    with (source/'prices.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['date','instrument','adjusted_close']);w.writeheader();w.writerows(raw)
    result=investigate(InputSnapshot.capture(source))
    assert not any(i['dates']==[selected[0]['date'],selected[2]['date']] for i in rules(result,'large_return'))
    assert result['calculation_blocked']


def test_supplied_calendar_holiday_not_missing(tmp_path):
    source,_=altered_prices(tmp_path,[])
    calendar=(source/'calendar.csv').read_text().splitlines()
    removed=calendar[12]
    (source/'calendar.csv').write_text('\n'.join(x for x in calendar if x!=removed)+'\n')
    with (source/'prices.csv').open() as f:
        raw=list(csv.DictReader(f))
    with (source/'prices.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['date','instrument','adjusted_close']);w.writeheader()
        w.writerows(r for r in raw if r['date']!=removed)
    assert not rules(investigate(InputSnapshot.capture(source)),'missing_observation')


def test_schema_failures_still_block(tmp_path):
    source,_=altered_prices(tmp_path,[])
    p=json.loads((source/'portfolio.json').read_text());p['weights'][0]['weight']=.8
    (source/'portfolio.json').write_text(json.dumps(p))
    result=investigate(InputSnapshot.capture(source))
    assert result['calculation_blocked']
    assert rules(result,'dataset_validation_failure')
    with pytest.raises(ValueError):
        calculate_report(source)


def test_fixture_metadata_changes_never_change_evidence(tmp_path):
    source,_=altered_prices(tmp_path,[100,50])
    before=investigate(InputSnapshot.capture(source))
    (source/'fixture.json').write_text('{"kind":"clean","instrument":"FAKE"}')
    assert investigate(InputSnapshot.capture(source))==before


@pytest.mark.parametrize('kwargs',[{'unchanged_sessions':1},{'unchanged_sessions':True},
    {'large_return_threshold':0},{'large_return_threshold':float('nan')},
    {'split_relative_tolerance':.5}])
def test_invalid_policy(kwargs):
    with pytest.raises(ValueError):
        InvestigationPolicy(**kwargs)


def test_report_comparison_reference_units():
    original=calculate_report(DATA/'split');corrected=calculate_report(DATA/'clean')
    result=compare_reports(original,corrected)
    assert result['compatibility_passed'] and not result['correction_impact']
    for row in result['metrics']:
        assert row['difference']==pytest.approx(corrected['metrics'][row['metric']]-original['metrics'][row['metric']])
        if row['unit']=='decimal_fraction':
            assert row['difference_percentage_points']==pytest.approx(row['difference']*100)
        else:
            assert row['unit']=='USD' and row['difference_percentage_points'] is None


@pytest.mark.parametrize('kind',['dates','currency','portfolio','configuration','engine_version','data_mode','adjustment_basis','calendar_assumption'])
def test_comparison_compatibility_rejections(kind):
    a=calculate_report(DATA/'clean');b=copy.deepcopy(a)
    if kind=='dates':
        b['daily'].pop(10)
    elif kind=='currency':
        b['portfolio']['base_currency']='EUR';b['provenance']['currency']='EUR'
    elif kind=='portfolio':
        b['portfolio']['valuation']=200000
    elif kind=='configuration':
        b['configuration']['confidence']=.99
    elif kind=='engine_version':
        b['engine_version']='unavailable'
    elif kind=='data_mode':
        b['mode']='historical_csv';b['provenance']['mode']='historical_csv'
    else:
        b['provenance'][kind]='different'
    result=compare_reports(a,b)
    assert kind in result['incompatibilities']
    assert not result['correction_impact']
    assert all(row['difference'] is None for row in result['metrics'])


def test_approved_saved_comparison_and_unrelated_lineage():
    records=MemoryRecords()
    parent=save_snapshot(records,InputSnapshot.capture(DATA/'split'))
    a=save_report(records,parent['id'])
    child=save_snapshot(records,InputSnapshot.capture(DATA/'clean'),parent['id'],approval(parent,DATA/'clean'))
    b=save_report(records,child['id'])
    result=compare_saved_reports(records,a['id'],b['id'])
    assert result['correction_impact'] and result['classification']=='correction_impact'
    unrelated=save_snapshot(records,InputSnapshot.capture(DATA/'clean'))
    c=save_report(records,unrelated['id'])
    assert not compare_saved_reports(records,a['id'],c['id'])['correction_impact']


def test_export_and_cli_complete_workflow(tmp_path):
    out=tmp_path/'issues'
    run=subprocess.run([sys.executable,'-m','risklens.cli','investigate',str(DATA/'missing'),'--output',str(out)],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)['calculation_blocked']
    assert (out/'issues.csv').exists()
    for fixture in ('split','clean'):
        run=subprocess.run([sys.executable,'-m','risklens.cli','report',str(DATA/fixture),'--output',str(tmp_path/fixture)],capture_output=True,text=True)
        assert run.returncode==0,run.stderr
    comparison=tmp_path/'comparison'
    run=subprocess.run([sys.executable,'-m','risklens.cli','compare',str(tmp_path/'split/report.json'),str(tmp_path/'clean/report.json'),
        '--output',str(comparison)],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    result=json.loads((comparison/'report.json').read_text())
    assert result['compatibility_passed'] and not result['correction_impact']
    with (comparison/'comparison.csv').open() as f:
        assert len(list(csv.DictReader(f)))==7
    with pytest.raises(FileExistsError):
        export_analysis(result,comparison)


def test_empty_findings_export_still_has_csv_header(tmp_path):
    out=tmp_path/'empty';export_analysis(investigate(InputSnapshot.capture(DATA/'clean')),out)
    with (out/'issues.csv').open() as f:
        assert list(csv.DictReader(f))==[]


def test_duplicate_conflicting_values_excluded_from_return_diagnostics(tmp_path):
    source,selected=altered_prices(tmp_path,[100,50])
    with (source/'prices.csv').open('a') as f:
        f.write(f"{selected[1]['date']},SYN_BANK,10000\n")
    result=investigate(InputSnapshot.capture(source))
    assert result['calculation_blocked']
    assert rules(result,'duplicate_observation')
    assert not any(i['instrument']=='SYN_BANK' and selected[1]['date'] in i['dates']
        for i in rules(result,'large_return'))


def test_invalid_csv_schema_is_diagnosed_without_risk(tmp_path):
    source,_=altered_prices(tmp_path,[])
    (source/'prices.csv').write_text('wrong,columns\n1,2\n')
    result=investigate(InputSnapshot.capture(source))
    assert result['calculation_blocked'] and rules(result,'invalid_csv')
    with pytest.raises(ValueError):
        calculate_report(source)


def test_approved_portfolio_change_cannot_claim_correction_impact(tmp_path):
    records=MemoryRecords()
    parent=save_snapshot(records,InputSnapshot.capture(DATA/'split'));a=save_report(records,parent['id'])
    source,_=altered_prices(tmp_path,[])
    p=json.loads((source/'portfolio.json').read_text());p['valuation']=200000
    (source/'portfolio.json').write_text(json.dumps(p))
    child=save_snapshot(records,InputSnapshot.capture(source),parent['id'],approval(parent,source))
    b=save_report(records,child['id'])
    result=compare_saved_reports(records,a['id'],b['id'])
    assert not result['correction_impact']
    assert not result['checks']['portfolio']
    assert not result['checks']['nonprice_inputs_unchanged']


def test_nonfinite_comparison_metric_rejected():
    a=calculate_report(DATA/'clean');b=copy.deepcopy(a)
    b['metrics']['cumulative_return']=float('nan')
    with pytest.raises(ValueError,match='finite'):
        compare_reports(a,b)


def test_invalid_capture_export_preserves_bytes_and_survives_source_removal(tmp_path):
    from risklens.investigations import export_investigation
    source=tmp_path/'source';shutil.copytree(DATA/'missing',source)
    captured=InputSnapshot.capture(source);result=investigate(captured)
    out=tmp_path/'export';export_investigation(result,captured,out)
    saved=json.loads((out/'capture.json').read_text())
    shutil.rmtree(source)
    restored=InputSnapshot.restore(saved['inputs'],saved['input_hash'])
    assert restored.files==captured.files
    assert investigate(restored)==result
    with pytest.raises(ValueError):
        calculate_from_snapshot(restored)


def test_incomplete_report_configuration_is_rejected():
    a=calculate_report(DATA/'clean');b=copy.deepcopy(a)
    del b['configuration']['quantile_method']
    with pytest.raises(ValueError,match='configuration incomplete'):
        compare_reports(a,b)


def test_extreme_numeric_range_is_blocked_in_diagnostics(tmp_path):
    source,_=altered_prices(tmp_path,[1e-308,1e308])
    result=investigate(InputSnapshot.capture(source))
    assert result['dataset_valid'] and result['calculation_blocked']
    assert rules(result,'unrepresentable_return')[0]['calculation_blocking']


def test_price_date_spellings_follow_existing_pydantic_schema(tmp_path):
    source,selected=altered_prices(tmp_path,[])
    text=(source/'prices.csv').read_text()
    day=selected[0]['date']
    # Existing Price date schema accepts exact midnight datetimes as a date.
    text=text.replace(day+',',day+'T00:00:00,',1)
    (source/'prices.csv').write_text(text)
    assert calculate_report(source)['observations']==755
    result=investigate(InputSnapshot.capture(source))
    assert result['dataset_valid'] and not result['calculation_blocked']
    assert not rules(result,'invalid_price_date')


def test_legacy_unbound_correction_is_not_exact_byte_approval():
    from risklens.storage import SnapshotRow,LegacyApprovalRow
    from risklens.snapshots import sha256
    records=MemoryRecords()
    parent=save_snapshot(records,InputSnapshot.capture(DATA/'split'));a=save_report(records,parent['id'])
    child=save_snapshot(records,InputSnapshot.capture(DATA/'clean'),parent['id'],approval(parent,DATA/'clean'))
    row=records.get(SnapshotRow,child['id']);body=json.loads(row.body)
    for key in ('parent_snapshot_id','parent_input_hash','proposed_input_hash'):
        del body['approval'][key]
    row.body=canonical(body).decode();row.record_hash=sha256(row.body.encode())
    records.rows[(LegacyApprovalRow,child['id'])]=LegacyApprovalRow(snapshot_id=child['id'],record_hash=row.record_hash)
    b=save_report(records,child['id'])
    result=compare_saved_reports(records,a['id'],b['id'])
    assert result['compatibility_passed'] and result['replay_verified']
    assert not result['correction_impact']
    assert 'approved_descendant_lineage' in result['correction_impact_rejection_reasons']
