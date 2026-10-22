import argparse
import json
from pathlib import Path
from pydantic import ValidationError
from .scenario_reporting import backtest, stress_scenario, historical_window, export_stage5
from .demo import generate
from .ingestion import load_dataset, DatasetError
from .reporting import calculate_report, export_report
from .snapshots import InputSnapshot
from .reviews import prepare_review
from .investigations import investigate, InvestigationPolicy, export_investigation
from .comparisons import compare_reports,export_analysis
from .comparison_storage import compare_saved_reports
from .storage import database_engine, save_snapshot, save_report, list_reports, replay_report
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

def main():
    parser=argparse.ArgumentParser(description='RiskLens offline dataset workflow')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('demo').add_argument('--output',type=Path,required=True)
    sub.add_parser('validate').add_argument('dataset',type=Path)
    report=sub.add_parser('report', help='Calculate validated one-day risk and export JSON/CSV')
    report.add_argument('dataset', type=Path)
    report.add_argument('--output', type=Path, required=True)
    report.add_argument('--confidence', type=float, default=0.95)
    report.add_argument('--trading-days', type=int, default=252)
    snapshot=sub.add_parser('snapshot', help='Save an immutable validated input capture')
    snapshot.add_argument('dataset', type=Path)
    saved=sub.add_parser('save-report', help='Save a report calculated from a persisted snapshot')
    saved.add_argument('snapshot_id')
    saved.add_argument('--confidence', type=float, default=.95)
    saved.add_argument('--trading-days', type=int, default=252)
    sub.add_parser('reports', help='List saved reports')
    replay=sub.add_parser('replay', help='Verify and recalculate from database inputs')
    replay.add_argument('report_id')
    correction=sub.add_parser('correct', help='Save an explicitly approved child snapshot')
    correction.add_argument('parent_id')
    correction.add_argument('dataset', type=Path)
    correction.add_argument('--approval', type=Path, required=True)
    review=sub.add_parser('review-correction', help='Export pending review, byte diffs, bindings and proposed inputs')
    review.add_argument('parent_id')
    review.add_argument('dataset',type=Path)
    review.add_argument('--output',type=Path,required=True)
    investigation=sub.add_parser('investigate',help='Diagnose captured valid or invalid inputs without calculating risk')
    investigation.add_argument('dataset',type=Path)
    investigation.add_argument('--output',type=Path,required=True)
    investigation.add_argument('--unchanged-sessions',type=int,default=5)
    investigation.add_argument('--large-return-threshold',type=float,default=.20)
    investigation.add_argument('--split-relative-tolerance',type=float,default=.03)
    comparison=sub.add_parser('compare',help='Compare standalone report JSON without approval/lineage claims')
    comparison.add_argument('original',type=Path)
    comparison.add_argument('corrected',type=Path)
    comparison.add_argument('--output',type=Path,required=True)
    saved_comparison=sub.add_parser('compare-saved',help='Verify and compare persisted original/corrected reports')
    saved_comparison.add_argument('original_id')
    saved_comparison.add_argument('corrected_id')
    saved_comparison.add_argument('--output',type=Path,required=True)
    bt=sub.add_parser('backtest',help='Rolling one-day historical VaR without lookahead')
    bt.add_argument('dataset',type=Path)
    bt.add_argument('--confidence',type=float,default=.95)
    bt.add_argument('--lookback',type=int,default=250)
    bt.add_argument('--output',type=Path,required=True)
    st=sub.add_parser('stress',help='Explicit instrument/sector hypothetical return shocks')
    st.add_argument('dataset',type=Path)
    st.add_argument('--scenario',type=Path,required=True)
    st.add_argument('--output',type=Path,required=True)
    win=sub.add_parser('window-replay',help='Validated daily-rebalanced observed window')
    win.add_argument('dataset',type=Path)
    win.add_argument('--start',required=True)
    win.add_argument('--end',required=True)
    win.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command in ('backtest','stress','window-replay'):
            captured=InputSnapshot.capture(args.dataset)
            if args.command=='backtest':
                result=backtest(captured,args.confidence,args.lookback)
            elif args.command=='stress':
                def unique_pairs(pairs):
                    obj={}
                    for key,value in pairs:
                        if key in obj: raise ValueError('duplicate JSON scenario key')
                        obj[key]=value
                    return obj
                scenario=json.loads(args.scenario.read_text(encoding='utf-8'),object_pairs_hook=unique_pairs)
                result=stress_scenario(captured,scenario)
            else:
                result=historical_window(captured,args.start,args.end)
            export_stage5(result,captured,args.output)
            print(json.dumps({k:result[k] for k in ('label','kind','input_hash')}))
        elif args.command=='demo':
            generate(args.output)
            print(json.dumps({'status':'generated','path':str(args.output),'mode':'synthetic'}))
        elif args.command=='report':
            result=calculate_report(args.dataset, args.confidence, args.trading_days)
            export_report(result, args.output)
            print(json.dumps({key: result[key] for key in ('label','observations','metrics')}))
        elif args.command=='investigate':
            captured=InputSnapshot.capture(args.dataset)
            result=investigate(captured,InvestigationPolicy(args.unchanged_sessions,args.large_return_threshold,args.split_relative_tolerance))
            export_investigation(result,captured,args.output)
            print(json.dumps({k:result[k] for k in ('label','input_hash','issue_count','calculation_blocked')}))
        elif args.command=='compare':
            result=compare_reports(json.loads(args.original.read_text()),json.loads(args.corrected.read_text()))
            export_analysis(result,args.output)
            print(json.dumps({k:result[k] for k in ('classification','compatibility_passed','correction_impact')}))
        elif args.command=='validate':
            d=load_dataset(args.dataset)
            print(json.dumps({'status':'valid','mode':d['provenance'].mode,'prices':len(d['prices']),'sessions':len(d['calendar']),'instruments':len(d['instruments'])}))
        else:
            engine=database_engine()
            try:
                with Session(engine) as session, session.begin():
                    if args.command=='snapshot':
                        result=save_snapshot(session, InputSnapshot.capture(args.dataset))
                        result={k: result[k] for k in ('id','input_hash','version')}
                    elif args.command=='review-correction':
                        result=prepare_review(session,args.parent_id,InputSnapshot.capture(args.dataset),args.output)
                    elif args.command=='correct':
                        approval=json.loads(args.approval.read_text(encoding='utf-8'))
                        result=save_snapshot(session, InputSnapshot.capture(args.dataset), args.parent_id, approval)
                        result={k: result[k] for k in ('id','parent_id','input_hash','version','approval')}
                    elif args.command=='save-report':
                        result=save_report(session, args.snapshot_id, args.confidence, args.trading_days)
                        result={k: result[k] for k in ('id','snapshot_id','kind','input_hash','result_hash')}
                    elif args.command=='compare-saved':
                        result=compare_saved_reports(session,args.original_id,args.corrected_id)
                        export_analysis(result,args.output)
                    elif args.command=='reports':
                        result=list_reports(session)
                    else:
                        result=replay_report(session, args.report_id)
                print(json.dumps(result))  # Only announce success after commit.
                if args.command=='replay' and not result['numerical_agreement']:
                    parser.exit(3,'Replay numerical mismatch\n')
            finally:
                engine.dispose()
    except SQLAlchemyError:
        parser.exit(2,'Database operation failed; transaction rolled back. Check PostgreSQL availability, migrations and permissions.\n')
    except (ValueError,OSError,ValidationError,DatasetError) as exc:
        parser.exit(2,f'Validation/generation failed: {exc}\n')

if __name__=='__main__':
    main()
