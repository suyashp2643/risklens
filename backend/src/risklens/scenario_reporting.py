"""Snapshot adapters and exports for Stage 5; never modifies saved reports."""
import csv
import json
from datetime import date
from pathlib import Path
from .calculations import ENGINE_VERSION, instrument_returns, portfolio_returns
from .snapshots import canonical, sha256
from .backtesting import rolling_var
from .stress import hypothetical, replay_window

ANALYSIS_VERSION='0.5.0'


def _inputs(snapshot):
    d=snapshot.validated_dataset()
    names=sorted(i.instrument for i in d['instruments'])
    sectors={i.instrument:i.sector for i in d['instruments']}
    weights={w.instrument:w.weight for w in d['portfolio'].weights}
    lookup={(p.date,p.instrument):p.adjusted_close for p in d['prices']}
    prices=[[lookup[(day,name)] for name in names] for day in d['calendar']]
    return d,names,sectors,[weights.get(n,0.0) for n in names],prices


def _envelope(snapshot,d,kind,configuration,result):
    body={'stage':5,'kind':kind,'engine_version':ENGINE_VERSION,'analysis_version':ANALYSIS_VERSION,
          'input_hash':snapshot.content_hash,'input_file_hashes':{k:v['sha256'] for k,v in snapshot.payload()['files'].items()},
          'mode':d['provenance'].mode,'label':'SYNTHETIC DEMONSTRATION — fictional prices and securities' if d['provenance'].mode=='synthetic' else 'IMPORTED HISTORICAL CSV — user-supplied source assertions',
          'portfolio_definition':d['portfolio'].model_dump(mode='json'),
          'limitations':['Synthetic timestamps do not reconstruct the historical information set.',
              'No costs, taxes, liquidity or FX simulation; supplied current weights are hypothetical historical holdings.',
              'Small evaluation samples and small expected exception counts weaken asymptotic coverage inference.',
              'A high Kupiec p-value is not proof of reliability or exception independence.'],
          'provenance':d['provenance'].model_dump(mode='json'),'configuration':configuration,**result}
    body['content_hash']=sha256(canonical(body))
    return body


def backtest(snapshot,confidence=.95,lookback=250):
    d,names,sectors,weights,prices=_inputs(snapshot)
    returns=portfolio_returns(instrument_returns(prices),weights)
    result=rolling_var(returns,[x.isoformat() for x in d['calendar'][1:]],confidence,lookback)
    return _envelope(snapshot,d,'rolling_var_backtest',{'confidence':confidence,'lookback':lookback,
        'horizon_days':1,'quantile_method':'empirical_inverse_cdf','minimum_observations':100,
        'minimum_tail_observations':5,'loss_sign':'loss = -simple_return; negative VaR retained',
        'exception_rule':'realized_loss > var; ties are not exceptions',
        'units':'decimal fractions; 0.01 = 1%','test_distribution':'asymptotic chi-square, 1 degree of freedom'},result)


def stress_scenario(snapshot,scenario):
    d,names,sectors,weights,prices=_inputs(snapshot)
    result=hypothetical(names,sectors,weights,d['portfolio'].valuation,scenario)
    return _envelope(snapshot,d,'hypothetical_stress',{'scenario':scenario,
        'overlap_rule':'instrument overrides sector; no addition','coverage':'all registered instruments, including zero weights',
        'valuation_basis':'starting portfolio valuation','shock_units':'decimal simple return',
        'pnl_sign':'positive gain, negative loss','horizon':'single simultaneous hypothetical shock'},result)


def historical_window(snapshot,start,end):
    d,names,sectors,weights,prices=_inputs(snapshot)
    start=date.fromisoformat(start); end=date.fromisoformat(end)
    calendar=d['calendar']
    if start not in calendar or end not in calendar or start>=end:
        raise ValueError('window needs two ordered exact calendar endpoints')
    a,b=calendar.index(start),calendar.index(end)
    result=replay_window(names,sectors,weights,d['portfolio'].valuation,
        instrument_returns(prices[a:b+1]),[x.isoformat() for x in calendar[a+1:b+1]])
    return _envelope(snapshot,d,'window_replay',{'start_price_date':start.isoformat(),'end_price_date':end.isoformat(),
        'scenario':{'type':'observed_window','start':start.isoformat(),'end':end.isoformat()},
        'rebalancing':'constant_weight_daily_rebalanced','valuation_basis':'starting portfolio valuation',
        'compounding':'product(1 + daily weighted simple return)',
        'contributions':'sum(start-of-day wealth * weight * instrument return); relative to starting valuation',
        'pnl_sign':'positive gain, negative loss'},result)


def export_stage5(result,snapshot,output:Path):
    if result['input_hash']!=snapshot.content_hash:
        raise ValueError('export snapshot mismatch')
    expected=sha256(canonical({k:v for k,v in result.items() if k!='content_hash'}))
    if result['content_hash']!=expected:
        raise ValueError('analysis content hash mismatch')
    output.mkdir(parents=True,exist_ok=False)
    (output/'report.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (output/'capture.json').write_bytes(canonical({'input_hash':snapshot.content_hash,'inputs':snapshot.payload()}))
    metadata={k:v for k,v in result.items() if k not in ('forecasts','daily','instruments','sectors')}
    (output/'metadata.json').write_text(json.dumps(metadata,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    for key in ('forecasts','daily','instruments','sectors'):
        if key not in result: continue
        rows=result[key]
        with (output/f'{key}.csv').open('w',newline='',encoding='utf-8') as f:
            fields=list(rows[0])+['mode','engine_version','analysis_version','input_hash']
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
            writer.writerows({**row,**{k:result[k] for k in fields if k not in row}} for row in rows)
    rows=([{'metric':k,'value':v,'unit':'decimal_fraction' if k=='exception_rate' else 'count'} for k,v in result['summary'].items() if k!='kupiec'] if 'summary' in result else
          [{'metric':k,'value':v,'unit':result['portfolio_definition']['base_currency'] if k=='currency_amount' else k} for k,v in result['portfolio'].items()])
    if 'summary' in result:
        rows.extend({'metric':k,'value':v,'unit':'probability' if k=='p_value' else 'statistic' if k=='likelihood_ratio' else 'count'} for k,v in result['summary']['kupiec'].items() if k!='method')
    with (output/'summary.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['metric','value','unit']);writer.writeheader();writer.writerows(rows)
