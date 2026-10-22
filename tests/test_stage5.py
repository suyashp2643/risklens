"""Hand references, leakage boundaries, strict adapters and export regressions."""
import copy
import json
import math
import shutil
import subprocess
import sys
from datetime import date,timedelta
from pathlib import Path
import numpy as np
import pytest
from risklens.backtesting import rolling_var,kupiec
from risklens.stress import hypothetical,replay_window
from risklens.scenario_reporting import backtest,stress_scenario,historical_window,export_stage5
from risklens.snapshots import InputSnapshot,canonical,sha256
from risklens.reporting import calculate_from_snapshot

DATA=Path(__file__).resolve().parents[1]/'sample-data/demo'

def dates(n):
    return [(date(2020,1,1)+timedelta(days=i)).isoformat() for i in range(n)]


def test_hand_var_no_lookahead_and_strict_ties():
    # 95 zeros and five losses .10: inverse CDF rank 95 gives zero.
    r=[0.0]*95+[-.1]*5+[0.0,-.2]
    got=rolling_var(r,dates(102),.95,100)
    assert got['forecasts'][0]['var']==0
    assert not got['forecasts'][0]['exception']
    assert got['forecasts'][1]['exception']
    assert got['forecasts'][0]['training_end']==dates(102)[99]
    assert got['summary']['exception_count']==1
    assert got['summary']['exception_rate']==.5
    assert got['summary']['expected_exception_count']==pytest.approx(.1)
    modified=r.copy();modified[100]=-1
    other=rolling_var(modified,dates(102),.95,100)
    assert other['forecasts'][0]['var']==got['forecasts'][0]['var']
    assert other['forecasts'][1]['var']!=got['forecasts'][1]['var']


def test_negative_var_retained():
    result=rolling_var([.01]*101,dates(101),lookback=100)
    assert result['forecasts'][0]['var']==-.01
    assert result['summary']['exception_count']==0


@pytest.mark.parametrize('x,n',[(0,100),(100,100),(5,100),(1,1)])
def test_kupiec_reference(x,n):
    result=kupiec(x,n,.95)
    null=x*math.log(.05)+(n-x)*math.log(.95)
    alt=0 if x in (0,n) else x*math.log(x/n)+(n-x)*math.log(1-x/n)
    assert result['likelihood_ratio']==pytest.approx(max(0,2*(alt-null)),abs=1e-12)
    assert 0<=result['p_value']<=1
    if x==5: assert result['p_value']==pytest.approx(1)


@pytest.mark.parametrize('confidence,lookback,n',[(.95,99,101),(.99,499,501),(.95,100,100),(1,100,101),(0,100,101),(.95,True,101)])
def test_backtest_sample_boundaries(confidence,lookback,n):
    with pytest.raises(ValueError): rolling_var([0]*n,dates(n),confidence,lookback)


def test_99_percent_boundary_and_bad_dates():
    assert len(rolling_var([0]*501,dates(501),.99,500)['forecasts'])==1
    with pytest.raises(ValueError): rolling_var([0]*101,['x']*101,lookback=100)
    with pytest.raises(ValueError): rolling_var([0]*100+[float('nan')],dates(101),lookback=100)


NAMES=['A','B'];SECTORS={'A':'X','B':'X'};WEIGHTS=[.6,.4]
def scenario():return {'name':'test','instrument_shocks':{'A':-.1},'sector_shocks':{'X':-.2}}


def test_hand_shock_overrides_sector_and_contributions():
    got=hypothetical(NAMES,SECTORS,WEIGHTS,1000,scenario())
    assert got['portfolio']['decimal_return']==pytest.approx(-.14)
    assert got['portfolio']['percent']==pytest.approx(-14)
    assert got['portfolio']['currency_amount']==pytest.approx(-140)
    assert got['instruments'][0]['currency_amount']==pytest.approx(-60)
    assert got['instruments'][0]['overridden_sector_shock']==-.2
    assert got['sectors'][0]['currency_amount']==pytest.approx(-140)


