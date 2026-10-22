# Dataset contract v1

Every dataset directory contains the following files. Headers must match exactly in the documented order. UTF-8 CSV; ISO dates; decimal numbers without percent signs. Weights are fractions. Unknown columns are rejected. Empty calendar/registry, malformed rows, invalid prices, duplicate price keys, unknown instruments, unexpected dates and missing expected sessions block calculation eligibility. No filling or zero returns.

| File | Exact header / fields |
|---|---|
| prices.csv | date,instrument,adjusted_close |
| instruments.csv | instrument,sector,currency |
| calendar.csv | date |
| corporate_actions.csv | date,instrument,action,value,evidence |
| portfolio.json | base_currency,valuation,weights_as_of,convention,weights |
| provenance.json | mode,source,acquired_at,currency,start_date,end_date,adjustment_basis,calendar_assumption,observation_timestamps_available |

`prices.csv`: one strictly positive finite adjusted close per instrument/session. Date/instrument uniquely identify an observation. Example: `2024-01-02,SYN_TECH,100.00000000`.

`instruments.csv`: unique registry names and sector labels, one uppercase three-letter base currency. Example: `SYN_TECH,Technology,USD`.

`calendar.csv`: sorted unique expected sessions; bounds equal provenance bounds. A date absent from this explicitly supplied calendar is not an expected observation. Calendar provenance must explain exchange holidays and instrument coverage. The initial contract assumes all instruments share the same calendar; suspended instruments need explicit policy in a future contract.

`corporate_actions.csv`: optional rows but mandatory header. `split` value is new shares per old share (2 = 2-for-1), `dividend` value is cash per share in base currency. Evidence is mandatory. This is supporting evidence, not an adjustment processor. Example: `2024-01-02,SYN_TECH,split,2,Synthetic fixture ledger`.

`portfolio.json`: valuation is positive base-currency money; weights list contains unique `{instrument,weight}` objects, each [0,1], summing to 1 within absolute 1e-10. Current weights have an as-of date. Historical convention is `constant_weight_daily_rebalanced`: an analytical hypothetical portfolio, not recovered actual holdings.

`provenance.json`: mode `synthetic` or `historical_csv`; source and adjustment/calendar descriptions required; timezone-aware acquired_at; inclusive date bounds. Imported data must document provider/export method, actual download time, currency, split/dividend adjustment method and expected session source. User CSV alone cannot establish adjustment correctness. No provider download adapter exists yet.

The included fixed acquired_at is explicitly a synthetic placeholder; no historical information-set claim is possible. Generated manifest SHA-256 hashes identify fixture bytes only. Future reports will hash input/configuration snapshots separately.
