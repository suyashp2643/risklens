"""Validated dataset adapter and explicit JSON/CSV exports, separate from math."""
import csv
import json
from pathlib import Path
import numpy as np
from .calculations import (ENGINE_VERSION, TailPolicy, instrument_returns,
    portfolio_returns, wealth_index, cumulative_return,
    annualized_sample_volatility, maximum_drawdown, historical_var_es)
from .snapshots import InputSnapshot


def calculate_report(dataset: Path, confidence=0.95, trading_days=252):
    return calculate_from_snapshot(InputSnapshot.capture(dataset), confidence, trading_days)


def calculate_from_snapshot(snapshot: InputSnapshot, confidence=0.95, trading_days=252):
    d = snapshot.validated_dataset()  # Same captured bytes are validated and calculated.
    policy = TailPolicy(confidence=confidence)
    names = sorted(i.instrument for i in d['instruments'])
    dates = d['calendar']
    lookup = {(p.date, p.instrument): p.adjusted_close for p in d['prices']}
    prices = [[lookup[(date, name)] for name in names] for date in dates]
    weight_map = {w.instrument: w.weight for w in d['portfolio'].weights}
    weights = [weight_map.get(name, 0.0) for name in names]
    individual = instrument_returns(prices)
    returns = portfolio_returns(individual, weights)
    wealth = wealth_index(returns)
    drawdowns = 1 - wealth / np.maximum.accumulate(wealth)
    tail = historical_var_es(returns, policy)
    valuation = d['portfolio'].valuation
    metrics = {
        'cumulative_return': cumulative_return(returns),
        'annualized_sample_volatility': annualized_sample_volatility(returns, trading_days),
        'maximum_drawdown': maximum_drawdown(returns),
        'historical_one_day_var': tail['var'],
        'historical_one_day_expected_shortfall': tail['expected_shortfall'],
        'historical_one_day_var_currency': tail['var'] * valuation,
        'historical_one_day_expected_shortfall_currency': tail['expected_shortfall'] * valuation,
    }
    if not all(np.isfinite(v) for v in metrics.values()):
        raise ValueError('report metrics exceed numerical range')
    rows = []
    for idx, date in enumerate(dates):
        row = {'date': date.isoformat(), 'mode': d['provenance'].mode,
               'currency': d['portfolio'].base_currency,
               'portfolio_return': None if idx == 0 else float(returns[idx-1]),
               'wealth_index': float(wealth[idx]), 'cumulative_return': float(wealth[idx]-1),
               'drawdown': float(drawdowns[idx])}
        for j, name in enumerate(names):
            row[f'return_{name}'] = None if idx == 0 else float(individual[idx-1, j])
        rows.append(row)
    return {
        'engine_version': ENGINE_VERSION, 'stage': 2,
        'mode': d['provenance'].mode,
        'label': 'SYNTHETIC DEMONSTRATION — fictional prices and securities' if d['provenance'].mode == 'synthetic' else 'IMPORTED HISTORICAL CSV — source assertions supplied by user',
        'provenance': d['provenance'].model_dump(mode='json'),
        'portfolio': d['portfolio'].model_dump(mode='json'),
        'configuration': {'confidence': confidence, 'trading_days_per_year': trading_days,
            'horizon_days': 1, 'quantile_method': 'empirical_inverse_cdf',
            'es_method': 'fractional_worst_tail_mass',
            'minimum_observations': policy.min_observations,
            'minimum_tail_observations': policy.min_tail_observations,
            'required_observations': tail['required_observations'],
            'loss_sign': 'loss = -simple_return; negative VaR retained',
            'fraction_units': 'decimal; 0.01 means 1%',
            'currency_loss_basis': 'portfolio.valuation; not terminal simulated wealth',
            'historical_convention': 'constant_weight_daily_rebalanced; current weights are a hypothetical historical strategy, not historical holdings',
            'drawdown_sign': 'positive peak-to-trough loss magnitude, initial wealth included'},
        'observations': len(returns), 'tail_mass': tail['tail_mass'],
        'metrics': metrics, 'daily': rows,
        'limitations': ['No transaction costs, taxes, slippage or intraday losses.',
            'Valid suspicious prices are retained; investigation engine is unfinished.',
            'This export is not an immutable snapshot or verified replay.'],
    }


def export_report(report, output: Path):
    # A directory keeps the related files together; refuse any existing destination.
    output.mkdir(parents=True, exist_ok=False)
    (output/'report.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    with (output/'daily.csv').open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(report['daily'][0]))
        writer.writeheader()
        writer.writerows(report['daily'])
    with (output/'metrics.csv').open('w', newline='', encoding='utf-8') as f:
        fields = ['mode','currency','confidence','observations','metric','value','unit','horizon_days','engine_version']
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for metric, value in report['metrics'].items():
            writer.writerow({'mode': report['mode'], 'currency': report['portfolio']['base_currency'],
                'confidence': report['configuration']['confidence'], 'observations': report['observations'],
                'metric': metric, 'value': value, 'unit': report['portfolio']['base_currency'] if metric.endswith('_currency') else 'decimal_fraction',
                'horizon_days': 1 if 'one_day' in metric else '', 'engine_version': report['engine_version']})
    # Conventions and provenance accompany CSV imports instead of being inferred.
    metadata = {key: value for key, value in report.items() if key not in ('daily','metrics')}
    (output/'metadata.json').write_text(json.dumps(metadata, indent=2, allow_nan=False)+'\n', encoding='utf-8')