@pytest.mark.parametrize('shock',[-1.001,float('nan'),float('inf'),True,'-.1',None])
def test_reject_shocks(shock):
    sc=scenario();sc['instrument_shocks']['A']=shock
    with pytest.raises(ValueError): hypothetical(NAMES,SECTORS,WEIGHTS,1000,sc)


def test_coverage_unknown_extra_and_total_loss():
    for sc in [{'name':'x','instrument_shocks':{'A':0},'sector_shocks':{}},
               {'name':'x','instrument_shocks':{'C':0},'sector_shocks':{'X':0}},
               {**scenario(),'horizon':2}]:
        with pytest.raises(ValueError):hypothetical(NAMES,SECTORS,WEIGHTS,1000,sc)
    got=hypothetical(NAMES,SECTORS,WEIGHTS,1000,{'name':'wipeout','instrument_shocks':{},'sector_shocks':{'X':-1}})
    assert got['portfolio']['currency_amount']==-1000


def test_hand_compounding_and_pnl_contributions():
    got=replay_window(NAMES,SECTORS,[.5,.5],1000,[[.1,0],[0,-.2]],dates(2))
    assert got['portfolio']['decimal_return']==pytest.approx(-.055)
    assert got['instruments'][0]['currency_amount']==pytest.approx(50)
    assert got['instruments'][1]['currency_amount']==pytest.approx(-105)
    assert sum(x['currency_pnl'] for x in got['daily'])==pytest.approx(-55)
    assert sum(x['decimal_return'] for x in got['instruments'])==pytest.approx(-.055)


@pytest.mark.parametrize('fixture',['missing','duplicate'])
@pytest.mark.parametrize('kind',['bt','stress','window'])
def test_invalid_dataset_blocks_all(fixture,kind):
    snap=InputSnapshot.capture(DATA/fixture)
    with pytest.raises(ValueError):
        if kind=='bt':backtest(snap)
        elif kind=='stress':stress_scenario(snap,scenario())
        else:historical_window(snap,'2020-01-01','2020-02-01')


def test_adapter_regression_hash_determinism_and_exports(tmp_path):
    snap=InputSnapshot.capture(DATA/'clean')
    old=calculate_from_snapshot(snap)
    result=backtest(snap)
    assert result==backtest(snap)
    assert len(result['forecasts'])==505
    assert result['label'].startswith('SYNTHETIC')
    assert result['content_hash']==sha256(canonical({k:v for k,v in result.items() if k!='content_hash'}))
    export_stage5(result,snap,tmp_path/'out')
    payload=json.loads((tmp_path/'out/capture.json').read_text())
    restored=InputSnapshot.restore(payload['inputs'],payload['input_hash'])
    assert backtest(restored)==result
    assert (tmp_path/'out/forecasts.csv').exists()
    with pytest.raises(FileExistsError):export_stage5(result,snap,tmp_path/'out')
    assert calculate_from_snapshot(snap)==old
    assert old['engine_version']=='0.2.0'


def test_window_endpoints_and_clean_reference(tmp_path):
    snap=InputSnapshot.capture(DATA/'clean');d=snap.validated_dataset();cal=d['calendar']
    got=historical_window(snap,cal[0].isoformat(),cal[-1].isoformat())
    assert got['portfolio']['decimal_return']==pytest.approx(calculate_from_snapshot(snap)['metrics']['cumulative_return'])
    assert sum(x['decimal_return'] for x in got['instruments'])==pytest.approx(got['portfolio']['decimal_return'])
    for start,end in [(cal[0],cal[0]),(cal[-1],cal[0]),(date(1900,1,1),cal[-1])]:
        with pytest.raises(ValueError):historical_window(snap,start.isoformat(),end.isoformat())
    export_stage5(got,snap,tmp_path/'window');assert (tmp_path/'window/daily.csv').exists()


