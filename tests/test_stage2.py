"""Independent numerical references and complete offline CLI workflows."""
import csv
import json
import math
import subprocess
import sys
from pathlib import Path
import numpy as np
import pytest
from risklens.calculations import (TailPolicy, instrument_returns, portfolio_returns,
    cumulative_return, annualized_sample_volatility, maximum_drawdown,
    historical_var_es, wealth_index)
from risklens.reporting import calculate_report, export_report
from risklens.demo import generate

ROOT = Path(__file__).resolve().parents[1]
SMALL = TailPolicy(confidence=.75, min_observations=1, min_tail_observations=1)


def test_hand_returns_and_daily_rebalancing():
    # First asset doubles then halves; second is flat. Rebalance 50/50 daily.
    r = instrument_returns([[100, 50], [200, 50], [100, 50]])
    np.testing.assert_allclose(r, [[1, 0], [-.5, 0]])
    p = portfolio_returns(r, [.5, .5])
    np.testing.assert_allclose(p, [.5, -.25])
    assert cumulative_return(p) == pytest.approx(.125)
    # Buy-and-hold would end at its initial value, not +12.5%.


def test_hand_sample_volatility():
    # Mean .02; squared deviations total .0002; sample variance .0001.
    assert annualized_sample_volatility([.01, .02, .03], 4) == pytest.approx(.02)
    assert annualized_sample_volatility([0, 0]) == 0


@pytest.mark.parametrize('returns,expected', [([-.1, -.1], .19), ([.2, -.25, .1], .25), ([.1,.2],0), ([-1,0],1)])
def test_drawdown_includes_initial_value(returns, expected):
    assert maximum_drawdown(returns) == pytest.approx(expected)


def test_hand_var_es_integer_tail():
    # Ascending losses: -2%, 0%, 1%, 4%; inverse CDF q75 = 1%; worst 25% = 4%.
    result = historical_var_es([.02, 0, -.01, -.04], SMALL)
    assert result['var'] == pytest.approx(.01)
    assert result['expected_shortfall'] == pytest.approx(.04)


def test_hand_fractional_tail():
    # Losses 0,1,2,3,4%; c=.70 -> tail mass=1.5, ES=(4+.5*3)/1.5 percent.
    policy = TailPolicy(.7, 1, 1)
    result = historical_var_es([0, -.01, -.02, -.03, -.04], policy)
    assert result['var'] == pytest.approx(.03)
    assert result['expected_shortfall'] == pytest.approx(.03666666666666667)


def test_ties_do_not_expand_tail():
    # c=.6, mass=2; largest values 5%,2%. Threshold 2% has three ties.
    result = historical_var_es([-.02,-.05,0,-.02,-.02], TailPolicy(.6,1,1))
    assert result['var'] == pytest.approx(.02)
    assert result['expected_shortfall'] == pytest.approx(.035)
    permuted = historical_var_es([-.02,0,-.02,-.05,-.02], TailPolicy(.6,1,1))
    assert result == permuted


def test_negative_var_retained():
    result = historical_var_es([.01,.02,.03,.04], SMALL)
    assert result['var'] == pytest.approx(-.02)
    assert result['expected_shortfall'] == pytest.approx(-.01)


@pytest.mark.parametrize('confidence,required', [(.95,100),(.99,500),(.975,200)])
def test_default_sample_boundary(confidence, required):
    policy = TailPolicy(confidence)
    assert policy.required_observations() == required
    with pytest.raises(ValueError, match='insufficient'):
        historical_var_es(np.zeros(required-1), policy)
    assert historical_var_es(np.zeros(required), policy)['var'] == 0


@pytest.mark.parametrize('confidence', [0,1,-.1,1.1,float('nan'),float('inf'),True])
def test_invalid_confidence(confidence):
    with pytest.raises(ValueError):
        TailPolicy(confidence)


@pytest.mark.parametrize('values', [[],[float('nan')],[float('inf')],[-1.01],[[.1,.2]]])
def test_invalid_return_vectors(values):
    for fn in (cumulative_return, maximum_drawdown, annualized_sample_volatility, historical_var_es):
        with pytest.raises(ValueError):
            fn(values)


@pytest.mark.parametrize('prices', [[[1],[0]],[[1],[-1]],[[1],[float('nan')]],[[1],[float('inf')]],[[1]], [[],[]]])
def test_invalid_prices(prices):
    with pytest.raises(ValueError):
        instrument_returns(prices)


@pytest.mark.parametrize('weights', [[.2,.2],[-.1,1.1],[1], [float('nan'),0],[]])
def test_invalid_weights(weights):
    with pytest.raises(ValueError):
        portfolio_returns([[.1,.2]], weights)


@pytest.mark.parametrize('days',[0,-1,2.5,True])
def test_invalid_annualization(days):
    with pytest.raises(ValueError):
        annualized_sample_volatility([0,.1], days)


def test_one_return_and_total_loss():
    assert cumulative_return([-.1]) == pytest.approx(-.1)
    with pytest.raises(ValueError):
        annualized_sample_volatility([.1])
    np.testing.assert_allclose(wealth_index([-1,.5]), [1,0,0])
    assert cumulative_return([-1,.5]) == -1


