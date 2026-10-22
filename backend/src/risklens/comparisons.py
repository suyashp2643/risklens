"""Pure report comparison and shared JSON/CSV exports; no database imports."""
import csv
import json
import math
from datetime import date
from pathlib import Path

METRICS=('cumulative_return','annualized_sample_volatility','maximum_drawdown',
    'historical_one_day_var','historical_one_day_expected_shortfall',
    'historical_one_day_var_currency','historical_one_day_expected_shortfall_currency')


def _validate_report(report):
    for key in ('metrics','daily','configuration','portfolio','provenance','engine_version','mode'):
        if key not in report:
            raise ValueError(f'incomplete report: missing {key}')
    required_config=('confidence','trading_days_per_year','horizon_days','quantile_method','es_method',
        'minimum_observations','minimum_tail_observations','required_observations','loss_sign','fraction_units',
        'currency_loss_basis','historical_convention','drawdown_sign')
    if not isinstance(report['configuration'],dict) or not all(k in report['configuration'] for k in required_config):
        raise ValueError('report configuration incomplete')
    if not isinstance(report['daily'],list) or len(report['daily'])<2:
        raise ValueError('report must include dated daily observations and initial value')
    dates=[row['date'] for row in report['daily']]
    if dates!=sorted(set(dates)):
        raise ValueError('report dates must be sorted and unique')
    for day in dates:
        date.fromisoformat(day)
    for metric in METRICS:
        value=report['metrics'].get(metric)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('report requires seven finite numeric metrics')
    if report['portfolio']['base_currency']!=report['provenance']['currency']:
        raise ValueError('report currency metadata inconsistent')
    if report['provenance']['start_date']!=dates[0] or report['provenance']['end_date']!=dates[-1]:
        raise ValueError('report provenance/dates inconsistent')
    if report['mode']!=report['provenance']['mode']:
        raise ValueError('report data mode metadata inconsistent')
    return dates


def compare_reports(original,corrected):
    dates_a=_validate_report(original);dates_b=_validate_report(corrected)
    checks={
        'dates':dates_a==dates_b,
        'currency':original['portfolio']['base_currency']==corrected['portfolio']['base_currency'],
        'portfolio':original['portfolio']==corrected['portfolio'],
        'configuration':original['configuration']==corrected['configuration'],
        'engine_version':original['engine_version']==corrected['engine_version'],
        'data_mode':original['mode']==corrected['mode'],
        'adjustment_basis':original['provenance']['adjustment_basis']==corrected['provenance']['adjustment_basis'],
        'calendar_assumption':original['provenance']['calendar_assumption']==corrected['provenance']['calendar_assumption']}
    compatible=all(checks.values())
    rows=[]
    for metric in METRICS:
        a=original['metrics'][metric];b=corrected['metrics'][metric]
        unit=original['portfolio']['base_currency'] if metric.endswith('_currency') else 'decimal_fraction'
        delta=b-a if compatible else None
        if delta is not None and not math.isfinite(delta):
            raise ValueError('comparison difference exceeds numerical range')
        rows.append({'metric':metric,'original':a,'corrected':b,'difference':delta,
            'unit':unit,'original_unit':unit,
            'corrected_unit':corrected['portfolio']['base_currency'] if metric.endswith('_currency') else 'decimal_fraction',
            'difference_percentage_points':delta*100 if delta is not None and unit=='decimal_fraction' else None})
    return {'format':'risklens-comparison-v1','mode':original['mode'] if original['mode']==corrected['mode'] else 'mixed',
        'label':'SYNTHETIC DEMONSTRATION' if original['mode']==corrected['mode']=='synthetic' else 'REPORT COMPARISON',
        'classification':'compatible_difference' if compatible else 'incompatible_reports',
        'correction_impact':False,'compatibility_passed':compatible,'checks':checks,
        'incompatibilities':[k for k,v in checks.items() if not v],
        'metrics':rows,'confidence':original['configuration'].get('confidence'),
        'difference_convention':'corrected minus original; decimal fractions and percentage-point differences are separate',
        'limitations':['Standalone report JSON does not prove input integrity or approved lineage.',
            'Incompatible reports retain values but suppress differences; no correction-impact claim.']}


def export_analysis(result,output: Path):
    output.mkdir(parents=True,exist_ok=False)
    (output/'report.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    investigation=result['format']=='risklens-investigation-v1'
    records=result['issues'] if investigation else result['metrics']
    fields=(['mode','input_hash','issue_id','instrument','dates','rule','category','severity','calculation_blocking','observed_values','suggested_review_action']
        if investigation else ['mode','classification','correction_impact','confidence','metric','original','corrected','difference','unit','original_unit','corrected_unit','difference_percentage_points'])
    with (output/('issues.csv' if investigation else 'comparison.csv')).open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for record in records:
            row={key:record.get(key,result.get(key)) for key in fields}
            for key,value in row.items():
                if isinstance(value,(dict,list)):
                    row[key]=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
            writer.writerow(row)
