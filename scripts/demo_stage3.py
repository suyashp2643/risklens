"""Two-phase synthetic demo: inspect pending review, then supply explicit approval."""
import argparse
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from sqlalchemy.orm import Session
from risklens.snapshots import InputSnapshot
from risklens.storage import database_engine,save_snapshot,save_report,replay_report,ReportRow
from risklens.reviews import prepare_review


def main():
    parser=argparse.ArgumentParser()
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--prepare',type=Path,metavar='NEW_REVIEW_DIRECTORY')
    modes.add_argument('--apply',type=Path,metavar='REVIEW_DIRECTORY')
    parser.add_argument('--approval',type=Path)
    args=parser.parse_args()
    if args.apply and not args.approval:
        parser.error('--apply requires --approval; no automatic approval')
    if args.prepare and args.approval:
        parser.error('--approval is used only with --apply')
    root=Path(__file__).resolve().parents[1]
    engine=database_engine()
    try:
        if args.prepare:
            with TemporaryDirectory(prefix='risklens-stage3-demo-') as folder:
                source=Path(folder)/'source';shutil.copytree(root/'sample-data/demo/split',source)
                with Session(engine) as s,s.begin():
                    parent=save_snapshot(s,InputSnapshot.capture(source))
                    original=save_report(s,parent['id'])
                shutil.rmtree(source)
                with Session(engine) as s,s.begin():
                    replay=replay_report(s,original['id'])
                    if not replay['numerical_agreement']:
                        raise RuntimeError('Original replay mismatch')
                    review=prepare_review(s,parent['id'],InputSnapshot.capture(root/'sample-data/demo/clean'),args.prepare)
                state={'parent_id':parent['id'],'original_report_id':original['id'],
                    'source_removed':not source.exists(),'original_replay':replay}
                (args.prepare/'demo-state.json').write_text(json.dumps(state,indent=2)+'\n',encoding='utf-8')
                print(json.dumps({'state':state,'review':review,'approval':'pending'},indent=2))
        else:
            state=json.loads((args.apply/'demo-state.json').read_text(encoding='utf-8'))
            approval=json.loads(args.approval.read_text(encoding='utf-8'))
            proposed=InputSnapshot.capture(args.apply/'proposed-inputs')
            with Session(engine) as s,s.begin():
                original_replay=replay_report(s,state['original_report_id'])
                original=json.loads(s.get(ReportRow,state['original_report_id']).body)
                child=save_snapshot(s,proposed,state['parent_id'],approval)
                corrected=save_report(s,child['id'])
                corrected_replay=replay_report(s,corrected['id'])
                if not original_replay['numerical_agreement'] or not corrected_replay['numerical_agreement']:
                    raise RuntimeError('Replay mismatch')
            old=original['result']['metrics'];new=corrected['result']['metrics']
            print(json.dumps({'mode':'synthetic','source_removed':state['source_removed'],
                'original_replay':original_replay,
                'metric_changes':{k:{'original':old[k],'corrected':new[k],'delta':new[k]-old[k]} for k in old},
                'correction':{k:child[k] for k in ('id','parent_id','version','approval')},
                'corrected_report_id':corrected['id'],'corrected_replay':corrected_replay},indent=2))
    finally:
        engine.dispose()


if __name__=='__main__':
    main()