def test_overflow_rejected():
    with pytest.raises(ValueError):
        instrument_returns([[1e-300],[1e300]])
    with pytest.raises(ValueError):
        cumulative_return([1e300,1e300])


def test_synthetic_report_independent_reference():
    dataset = ROOT/'sample-data/demo/clean'
    report = calculate_report(dataset)
    with (dataset/'prices.csv').open() as f:
        raw = list(csv.DictReader(f))
    portfolio = json.loads((dataset/'portfolio.json').read_text())
    weights = {w['instrument']: w['weight'] for w in portfolio['weights']}
    dates = sorted({r['date'] for r in raw})
    prices = {(r['date'],r['instrument']): float(r['adjusted_close']) for r in raw}
    # Reference uses Python scalar loops, statistics and independently sorted losses.
    r = [sum(w*(prices[(dates[t],name)]/prices[(dates[t-1],name)]-1)
             for name,w in weights.items()) for t in range(1,len(dates))]
    import statistics
    assert report['observations'] == 755
    assert report['mode'] == 'synthetic'
    assert report['metrics']['cumulative_return'] == pytest.approx(math.prod(1+x for x in r)-1)
    assert report['metrics']['annualized_sample_volatility'] == pytest.approx(statistics.stdev(r)*math.sqrt(252))
    losses = sorted(-x for x in r)
    assert report['metrics']['historical_one_day_var'] == pytest.approx(losses[717])
    # 755*.05 = 37.75: 37 full worst losses plus .75 of the next.
    es = (sum(losses[-37:])+.75*losses[-38])/37.75
    assert report['metrics']['historical_one_day_expected_shortfall'] == pytest.approx(es)
    assert report['daily'][0]['portfolio_return'] is None
    assert report['daily'][0]['wealth_index'] == 1
    assert report['metrics']['historical_one_day_var_currency'] == pytest.approx(losses[717]*portfolio['valuation'])


@pytest.mark.parametrize('fixture',['missing','duplicate'])
def test_invalid_existing_fixtures_block_report(fixture):
    with pytest.raises(ValueError):
        calculate_report(ROOT/'sample-data/demo'/fixture)


def test_suspicious_fixtures_retained():
    clean = calculate_report(ROOT/'sample-data/demo/clean')
    for fixture in ('split','stale'):
        changed = calculate_report(ROOT/'sample-data/demo'/fixture)
        assert changed['observations'] == clean['observations']
        assert changed['metrics'] != clean['metrics']


def test_export_round_trip_and_overwrite_protection(tmp_path):
    report = calculate_report(ROOT/'sample-data/demo/clean')
    out = tmp_path/'report'
    export_report(report,out)
    assert json.loads((out/'report.json').read_text()) == report
    with (out/'metrics.csv').open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 7
    assert all(row['mode']=='synthetic' for row in rows)
    for row in rows:
        assert float(row['value']) == report['metrics'][row['metric']]
    with (out/'daily.csv').open() as f:
        daily = list(csv.DictReader(f))
    assert len(daily)==756
    assert daily[0]['portfolio_return']==''
    assert float(daily[-1]['wealth_index']) == report['daily'][-1]['wealth_index']
    with pytest.raises(FileExistsError):
        export_report(report,out)


def test_complete_cli_workflow(tmp_path):
    dataset=tmp_path/'demo'
    generate(dataset)
    out=tmp_path/'result'
    run=subprocess.run([sys.executable,'-m','risklens.cli','report',str(dataset/'clean'),
        '--output',str(out),'--confidence','.99','--trading-days','260'],capture_output=True,text=True)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)['observations']==755
    report=json.loads((out/'report.json').read_text())
    assert report['configuration']['required_observations']==500
    assert report['configuration']['trading_days_per_year']==260
    for fixture in ('missing','duplicate'):
        bad=tmp_path/fixture
        run=subprocess.run([sys.executable,'-m','risklens.cli','report',str(dataset/fixture),
            '--output',str(bad)],capture_output=True,text=True)
        assert run.returncode==2
        assert not bad.exists()
    run=subprocess.run([sys.executable,'-m','risklens.cli','report',str(dataset/'clean'),
        '--output',str(tmp_path/'too-small'),'--confidence','.999'],capture_output=True,text=True)
    assert run.returncode==2
    assert 'insufficient' in run.stderr
    assert not (tmp_path/'too-small').exists()


def test_equal_losses_and_confidence_rank_boundary():
    equal = historical_var_es([-.02]*4, SMALL)
    assert equal['var'] == pytest.approx(.02)
    assert equal['expected_shortfall'] == pytest.approx(.02)
    losses_returns = [0, -.01, -.02, -.03]
    # At c=.5 the second ascending loss is selected; just above, the third.
    at = historical_var_es(losses_returns, TailPolicy(.5,1,1))
    above = historical_var_es(losses_returns, TailPolicy(.50001,1,1))
    assert at['var'] == pytest.approx(.01)
    assert above['var'] == pytest.approx(.02)
    assert at['expected_shortfall'] == pytest.approx(.025)


@pytest.mark.parametrize('kwargs', [{'min_observations':0},{'min_tail_observations':0},
    {'min_observations':True},{'min_tail_observations':1.5}])
def test_invalid_sample_policies(kwargs):
    with pytest.raises(ValueError):
        TailPolicy(**kwargs)
