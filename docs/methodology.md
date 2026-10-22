# Stage 2 calculation methodology

## Inputs and scope
All bundled securities and prices are fictional synthetic data. Imported historical CSVs retain user-supplied source and adjustment assertions; RiskLens does not independently certify them. Stage 1 schemas, fixture bytes and validation rules are unchanged. Every report first calls the strict dataset loader. Duplicate records, unknown instruments, prices outside the supplied calendar, nonpositive/nonfinite prices, and any missing expected instrument/session observation block calculation. No price is filled, row dropped or missing return treated as zero.

The pure matrix function assumes rows are consecutive expected sessions and columns correspond to the supplied weight vector. It checks numerical integrity; calendar/identifier integrity belongs to the dataset adapter. It returns N-1 rows from N price sessions, so the first date has no observed return. Its CSV return cell is blank and JSON return is null, never zero.

## Returns and portfolio interpretation
Simple adjusted-close return: r(i,t) = P(i,t)/P(i,t-1) - 1. Prices follow the adjustment basis stated in provenance. Stock split/dividend interpretation is not inferred from price changes.

Portfolio return: r(p,t) = sum_i w(i)*r(i,t). The fixed supplied weights are restored at every prior close, without transaction costs, taxes or slippage. Unheld registered instruments receive explicit zero exposure; their price observations must still pass ingestion. Weights are nonnegative and sum to one within the existing 1e-10 tolerance; they are never normalized silently.

These weights define a hypothetical daily-rebalanced historical strategy. The weights_as_of field is preserved. Current weights do not reconstruct historical holdings or actual investor performance. Currency risk estimates use the supplied portfolio valuation, not the terminal simulated wealth.

Wealth starts at W(0)=1, then W(t)=product_s(1+r(p,s)). Cumulative return = W(final)-1. A -100% return produces zero subsequent wealth. Finite inputs outside numerical output range are rejected.

## Volatility and drawdown
Sample volatility = sample standard deviation with denominator N-1, multiplied by sqrt(trading_days_per_year). At least two returns are necessary. The positive integer annualization assumption defaults to 252 and is configurable. This is a convention for annualizing daily standard deviation; it does not assert independence or justify multi-day VaR scaling.

Drawdown(t) = 1-W(t)/max(W(0),...,W(t)). Maximum drawdown is its largest value, reported as a nonnegative decimal fraction. Initial wealth participates in the peak, so two initial losses of 10% have a 19% drawdown. No annualization applies to cumulative return or drawdown.

## Historical one-day VaR and expected shortfall
Loss L=-r(p). Positive means loss, negative means gain. Confidence c is strictly between 0 and 1 and defaults to .95. Both estimates are one-day, unconditional historical empirical estimates using the same return sample. No square-root-of-time scaling or multi-day estimate is implemented.

VaR uses the inverse empirical CDF (order-statistic quantile; analogous to the inverted_cdf estimator). Sort losses ascending. VaR = L[ceil(N*c)] with one-based indexing; there is no linear interpolation. This definition includes ties in the empirical distribution. It need not leave exactly N*(1-c) strictly larger observations.

ES is the average of the worst N*(1-c) empirical observations, allowing a fractional boundary observation. Sort losses descending, let m=N*(1-c), k=floor(m) and f=m-k. ES=(sum of the worst k losses + f*the next loss)/m; omit the next term when f=0. This is the integrated empirical upper quantile tail, not an average of every loss greater than or equal to VaR. Ties at the boundary receive only enough combined probability mass to complete the tail; tied values are equal, so their order has no effect. For losses 0%,2%,2%,2%,5% at 60% confidence, m=2, VaR=2% and ES=3.5%. Averaging all observations at or above VaR would give a different, unintended result.

Tail mass and sample-threshold arithmetic snap roundoff near integers with a relative 1e-12 tolerance. No substantive rounding of returns, VaR or ES is performed.

Negative VaR and ES are retained and labelled: a negative estimate means even this empirical loss quantile/tail was a gain. It is not clamped to zero and does not mean future losses are impossible. ES is at least VaR under these conventions.

Default minimum sample policy: N >= max(100, ceil(5/(1-c))) returns. This requires five expected tail observations: 100 returns at 95%, 200 at 97.5%, 500 at 99%. 756 price sessions give 755 returns. The CLI cannot lower this policy. The pure TailPolicy object can explicitly lower it for hand-calculated tests; tiny arrays in tests are not production estimates. Meeting this floor does not guarantee statistical precision or model adequacy.

All return/volatility/drawdown/VaR/ES fractions are decimals: .01 means 1%, not .01%. Currency VaR/ES = fraction estimate * portfolio.valuation in base_currency. Currency losses and percentage losses are distinct fields. Volatility is annualized; VaR/ES are one-day; cumulative return/drawdown cover the supplied sample range.

## Exports and limits
`report.json` contains provenance, portfolio, configuration, metrics and full dated series. `metrics.csv` carries mode, currency, confidence, observation count, metric, unit, horizon and engine version. `daily.csv` carries mode/currency and all dated instrument returns, portfolio returns, wealth, cumulative return and drawdown. `metadata.json` carries the conventions and provenance alongside CSV imports. Import all relevant files into Power BI; percentage-format decimal fields without multiplying them twice.

Output directories must be new; exports refuse overwrite. Valid suspicious prices remain in results, explicitly pending Stage 4 investigation. Exported reports are ordinary local files, not immutable persisted reports or verified replay snapshots. There is no real-world point-in-time information-set reconstruction, database, replay, coverage test, backtest or scenario analysis yet.
