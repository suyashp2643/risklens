"""Reproducible synthetic prices; fixed seed, no network, no actual securities."""
import csv
import hashlib
import json
import shutil
from datetime import date, timedelta
from pathlib import Path
import numpy as np

SEED = 1729

def write_csv(path, columns, rows):
    with path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator='\n')
        w.writeheader()
        w.writerows(rows)

def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True)+'\n')

def generate(root: Path):
    if root.exists():
        raise FileExistsError(f'{root} exists; choose a new output directory')
    root.mkdir(parents=True)
    clean = root/'clean'
    clean.mkdir()
    dates=[]
    day=date(2023,1,2)
    while len(dates)<756:
        if day.weekday()<5:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    names=['SYN_TECH','SYN_BANK','SYN_HEALTH','SYN_ENERGY']
    sectors=['Technology','Financials','Healthcare','Energy']
    rng=np.random.default_rng(SEED)
    common=rng.normal(0.0002,0.008,len(dates)-1)
    prices=[]
    for j,name in enumerate(names):
        returns=common*(0.7+j*0.1)+rng.normal(0.0001,0.006+j*0.001,len(common))
        levels=np.concatenate(([100.0+20*j],(100.0+20*j)*np.cumprod(1+returns)))
        prices += [dict(date=d,instrument=name,adjusted_close=f'{v:.8f}') for d,v in zip(dates,levels)]
    prices.sort(key=lambda r:(r['date'],r['instrument']))
    columns=['date','instrument','adjusted_close']
    write_csv(clean/'prices.csv',columns,prices)
    write_csv(clean/'calendar.csv',['date'],[dict(date=d) for d in dates])
    write_csv(clean/'instruments.csv',['instrument','sector','currency'],[dict(instrument=n,sector=s,currency='USD') for n,s in zip(names,sectors)])
    write_csv(clean/'corporate_actions.csv',['date','instrument','action','value','evidence'],[dict(date=dates[400],instrument=names[0],action='split',value=2,evidence='Synthetic fixture ledger: illustrative 2-for-1 split; clean series is already adjusted.')])
    write_json(clean/'portfolio.json',dict(base_currency='USD',valuation=100000,weights_as_of=dates[-1],convention='constant_weight_daily_rebalanced',weights=[dict(instrument=n,weight=w) for n,w in zip(names,[0.4,0.3,0.2,0.1])]))
    write_json(clean/'provenance.json',dict(mode='synthetic',source=f'RiskLens deterministic generator v0.1.0; NumPy PCG64 seed {SEED}',acquired_at='2026-01-01T00:00:00Z',currency='USD',start_date=dates[0],end_date=dates[-1],adjustment_basis='Synthetic split/dividend-adjusted levels; no real corporate actions or market prices.',calendar_assumption='756 synthetic weekday sessions; real exchange holidays deliberately not modeled.',observation_timestamps_available=False))
    for kind in ['split','stale','missing','duplicate']:
        dest=root/kind
        shutil.copytree(clean,dest)
        rows=[dict(r) for r in prices]
        if kind=='split':
            for r in rows:
                if r['instrument']==names[0] and r['date']>=dates[400]:
                    r['adjusted_close']=f"{float(r['adjusted_close'])/2:.8f}"
        elif kind=='stale':
            anchor=next(r['adjusted_close'] for r in rows if r['instrument']==names[1] and r['date']==dates[299])
            for r in rows:
                if r['instrument']==names[1] and r['date'] in dates[300:308]:
                    r['adjusted_close']=anchor
        elif kind=='missing':
            rows=[r for r in rows if not(r['instrument']==names[2] and r['date']==dates[350])]
        else:
            rows.append(dict(next(r for r in rows if r['instrument']==names[3] and r['date']==dates[450])))
        write_csv(dest/'prices.csv',columns,rows)
        write_json(dest/'fixture.json',dict(kind=kind,parent='clean',instrument=names[['split','stale','missing','duplicate'].index(kind)],affected_dates={'split':dates[400:],'stale':dates[300:308],'missing':[dates[350]],'duplicate':[dates[450]]}[kind],intent='Intentionally corrupted synthetic data; not an approved correction.'))
    hashes={str(f.relative_to(root)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(root.rglob('*')) if f.is_file()}
    write_json(root/'manifest.json',dict(generator_version='0.1.0',seed=SEED,files=hashes,notes='Hashes identify fixture bytes, not yet persisted report snapshots. Fixed acquired_at is a synthetic placeholder, not a genuine historical ingestion timestamp.'))
