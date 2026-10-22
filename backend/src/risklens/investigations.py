"""Deterministic diagnostics of captured inputs; no persistence or repairs."""
import csv
import io
import json
import math
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date
from .snapshots import InputSnapshot, canonical, sha256
from .schemas import Price
from pydantic import TypeAdapter
PRICE_DATE = TypeAdapter(date)


@dataclass(frozen=True)
class InvestigationPolicy:
    unchanged_sessions: int = 5
    large_return_threshold: float = .20
    split_relative_tolerance: float = .03

    def __post_init__(self):
        if isinstance(self.unchanged_sessions,bool) or not isinstance(self.unchanged_sessions,int) or self.unchanged_sessions<2:
            raise ValueError('unchanged_sessions must be an integer >= 2')
        for value in (self.large_return_threshold,self.split_relative_tolerance):
            if isinstance(value,bool) or not math.isfinite(value) or value<=0:
                raise ValueError('thresholds must be finite and positive')
        if self.split_relative_tolerance>=.5:
            raise ValueError('split_relative_tolerance must be below .5')


def investigate(snapshot: InputSnapshot, policy=None):
    policy=policy or InvestigationPolicy()
    issues=[]
    def add(rule,instrument,dates,observed,blocking=False,category='suspected_data_issue',action=None):
        issue={'instrument':instrument,'dates':sorted(set(dates)), 'rule':rule,
            'observed_values':observed,'category':category,
            'severity':'error' if blocking else 'warning','calculation_blocking':blocking,
            'suggested_review_action':action or 'Review source records and supporting evidence; do not repair automatically.'}
        issue['issue_id']=sha256(canonical(issue))
        issues.append(issue)
    def rows(name,columns):
        try:
            reader=csv.DictReader(io.StringIO(snapshot.files[name].decode('utf-8'),newline=''),strict=True)
            if reader.fieldnames!=columns:
                raise ValueError(f'expected columns {columns}')
            values=list(reader)
            if any(None in r or None in r.values() for r in values):
                raise ValueError('malformed CSV row')
            return values
        except (ValueError,UnicodeError,csv.Error) as exc:
            add('invalid_csv',None,[],{'file':name,'error':str(exc)},True,'confirmed_integrity_error')
            return []
    registry=rows('instruments.csv',['instrument','sector','currency'])
    names=sorted({r['instrument'] for r in registry if r['instrument']})
    calendar=[]
    for row in rows('calendar.csv',['date']):
        try:
            calendar.append(date.fromisoformat(row['date']).isoformat())
        except ValueError:
            add('invalid_calendar_date',None,[],{'date':row['date']},True,'confirmed_integrity_error')
    sessions=sorted(set(calendar))
    calendar_usable=bool(sessions) and calendar==sessions
    if not calendar_usable:
        add('invalid_calendar',None,[],{'supplied_dates':calendar},True,'confirmed_integrity_error',
            'Provide an explicit sorted, unique, nonempty expected-session calendar before inferring gaps.')
    records=rows('prices.csv',['date','instrument','adjusted_close'])
    keyed=defaultdict(list)
    valid={}
    for number,row in enumerate(records,start=1):
        instrument=row['instrument']; rawdate=row['date']
        try:
            day=PRICE_DATE.validate_python(rawdate).isoformat()
        except ValueError:
            add('invalid_price_date',instrument,[],{'date':rawdate,'row_number':number},True,'confirmed_integrity_error')
            continue
        keyed[(day,instrument)].append({'row_number':number,'adjusted_close':row['adjusted_close']})
        try:
            price=Price.model_validate(row).adjusted_close
        except ValueError:
            add('invalid_price',instrument,[day],{'raw_price':row['adjusted_close'],'row_number':number},True,'confirmed_integrity_error',
                'Verify the raw source; nonpositive, missing or nonfinite prices cannot enter risk calculations.')
            continue
        if instrument not in names:
            add('unknown_instrument',instrument,[day],{'price':price},True,'confirmed_integrity_error')
        elif day not in sessions:
            add('outside_calendar',instrument,[day],{'price':price},True,'confirmed_integrity_error')
        else:
            valid[(day,instrument)]=price
    for (day,instrument),values in sorted(keyed.items()):
        if len(values)>1:
            valid.pop((day,instrument),None)
            add('duplicate_observation',instrument,[day],{'records':values,'count':len(values)},True,'confirmed_integrity_error',
                'Reconcile duplicate source records; retain the original capture and approve a separate corrected version.')
    if calendar_usable:
        for instrument in names:
            missing=[day for day in sessions if (day,instrument) not in keyed]
            if missing:
                add('missing_observation',instrument,missing,{'expected_calendar_dates':missing},True,'confirmed_integrity_error',
                    'Check exchange calendar/source coverage. These dates are expected sessions under the supplied calendar; do not forward-fill.')
    actions=rows('corporate_actions.csv',['date','instrument','action','value','evidence'])
    # Invalid dates/prices/duplicates never bridge into an inferred return.
    for instrument in names:
        run=[]
        def finish_run():
            if len(run)>=policy.unchanged_sessions:
                add('unchanged_price_sequence',instrument,[d for d,p in run],
                    {'prices':[{'date':d,'adjusted_close':p} for d,p in run],
                     'sessions':len(run),'minimum_sessions':policy.unchanged_sessions},
                    action='Review source refresh timestamps, trading activity and suspensions. Equal closes alone do not establish stale data.')
        previous=None
        for day in sessions if calendar_usable else []:
            current=valid.get((day,instrument))
            if current is None:
                finish_run();run=[];previous=None;continue
            if run and run[-1][1]!=current:
                finish_run();run=[]
            run.append((day,current))
            if previous:
                prior_day,prior=previous
                ratio=current/prior
                if not math.isfinite(ratio):
                    add('unrepresentable_return',instrument,[prior_day,day],{'prior_price':prior,'price':current},True,
                        'confirmed_numeric_limit','Verify extreme numeric range; the numerical engine independently rejects overflow.')
                else:
                    ret=ratio-1
                    if abs(ret)>=policy.large_return_threshold or math.isclose(abs(ret),policy.large_return_threshold,rel_tol=0,abs_tol=1e-12):
                        add('large_return',instrument,[prior_day,day],
                            {'prior_price':prior,'price':current,'simple_return':ret,'absolute_threshold':policy.large_return_threshold},
                            category='market_movement_review',
                            action='Check news, source prices and corporate actions. A large market move alone is not a confirmed data error.')
                    factors=(2,3,4,5,10)
                    targets=[(factor,target) for factor in factors for target in (1/factor,float(factor))]
                    candidates=[(abs(ratio/target-1),factor,target) for factor,target in targets
                        if abs(ratio/target-1)<=policy.split_relative_tolerance or math.isclose(abs(ratio/target-1),policy.split_relative_tolerance,rel_tol=0,abs_tol=1e-12)]
                    if candidates:
                        error,factor,target=min(candidates)
                        ledger=[a for a in actions if a['instrument']==instrument and a['date']==day and a['action']=='split']
                        add('potential_unadjusted_split',instrument,[prior_day,day],
                            {'prior_price':prior,'price':current,'price_ratio':ratio,'candidate_factor':factor,
                             'target_ratio':target,'relative_distance':error,'relative_tolerance':policy.split_relative_tolerance,
                             'same_date_split_ledger':ledger},
                            action='Verify adjustment basis and authoritative corporate-action terms. Near-ratio and supplied ledger evidence are clues, not permission to repair.')
            previous=(day,current)
        finish_run()
    # Existing loader remains the final authority over every schema/integrity rule.
    try:
        validated=snapshot.validated_dataset()
        is_valid=True;validation_error=None
    except (ValueError,UnicodeError,csv.Error) as exc:
        validated=None;is_valid=False;validation_error=str(exc)
        if not any(i['calculation_blocking'] for i in issues):
            add('dataset_validation_failure',None,[],{'error':validation_error},True,'confirmed_integrity_error')
    try:
        raw_provenance=json.loads(snapshot.files['provenance.json'])
        mode=raw_provenance.get('mode','unknown') if isinstance(raw_provenance,dict) else 'unknown'
    except (ValueError,UnicodeError):
        mode='unknown'
    if mode not in ('synthetic','historical_csv'):
        mode='unknown'
    issues.sort(key=lambda i:(i['instrument'] or '',i['dates'],i['rule'],i['issue_id']))
    return {'format':'risklens-investigation-v1','mode':mode,
        'label':'SYNTHETIC DEMONSTRATION' if mode=='synthetic' else 'IMPORTED CSV; user supplied source claims' if mode=='historical_csv' else 'UNKNOWN DATA MODE',
        'input_hash':snapshot.content_hash,'input_file_hashes':{name:sha256(value) for name,value in sorted(snapshot.files.items())},'policy':asdict(policy),
        'dataset_valid':is_valid,'calculation_blocked':not is_valid or any(i['calculation_blocking'] for i in issues),'validation_error':validation_error,
        'issues':issues,'issue_count':len(issues),
        'limitations':['No fixture metadata or clean reference is used for detection.',
            'No prices are filled, dropped, overwritten or automatically repaired.',
            'Suspicion does not prove an error; an explicit calendar defines expected sessions.']}


def export_investigation(result,snapshot,output):
    from .comparisons import export_analysis
    if result['input_hash']!=snapshot.content_hash:
        raise ValueError('investigation/capture hash mismatch')
    export_analysis(result,output)
    (output/'capture.json').write_bytes(canonical({'input_hash':snapshot.content_hash,'inputs':snapshot.payload()}))