def test_cli_all_commands_and_overwrite(tmp_path):
    snap=InputSnapshot.capture(DATA/'clean');d=snap.validated_dataset()
    sc={'name':'SYNTHETIC sector decline','instrument_shocks':{},'sector_shocks':{i.sector:-.1 for i in d['instruments']}}
    path=tmp_path/'scenario.json';path.write_text(json.dumps(sc))
    commands=[['backtest','--lookback','250'],['stress','--scenario',str(path)],
              ['window-replay','--start',d['calendar'][0].isoformat(),'--end',d['calendar'][10].isoformat()]]
    for i,cmd in enumerate(commands):
        args=[sys.executable,'-m','risklens.cli',cmd[0],str(DATA/'clean'),*cmd[1:],'--output',str(tmp_path/f'out{i}')]
        first=subprocess.run(args,capture_output=True,text=True)
        assert first.returncode==0,first.stderr
        assert subprocess.run(args,capture_output=True).returncode==2
    path.write_text('{"name":"x","name":"y","instrument_shocks":{},"sector_shocks":{}}')
    assert subprocess.run([sys.executable,'-m','risklens.cli','stress',str(DATA/'clean'),'--scenario',str(path),'--output',str(tmp_path/'bad')],capture_output=True).returncode==2

@pytest.mark.parametrize('x,n,c',[(True,100,.95),(-1,100,.95),(101,100,.95),(0,0,.95),(0,100,float('nan'))])
def test_invalid_kupiec(x,n,c):
    with pytest.raises(ValueError):kupiec(x,n,c)


def test_zero_weight_coverage_and_bad_portfolio():
    with pytest.raises(ValueError):hypothetical(NAMES,{'A':'X','B':'Y'},[1,0],1000,{'name':'x','instrument_shocks':{'A':0},'sector_shocks':{}})
    with pytest.raises(ValueError):hypothetical(NAMES,SECTORS,[.5,.4],1000,scenario())
    with pytest.raises(ValueError):hypothetical(NAMES,SECTORS,WEIGHTS,float('inf'),scenario())
    with pytest.raises(ValueError):replay_window(NAMES,SECTORS,WEIGHTS,1000,[[0,0]],['not-a-date'])


def test_future_mutations_do_not_change_earlier_forecasts():
    r=[.01]*100+[0]*10
    before=rolling_var(r,dates(110),lookback=100)
    r[-1]=-1
    after=rolling_var(r,dates(110),lookback=100)
    assert before['forecasts'][:-1]==after['forecasts'][:-1]
    assert before['forecasts'][-1]['var']==after['forecasts'][-1]['var']


def test_capture_survives_source_removal_and_tamper_rejected(tmp_path):
    source=tmp_path/'source';shutil.copytree(DATA/'clean',source)
    snap=InputSnapshot.capture(source);expected=backtest(snap);shutil.rmtree(source)
    assert backtest(snap)==expected
    tampered=copy.deepcopy(expected);tampered['forecasts'][0]['var']=999
    with pytest.raises(ValueError):export_stage5(tampered,snap,tmp_path/'bad')
    assert not (tmp_path/'bad').exists()

@pytest.mark.parametrize('kind',['bt','stress','window'])
def test_nonpositive_captured_prices_block_all(kind):
    original=InputSnapshot.capture(DATA/'clean');files=dict(original.files)
    lines=files['prices.csv'].decode().splitlines();row=lines[1].split(',');row[-1]='0';lines[1]=','.join(row)
    files['prices.csv']=('\n'.join(lines)+'\n').encode();snap=InputSnapshot(files)
    with pytest.raises(ValueError):
        if kind=='bt':backtest(snap)
        elif kind=='stress':stress_scenario(snap,scenario())
        else:historical_window(snap,'2023-01-02','2023-01-16')
