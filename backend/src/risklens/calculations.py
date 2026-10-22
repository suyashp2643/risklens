"""Pure numerical functions: no files, database, API or implicit data cleaning."""
from dataclasses import dataclass
from math import ceil, sqrt
import numpy as np

ENGINE_VERSION = '0.2.0'


def _array(values, ndim=1, minimum=1):
    a = np.asarray(values, dtype=float)
    if a.ndim != ndim or a.shape[0] < minimum or not np.isfinite(a).all():
        raise ValueError('expected a nonempty finite array with the required dimensions')
    return a


def _returns(values):
    a = _array(values)
    if np.any(a < -1):
        raise ValueError('simple returns cannot be below -100%')
    return a


def instrument_returns(prices):
    """Rows are consecutive expected sessions, columns are instruments."""
    a = _array(prices, ndim=2, minimum=2)
    if a.shape[1] == 0 or np.any(a <= 0):
        raise ValueError('prices must have instruments and be strictly positive')
    with np.errstate(over='raise', invalid='raise', divide='raise'):
        try:
            result = a[1:] / a[:-1] - 1
        except FloatingPointError as exc:
            raise ValueError('price ratios exceed numerical range') from exc
    if not np.isfinite(result).all():
        raise ValueError('nonfinite calculated returns')
    return result


def portfolio_returns(returns, weights):
    a = _array(returns, ndim=2)
    w = _array(weights)
    if a.shape[1] != len(w) or np.any(a < -1):
        raise ValueError('return columns must match weights; returns must be >= -1')
    if np.any(w < 0) or np.any(w > 1) or abs(float(w.sum()) - 1) > 1e-10:
        raise ValueError('long-only weights must sum to one within 1e-10')
    result = a @ w
    if not np.isfinite(result).all():
        raise ValueError('nonfinite portfolio returns')
    return result


def wealth_index(returns):
    r = _returns(returns)
    with np.errstate(over='raise', invalid='raise'):
        try:
            wealth = np.concatenate(([1.0], np.cumprod(1 + r)))
        except FloatingPointError as exc:
            raise ValueError('wealth exceeds numerical range') from exc
    return wealth


def cumulative_return(returns):
    return float(wealth_index(returns)[-1] - 1)


def annualized_sample_volatility(returns, trading_days=252):
    r = _returns(returns)
    if len(r) < 2:
        raise ValueError('sample volatility needs at least two returns')
    if isinstance(trading_days, bool) or not isinstance(trading_days, int) or trading_days <= 0:
        raise ValueError('trading_days must be a positive integer')
    result = float(np.std(r, ddof=1) * sqrt(trading_days))
    if not np.isfinite(result):
        raise ValueError('volatility exceeds numerical range')
    return result


def maximum_drawdown(returns):
    """Positive magnitude; initial wealth 1 participates in the running peak."""
    wealth = wealth_index(returns)
    return float(np.max(1 - wealth / np.maximum.accumulate(wealth)))


@dataclass(frozen=True)
class TailPolicy:
    confidence: float = 0.95
    min_observations: int = 100
    min_tail_observations: int = 5

    def __post_init__(self):
        if isinstance(self.confidence, bool) or not np.isfinite(self.confidence) or not 0 < self.confidence < 1:
            raise ValueError('confidence must be strictly between zero and one')
        for value in (self.min_observations, self.min_tail_observations):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError('sample requirements must be positive integers')

    def required_observations(self):
        # Subtract only roundoff at integer boundaries, e.g. 5/(1-.99).
        target = self.min_tail_observations / (1 - self.confidence)
        return max(self.min_observations, ceil(target - 1e-12 * max(1, target)))


def historical_var_es(returns, policy=None):
    """Loss=-return. Inverse empirical CDF VaR; integrated empirical tail ES."""
    policy = policy or TailPolicy()
    r = _returns(returns)
    n = len(r)
    if n < policy.required_observations():
        raise ValueError(f'insufficient data: {n} returns; require {policy.required_observations()}')
    losses = np.sort(-r)
    rank = ceil(n * policy.confidence)
    var = float(losses[rank - 1])
    mass = n * (1 - policy.confidence)
    nearest = round(mass)
    if abs(mass - nearest) <= 1e-12 * max(1, mass):
        mass = float(nearest)
    full = int(np.floor(mass))
    fraction = mass - full
    worst = losses[::-1]
    total = float(worst[:full].sum())
    if fraction > 0:
        total += fraction * float(worst[full])
    es = total / mass
    if not np.isfinite(es):
        raise ValueError('expected shortfall exceeds numerical range')
    return {'var': var, 'expected_shortfall': es, 'tail_mass': mass,
            'observations': n, 'confidence': policy.confidence,
            'required_observations': policy.required_observations()}
