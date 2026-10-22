"""Read-only persisted-report verification adapter; detection/comparison stay pure."""
from .comparisons import compare_reports
from .storage import ReportRow,_verified,read_snapshot,replay_report
from .snapshots import INPUT_FILES


def compare_saved_reports(session,original_id,corrected_id):
    replays=[replay_report(session,ident) for ident in (original_id,corrected_id)]
    if not all(r['numerical_agreement'] for r in replays):
        raise ValueError('saved report replay mismatch; comparison rejected')
    a=_verified(session.get(ReportRow,original_id));b=_verified(session.get(ReportRow,corrected_id))
    result=compare_reports(a['result'],b['result'])
    first,first_meta=read_snapshot(session,a['snapshot_id'])
    second,second_meta=read_snapshot(session,b['snapshot_id'])
    chain=b['corrections']
    start=next((i for i,item in enumerate(chain) if item['parent_id']==a['snapshot_id']),None)
    intervening=chain[start:] if start is not None else []
    lineage=bool(intervening) and intervening[-1]['snapshot_id']==b['snapshot_id']
    bound=lineage and all(all(k in item['approval'] for k in ('parent_snapshot_id','parent_input_hash','proposed_input_hash')) for item in intervening)
    unchanged={name:first.files[name]==second.files[name] for name in INPUT_FILES if name!='prices.csv'}
    result['checks'].update(approved_descendant_lineage=bool(bound),nonprice_inputs_unchanged=all(unchanged.values()))
    result['nonprice_input_checks']=unchanged
    impact=result['compatibility_passed'] and bool(bound) and all(unchanged.values())
    result.update(original_report_id=original_id,corrected_report_id=corrected_id,
        original_input_hash=first.content_hash,corrected_input_hash=second.content_hash,
        integrity_verified=True,replay_verified=True,correction_impact=impact,
        classification='correction_impact' if impact else result['classification'],
        correction_impact_rejection_reasons=[k for k,v in result['checks'].items() if not v],
        limitations=['Correction impact means an approved controlled input comparison, not causal attribution.',
            'Unrelated or legacy-unbound lineage and changed nonprice input bytes cannot support that label.'])
    return result
