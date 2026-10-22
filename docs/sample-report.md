# Included synthetic clean report

Run from the repository root:

```bash
risklens report sample-data/demo/clean --output outputs/clean-95
```

The included `sample-reports/clean-95` was generated with engine 0.2.0, confidence 95% and 252 trading days per year. Four fictional USD equities provide 756 synthetic sessions and 755 daily returns. It describes the constant-weight daily-rebalanced strategy using the supplied portfolio specification, not an actual investment account.

The strategy finished 0.195% below its initial value. Its annualized daily sample volatility is 11.397%; that summarizes variability rather than predicting a future loss. Its deepest decline from a running peak was 23.258%, even though its final net loss was small.

Historical one-day 95% VaR is 1.180% of the supplied valuation, or USD 1,179.73 on USD 100,000. It is the empirical 95th percentile of daily losses under the documented inverse-CDF definition, not a maximum possible loss. Historical ES is 1.492%, or USD 1,492.39, averaging the worst 5% empirical probability mass. With 755 returns this tail contains 37 full observations plus 75% of the next observation; it is not an average of all values tied at or above VaR.

All values come from generated data. Neither these results nor meeting the minimum sample size validate a real-world risk model. Report JSON retains decimal fractions; this explanation rounds for readability. Corruption detection, correction comparisons, immutable replay and backtesting remain for later stages.
