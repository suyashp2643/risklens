"""Pure rolling historical VaR and unconditional coverage calculations."""
from math import log, log1p, erfc, sqrt
from datetime import date
import numpy as np
from .calculations import _returns, TailPolicy, historical_var_es


def kupiec(exceptions, observations, confidence):
    TailPolicy(confidence)
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (exceptions, observations)) or observations < 1 or not 0 <= exceptions <= observations:
        raise ValueError('invalid exception count or sample size')
    n, x = observations, exceptions
    p = 1 - confidence
    null = x * log(p) + (n-x) * log1p(-p)
    # 0 log 0 is zero; endpoints have maximized log likelihood zero.
    alternative = 0.0 if x in (0, n) else x * log(x/n) + (n-x) * log1p(-x/n)
    statistic = max(0.0, 2 * (alternative-null))
    return {'likelihood_ratio': statistic, 'p_value': erfc(sqrt(statistic/2)),
            'degrees_of_freedom': 1, 'method': 'Kupiec unconditional coverage; asymptotic chi-square'}


def rolling_var(returns, dates, confidence=.95, lookback=250):
    r = _returns(returns)
    try:
        if any(not isinstance(d,str) or date.fromisoformat(d).isoformat()!=d for d in dates):
            raise ValueError('dates must be canonical ISO dates')
    except (TypeError, ValueError) as exc:
        raise ValueError('dates must be canonical ISO dates') from exc
    policy = TailPolicy(confidence)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < policy.required_observations():
        raise ValueError(f'lookback must be an integer >= {policy.required_observations()}')
    if len(dates) != len(r) or any(a >= b for a,b in zip(dates, dates[1:])):
        raise ValueError('return dates must be unique and increasing, one per return')
    if len(r) <= lookback:
        raise ValueError('need at least one out-of-sample forecast after the lookback')
    rows = []
    for i in range(lookback, len(r)):
        var = historical_var_es(r[i-lookback:i], policy)['var']
        loss = float(-r[i])
        rows.append({'forecast_date': dates[i], 'training_start': dates[i-lookback],
                     'training_end': dates[i-1], 'training_observations': lookback,
                     'var': var, 'realized_loss': loss, 'exception': loss > var})
    x = sum(row['exception'] for row in rows)
    n = len(rows)
    return {'forecasts': rows, 'summary': {'observations': n, 'exception_count': x,
            'exception_rate': x/n, 'expected_exception_count': n*(1-confidence),
            'kupiec': kupiec(x, n, confidence)}}
