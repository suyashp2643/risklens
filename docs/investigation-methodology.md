# Stage 4 investigation and comparison methodology

## Capture and calculation separation

Investigation accepts an InputSnapshot of the six original input files. It uses those captured bytes only. It does not read fixture.json, a clean reference directory, network prices, or a database. Dataset mode is carried from the supplied provenance and labelled synthetic/historical/unknown; invalid provenance is not certified. The existing loader is called on the same capture for strict validation, never changed or bypassed for risk calculation.

The diagnostic parser can examine malformed rows that the loader rejects. Usable observations are solely for finding evidence; they are not a cleaned portfolio dataset. Missing/duplicated/invalid observations interrupt suspicious-return and equal-price runs, so no multi-session return is represented as a daily observation. Calculations still go through the strict loader and reject incomplete/invalid inputs. Valid suspicious values remain in risk calculations; no data is dropped, filled or automatically corrected.

CLI exports include capture.json (all six original byte bodies, hashes and lengths), report.json and issues.csv. Raw values such as NaN remain strings in evidence rather than nonfinite JSON numbers. Capture JSON can be restored via InputSnapshot.restore(saved['inputs'],saved['input_hash']); it preserves invalid raw inputs for future review without inserting them into the validated-only PostgreSQL snapshot table. Export folders are new only and ordinary local files, not immutable database records. Missing physical input files cannot form the existing six-file snapshot: capture fails explicitly rather than inventing empty replacements.

## Structured evidence and classes

Each finding contains a stable issue_id (SHA-256 of canonical evidence), instrument (null for dataset-wide failures), affected dates, rule, observed_values, category, severity, calculation_blocking and suggested_review_action. Output ordering is deterministic for the same captured bytes and policy; there are no generated timestamps in findings. Row numbers are one-based data-record ordinals, not physical CSV line numbers. Input row order is preserved in duplicate evidence. Sorting does not mutate raw input.

- confirmed_integrity_error / error / blocking: missing expected observations, duplicate dates per instrument, nonpositive/nonfinite/unparseable prices, unknown instruments, malformed schema/date/calendar, or other strict-loader errors. When already explained by detailed blocking findings, the authoritative loader error remains at report level; otherwise it produces dataset_validation_failure (including invalid weights or inconsistent provenance).
- suspected_data_issue / warning / nonblocking: suspicious unchanged closes or a potential unadjusted split. Neither proves an error or permits automatic repair.
- market_movement_review / warning / nonblocking: unusually large returns. An actual market move can be correct data.
- confirmed_numeric_limit / error / blocking: a finite price pair whose ratio overflows the numerical return representation. This is a calculation limitation, not proof the quoted prices are wrong.

`dataset_valid` means schema/integrity validation passed. `calculation_blocked` includes validation failures or confirmed numeric limits; it is not a promise of sufficient risk sample size or that all future numerical computations are representable. Stage 2's sample and numerical policies independently remain authoritative.

## Rules and thresholds

Missing observations: compare registered instruments with every supplied expected calendar session. Dates excluded by that calendar are not reported as missing. A malformed/unsorted/duplicate calendar is blocking, and gaps are not inferred from an unreliable calendar. RiskLens does not assert that a user-supplied real calendar or its exclusions are correct. The bundled calendar is explicitly synthetic weekdays with no real holiday model.

Duplicates: more than one raw price record for an instrument/session, including identical prices, blocks ingestion. All duplicate raw values and row ordinals are evidence. Invalid prices: the unchanged Price model requires finite positive values; raw strings are retained in evidence.

Unchanged prices: at least 5 consecutive expected-session observations with exactly equal adjusted closes by default. This is a count of observations, so 5 equal closes correspond to 4 zero returns. A maximal sequence is emitted once, including its first close. Missing, invalid or duplicate records break the run. Exact equality is intentional; no hidden rounding/tolerance creates a stale flag. Illiquid instruments, holidays, suspensions or legitimate trading outcomes require review; equal closes alone do not prove staleness.

Large returns: abs(P(t)/P(t-1)-1) >= 0.20 by default, with both closes valid, unique and consecutive under the calendar. This is a 20% daily absolute return threshold, not an annualized number. Inclusive comparisons allow only a 1e-12 absolute roundoff margin at the boundary. The reported raw return is not rounded or changed.

Potential unadjusted splits: compare P(t)/P(t-1) with 1/f or f for f in {2,3,4,5,10}. Flag relative distance abs(ratio/target-1) <= 0.03 by default, with the same 1e-12 boundary roundoff margin. If several targets match, use the smallest relative distance, with factor/target as deterministic tie breakers. Same-date split ledger entries from the captured corporate_actions.csv are additional supplied evidence, not unquestioned authoritative truth. A ratio-compatible genuine crash/rally can also trigger this suspicion. Splits outside these factors or with large simultaneous market moves can be missed; this is a transparent review heuristic, not a complete adjustment detector.

All thresholds are explicit in report.policy. unchanged_sessions must be an integer >=2; return threshold must be finite positive; split tolerance must be finite, positive and <0.5. CLI options accept decimal fractions, e.g. --large-return-threshold 0.20.

## Original versus corrected reports

Pure comparison validates complete finite metrics and dated rows/configuration, then checks the entire date sequence, base currency, full portfolio (including valuation/weights_as_of), full calculation configuration, engine version, data mode, adjustment basis and calendar assumption. Any mismatch produces incompatible_reports, lists failures, retains both values with their respective units, and suppresses numerical differences. It does not attribute changes between different portfolios or models to a correction.

Compatible standalone JSON yields compatible_difference only. JSON files do not establish approved lineage or authentic inputs; no correction-impact claim is made. The difference is corrected minus original. Decimal fraction differences are separate from percentage-point differences (difference*100). For example, VaR changing from .02 to .01 is -.01 decimal or -1 percentage point, not a -1% relative change. Currency rows use the explicit base currency and no percentage-point field.

Persisted comparison first verifies both report hashes, snapshots, configuration/engine compatibility, and numerical replay. It then requires the corrected snapshot to descend from the original snapshot through hash-bound approved corrections. Legacy unbound records among those intervening corrections do not establish exact-byte approval. Original snapshots may themselves have older ancestors; only intervening correction approvals support the new comparison.

For the label correction_impact, all report checks must pass and all nonprice input files must also be byte-identical: instruments, calendar, corporate actions, portfolio and provenance. This conservative rule prevents changing another input or assumption while attributing all changes to prices. Formatting-only changes to those files also fail this strict label. Otherwise a compatible numerical difference may be shown, accompanied by explicit correction-impact rejection reasons. A schema-valid suspicious original report is allowed; an invalid original dataset has no risk report to compare. An identical numerical result after a price formatting change can legitimately show zero difference.

The label describes an approved, controlled corrected-input comparison; it is not causal attribution, a sequential risk bridge, or a claim that each suspected observation was bad data. Dates and currency match alone are insufficient. Comparison code is read-only, does not approve corrections, and does not rewrite stored reports.

## Workflow and limits

Investigate -> inspect evidence -> prepare correction review -> record explicit human approval bound to exact bytes -> save a new child snapshot/report -> compare saved original/corrected reports. The existing approval workflow and database history are unchanged. No investigation auto-generates approved decisions or modifies raw input. False positives and missed issues are expected limitations of configurable heuristics. Frontend, backtesting, scenarios and risk-change bridge remain later work. Synthetic demonstrations and their timestamps do not reconstruct a real historical information set.
