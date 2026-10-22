"""Create an inspectable pending review; never grant approval."""
import difflib
import json
import uuid
from pathlib import Path
from .snapshots import INPUT_FILES, InputSnapshot, sha256
from .storage import read_snapshot


def prepare_review(session,parent_id,proposed: InputSnapshot,output: Path):
    parent,body=read_snapshot(session,parent_id)
    proposed.validated_dataset()
    if parent.content_hash==proposed.content_hash:
        raise ValueError('proposed correction must change inputs')
    changes=[]
    diffs=[]
    for name in INPUT_FILES:
        old,new=parent.files[name],proposed.files[name]
        if old!=new:
            changes.append({'file':name,'parent_sha256':sha256(old),
                'proposed_sha256':sha256(new),'parent_size':len(old),'proposed_size':len(new)})
            diffs.extend(difflib.unified_diff(old.decode('utf-8').splitlines(True),
                new.decode('utf-8').splitlines(True),fromfile='parent/'+name,tofile='proposed/'+name))
    bindings={'parent_snapshot_id':parent_id,'parent_input_hash':parent.content_hash,
              'proposed_input_hash':proposed.content_hash}
    approval={**bindings,'record_id':str(uuid.uuid4()),'approved_by':'REPLACE_WITH_REVIEWER',
        'approved_at':'REPLACE_WITH_ACTUAL_TIMEZONE_AWARE_APPROVAL_TIME','decision':'pending',
        'reason':'REPLACE_WITH_REVIEWED_REASON','evidence':'REPLACE_WITH_REVIEWED_EVIDENCE'}
    output.mkdir(parents=True,exist_ok=False)
    inputs=output/'proposed-inputs';inputs.mkdir()
    for name,value in proposed.files.items():
        (inputs/name).write_bytes(value)
    review={**bindings,'parent_version':body['version'],'changed_files':changes,
            'mode':proposed.validated_dataset()['provenance'].mode,
            'status':'pending; no correction saved or approved'}
    (output/'review.json').write_text(json.dumps(review,indent=2)+'\n',encoding='utf-8')
    (output/'changes.diff').write_text(''.join(diffs),encoding='utf-8')
    (output/'approval.json').write_text(json.dumps(approval,indent=2)+'\n',encoding='utf-8')
    return review
