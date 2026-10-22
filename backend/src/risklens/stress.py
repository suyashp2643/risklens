"""Pure hypothetical shock and daily-rebalanced window arithmetic."""
import math
from collections.abc import Mapping
from datetime import date
import numpy as np
from .calculations import portfolio_returns, wealth_index


def _amounts(value, valuation):
    amount = value * valuation
    percent = value * 100
    if not all(math.isfinite(x) for x in (value, amount, percent)):
        raise ValueError('stress results exceed numerical range')
    return {'decimal_return': float(value), 'percent': float(percent), 'currency_amount': float(amount)}


def _portfolio(names, sectors, weights, valuation):
    if len(names) != len(set(names)) or not names or set(sectors) != set(names) or any(not isinstance(v,str) or not v for v in sectors.values()):
        raise ValueError('invalid instrument/sector definitions')
    portfolio_returns(np.zeros((1,len(names))), weights)
    if isinstance(valuation,bool) or not isinstance(valuation,(float,int)) or not math.isfinite(valuation) or valuation <= 0:
        raise ValueError('valuation must be positive finite')


def hypothetical(names, sectors, weights, valuation, scenario):
    _portfolio(names, sectors, weights, valuation)
    if not isinstance(scenario, Mapping) or set(scenario) != {'name','instrument_shocks','sector_shocks'} or not isinstance(scenario['name'],str) or not scenario['name'].strip():
        raise ValueError('scenario needs name, instrument_shocks and sector_shocks only')
    individual, grouped = scenario['instrument_shocks'], scenario['sector_shocks']
    if not isinstance(individual,dict) or not isinstance(grouped,dict):
        raise ValueError('shocks must be mappings')
    if set(individual)-set(names) or set(grouped)-set(sectors.values()):
        raise ValueError('unknown instrument or sector shock')
    for shock in list(individual.values())+list(grouped.values()):
        if isinstance(shock,bool) or not isinstance(shock,(float,int)) or not math.isfinite(shock) or shock < -1:
            raise ValueError('shocks must be finite decimal simple returns >= -1')
    rows=[]
    for name, weight in zip(names,weights):
        sector=sectors[name]
        if name not in individual and sector not in grouped:
            raise ValueError(f'explicit shock coverage required for {name}, including zero weights')
        shock=individual[name] if name in individual else grouped[sector]
        rows.append({'instrument':name,'sector':sector,'weight':float(weight),'shock':shock,
            'shock_source':'instrument' if name in individual else 'sector',
            'overridden_sector_shock':grouped.get(sector) if name in individual else None,
            **_amounts(float(weight)*shock,valuation)})
    sector_rows=[{'sector':s,**_amounts(math.fsum(x['decimal_return'] for x in rows if x['sector']==s),valuation)} for s in sorted(set(sectors.values()))]
    return {'instruments':rows,'sectors':sector_rows,
            'portfolio':_amounts(math.fsum(x['decimal_return'] for x in rows),valuation)}


def replay_window(names, sectors, weights, valuation, returns, dates):
    _portfolio(names,sectors,weights,valuation)
    r=np.asarray(returns,dtype=float)
    daily=portfolio_returns(r,weights)
    try:
        if any(not isinstance(d,str) or date.fromisoformat(d).isoformat()!=d for d in dates):
            raise ValueError('dates must be canonical ISO dates')
    except (TypeError,ValueError) as exc:
        raise ValueError('dates must be canonical ISO dates') from exc
    if len(dates)!=len(daily) or any(a>=b for a,b in zip(dates,dates[1:])):
        raise ValueError('window dates must be increasing, one per return')
    wealth=wealth_index(daily)
    # Daily P&L allocated to starting wealth and current fixed weights; sums
    # exactly to the compounded portfolio change, unlike standalone buy/hold.
    contributions=np.sum(wealth[:-1,None]*r*np.asarray(weights),axis=0)
    rows=[{'instrument':name,'sector':sectors[name],**_amounts(float(v),valuation)} for name,v in zip(names,contributions)]
    return {'daily':[{'date':date,'portfolio_return':float(value),'wealth_index':float(wealth[i+1]),
                       'currency_pnl':_amounts(float(wealth[i]*value),valuation)['currency_amount']} for i,(date,value) in enumerate(zip(dates,daily))],
            'instruments':rows,
            'sectors':[{'sector':s,**_amounts(math.fsum(x['decimal_return'] for x in rows if x['sector']==s),valuation)} for s in sorted(set(sectors.values()))],
            'portfolio':_amounts(float(wealth[-1]-1),valuation)}
