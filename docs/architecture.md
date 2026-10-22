# Architecture and locked conventions

CLI and future API call ingestion/services. Pure calculations will accept validated arrays and explicit configuration without database imports. Storage will persist raw inputs, corrected versions, portfolio/configuration/scenario snapshots and report metadata through SQLAlchemy/PostgreSQL/Alembic. Frontend will render API results, never calculate substitute metrics.

Stage 1 raw CSV reading is separate from validated ingestion. The future investigation engine will inspect raw rows, including rows ingestion rejects. Split and stale fixtures remain schema-valid; their existence is not itself a detection result. Each fixture preserves clean files in a different directory. Generator refuses overwrite. Corporate action ledger is synthetic supporting evidence. No repairs are approved or applied yet.

Future calculation conventions: simple daily returns; aligned complete sessions; constant-weight daily rebalanced analytical history; sample standard deviation (ddof=1), configurable 252 annualization default; cumulative wealth for drawdown. Current valuation converts fractional loss to currency and does not imply a historical position ledger.

Proposed Stage 2 loss convention is loss = -return. Historical VaR will use NumPy linear quantiles; ES will use the integral of the empirical quantile function over the upper tail with fractional mass at the boundary, documenting ties. Negative VaR will remain signed and explained, not clipped. Default minimum history will be max(100, ceil(5/(1-confidence))) returns, ensuring at least five expected tail observations; small-array mathematical tests may explicitly bypass the production policy. These are design decisions, not implemented metrics. No multi-day scaling is assumed.

Sequential comparison bridge will substitute data, then weights, then configuration, reconciling metric deltas; order dependence/interactions will be visible. Exact replay must check engine availability. Snapshot replay is different from reconstructing information available on a historical date.

## Stage 2
The CLI invokes reporting.calculate_report, which validates through the unchanged loader and aligns the complete calendar/instrument matrix. It delegates only numerical arrays to calculations.py. The pure engine imports only NumPy and Python standard-library modules. reporting.py owns serialization; it does not require an API or database. See methodology.md for the complete financial contract.

## Stage 3
snapshots.py captures six files into immutable bytes; validation/calculation reuse only that capture. storage.py owns SQLAlchemy transactions and saved canonical envelopes. Alembic migration 0001 supplies PostgreSQL DML immutability and insert guards. Reporting supports directory and supplied-snapshot adapters. Replay checks hashes/configuration/engine before full-result numerical comparison. scripts/demo_stage3.py is a caller-approved correction demonstration; it requires a real PostgreSQL server.

## Stage 4

investigations.py diagnoses the exact InputSnapshot bytes without database access or repair. It reuses unchanged schema validation as the final integrity gate and exports raw capture evidence separately. comparisons.py validates and compares numerical reports and serializes JSON/CSV. comparison_storage.py is a read-only adapter that verifies saved replay and approved lineage before allowing a correction-impact label. All existing schemas, calculations, approval persistence and migrations are unchanged from the user's verified baseline. No frontend or backtesting is added.

## Stage 5 separation

`backtesting.py` and `stress.py` implement pure numerical operations. `scenario_reporting.py` validates supplied immutable snapshots and creates separate versioned analysis envelopes; CLI only captures, dispatches and exports. Existing risk reporting, engine version 0.2.0, SQLAlchemy persistence, migrations, approval binding and comparison rules are unchanged. Stage 5 analysis envelopes are not inserted into the Stage 3 risk report tables.
