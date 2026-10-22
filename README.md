# RiskLens — Portfolio Risk Investigation & Replay

Stages 1–5: offline data, risk calculations, PostgreSQL snapshot/replay, investigation/comparison, rolling VaR backtesting, and stress scenarios. This is an educational project, not investment advice. All included prices and securities are synthetic.

## Windows 11 / Ubuntu WSL setup
Run inside Ubuntu WSL, on the Linux filesystem. Python 3.12 and its venv module are required. On Ubuntu 24.04:

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv
cd ~/projects/risklens
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install -e '.[test]'
python -m pytest -q
risklens validate sample-data/demo/clean
risklens report sample-data/demo/clean --output outputs/clean-95
risklens report sample-data/demo/clean --output outputs/clean-99 --confidence 0.99 --trading-days 252
# Generate a fresh copy: refuses to overwrite an existing directory.
risklens demo --output sample-data/my-demo
risklens validate sample-data/my-demo/split
# These intentionally exit 2:
risklens validate sample-data/my-demo/missing
risklens validate sample-data/my-demo/duplicate
```

Extract the delivered archive under `~/projects` before running these commands. Stages 1–2 directory workflows need no Docker, paid services, database or network after dependency installation. Stage 3 persistence needs local PostgreSQL. Python native Windows also works: `py -3.12 -m venv .venv`, `.venv\Scripts\Activate.ps1`, then the same Python/CLI commands.

## Repository

- `backend/src/risklens/schemas.py`: typed boundary contracts.
- `backend/src/risklens/ingestion.py`: strict ingestion; raw files remain untouched.
- `backend/src/risklens/demo.py`: deterministic synthetic dataset and fixtures.
- `backend/src/risklens/calculations.py`: pure validated numerical functions.
- `backend/src/risklens/reporting.py`: dataset adapter and JSON/CSV exports.
- `backend/src/risklens/cli.py`: generation, validation and report commands.
- `tests/`: independent integrity and reproducibility checks.
- `sample-data/demo/`: clean and four intentionally corrupted datasets.
- `docs/`: schemas, conventions, architecture, implementation status.
- `frontend/`: Stage 6 location, currently no application.

## Dependencies
The original data/calculation dependencies pin Pydantic 2.13.5, NumPy 2.3.5 and Pandas 2.2.3; test extra pins Pytest 8.3.5. `requirements-lock.txt` records the resolved isolated environment. Python is constrained to 3.12. Backend/API, database, SciPy and frontend dependencies will be selected and verified in their implementation stages rather than claimed as installed now.

Official references checked: https://fastapi.tiangolo.com/deployment/versions/ and https://pydantic.dev/docs/validation/latest/get-started/install/ . A successful local install, `pip check` and tests establish compatibility for this stage; no claim of checking all future dependencies is made.

## Reports

`risklens report DATASET --output NEW_DIRECTORY [--confidence 0.95] [--trading-days 252]` produces `report.json`, `metrics.csv`, `daily.csv` and `metadata.json`. Destinations must not already exist. Errors exit 2 before creating output when validation or calculation fails. Included `sample-reports/clean-95/` was generated from the existing synthetic clean fixture. No download or database is required.

See [calculation methodology](docs/methodology.md) for signs, units, quantile/tail conventions and minimum samples. The first daily row has no return; wealth starts at one. All bundled report results are synthetic. Daily rebalancing is hypothetical, not a reconstruction of actual holdings.

## Status
Completed: all Stage 1 schemas, ingestion, provenance, deterministic fixtures and hashes; simple instrument and constant-weight portfolio returns; cumulative return; annualized sample volatility; initial-value-aware drawdown; historical one-day VaR/ES; strict reporting CLI; JSON/CSV exports; independent reference, edge-case and integration tests.

Stage 3 implemented: PostgreSQL persistence, immutable snapshots/reports, explicit correction records, source-independent replay and database CLI commands. Database execution limitations are recorded in docs/verification.md.

Stage 4 implemented: captured-input investigation, structured evidence, JSON/CSV exports and compatible original/corrected report comparisons.

Unfinished: risk-change bridge, API, frontend, full interview script and advanced extensions. PostgreSQL migrations and commands are below; API/frontend startup instructions will accompany Stage 6. No production, user or performance claims are made.

Stage 4 investigation and controlled comparisons are implemented below. Stage 5 backtesting and scenarios are implemented below. Frontend, API and Power BI have not been started.

## Stage 3: PostgreSQL on Ubuntu WSL, no Docker

This release adds saved snapshots, approved corrections and offline-from-source replay. Use PostgreSQL 16 on Ubuntu 24.04 (14+ provides the required SHA-256 function). From your extracted `~/projects/risklens` directory:

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv postgresql
sudo service postgresql start
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install -e '.[test]'

# Create a local role matching your WSL username; peer auth needs no password.
# Skip createuser if this role already exists.
sudo -u postgres createuser --createdb "$USER"
createdb risklens
export RISKLENS_DATABASE_URL="postgresql+psycopg://$USER@/risklens"
alembic upgrade head
alembic current

# Separate test DB: tests create and remove their own isolated schemas.
createdb risklens_test
export RISKLENS_TEST_DATABASE_URL="postgresql+psycopg://$USER@/risklens_test"
python -m pytest -q -rs
python -m pip check
```

Run `export` commands in each new terminal or set equivalent environment values. `.env.example` is a reference; the application does not automatically load `.env`. If PostgreSQL is installed on Windows rather than WSL, use a `postgresql+psycopg://...` TCP URL and configure authentication yourself; the tested command path here is WSL peer authentication. No API/server/frontend startup is needed for Stage 3.

### Commands

```bash
# Prints a snapshot UUID and input hash.
risklens snapshot sample-data/demo/clean
# Substitute the returned UUID; this reads the database snapshot, not the directory.
risklens save-report SNAPSHOT_UUID --confidence 0.95 --trading-days 252
risklens reports
risklens replay REPORT_UUID

# Existing directory-based workflow remains available and does not use PostgreSQL.
risklens report sample-data/demo/clean --output outputs/stage3-clean
```

Snapshot/report CLI JSON output is script-readable. Database commands require migration 0001. Existing output directories remain protected from overwrite. Package dependencies add SQLAlchemy 2.0.44, Alembic 1.17.1 and Psycopg 3.2.12 with its binary extra; the full isolated resolution is pinned in requirements-lock.txt. Core calculation engine remains 0.2.0; package version is 0.3.1.

### Explicit correction and demonstration

Inspect `sample-data/demo/split/fixture.json`, the synthetic corporate actions and clean/split prices. The demo correction replaces the deliberately corrupted synthetic split prices with the deterministic clean data. Then create your own review record:

```bash
cp docs/correction-review-template.json correction-approval.json
nano correction-approval.json
```

Fill every field, use a unique review identifier, record the actual aware approval time and evidence, and set decision to `approved` only after explicitly reviewing the correction. An unchanged pending template is rejected. Example evidence can reference the bundled fixture manifest and the hashes printed by:

```bash
sha256sum sample-data/demo/split/prices.csv sample-data/demo/clean/prices.csv
```

The demo now uses separate prepare/apply phases. See the Stage 3.1 commands below; the old one-step `--approval` invocation is no longer supported because approval must bind to a parent already created and reviewed.

For the individual correction CLI workflow, first create a split snapshot and use its id:

```bash
risklens snapshot sample-data/demo/split
risklens correct PARENT_SNAPSHOT_UUID sample-data/demo/clean --approval correction-approval.json
risklens save-report CHILD_SNAPSHOT_UUID
```

### Optional runtime role with SELECT/INSERT only

The setup above uses your local owner role for a simple development workflow. PostgreSQL triggers still block saved-row mutations under that role. To separate routine operations from migrations, run the following once as the owner in `psql risklens`, replacing `risklens_app` only if needed:

```sql
CREATE ROLE risklens_app LOGIN;
GRANT CONNECT ON DATABASE risklens TO risklens_app;
GRANT USAGE ON SCHEMA public TO risklens_app;
GRANT SELECT, INSERT ON dataset_snapshots, saved_reports TO risklens_app;
```

Configure a password with `\password risklens_app` interactively if using TCP; do not put it in this repository. Run migrations with the owner and routine commands with the application role. No sequences are used; record ids are application-generated UUIDs.

See [persistence contract](docs/persistence.md) for serialization/hash rules, database guarantees, transaction boundaries, correction records, replay tolerances and historical limitations. Frontend, API and Power BI remain unfinished. Do not use `alembic downgrade base` on a database whose saved history you want to retain; downgrade is tested only in disposable schemas.

## Stage 3.1 update: review and bound approval

For an existing Stage 3 database, preserve it and apply the new migration:

```bash
cd ~/projects/risklens
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install -e '.[test]'
export RISKLENS_DATABASE_URL="postgresql+psycopg://$USER@/risklens"
alembic upgrade head
alembic current  # 0002
```

Do not recreate the database or downgrade it. Migration 0002 preserves history and explicitly classifies older unbound approvals; see docs/persistence.md. If using the optional runtime role, the owner must grant its registry read permission:

```sql
GRANT SELECT ON legacy_correction_approvals TO risklens_app;
```

Inspect and approve a proposal in two steps:

```bash
risklens review-correction PARENT_UUID sample-data/demo/clean --output reviews/clean-proposal
cat reviews/clean-proposal/review.json
less reviews/clean-proposal/changes.diff
nano reviews/clean-proposal/approval.json
# Fill reviewer, actual approval time, reason and evidence; approve only after review.
# Keep the generated parent id and parent/proposed hashes intact.
risklens correct PARENT_UUID reviews/clean-proposal/proposed-inputs \
  --approval reviews/clean-proposal/approval.json
```

The template now requires parent_snapshot_id, parent_input_hash and proposed_input_hash. Existing unbound approval JSON files cannot authorize a new correction. Approval identifiers cannot be reused, even for the same proposal. Changing proposed-inputs after approving the hash causes rejection.

The demo now also has two explicit phases:

```bash
python scripts/demo_stage3.py --prepare reviews/demo-bound
cat reviews/demo-bound/review.json
less reviews/demo-bound/changes.diff
nano reviews/demo-bound/approval.json
python scripts/demo_stage3.py --apply reviews/demo-bound \
  --approval reviews/demo-bound/approval.json
```

Prepare saves the original synthetic split snapshot/report, removes its temporary source, replays it and exports the pending clean proposal. Apply uses the saved parent id rather than creating a new parent, validates the approved bindings, then saves/replays a separate corrected version. Stages 4 and 5 are implemented in later sections.

Run remaining real PostgreSQL checks on WSL:

```bash
sudo service postgresql start
# Run once if the disposable test database does not already exist:
createdb risklens_test
export RISKLENS_TEST_DATABASE_URL="postgresql+psycopg://$USER@/risklens_test"
python -m pytest -q -rs
python -m pytest -q tests/test_stage3_postgres.py -rs
python -m pip check
```

Tests migrate isolated schemas in that test DB. A configured but unreachable database fails instead of skipping. New live checks cover approval mismatch enforcement, reuse of an id, post-approval byte changes, and upgrade from 0001 with unchanged legacy snapshot/report bytes.

## Stage 4: investigation and report comparison

This release uses the supplied RiskLens-Stage-3-Verified.zip as its baseline. Its SQL CASE, explicit PostgreSQL approval lookup and PostgreSQL test transaction fixes are preserved unchanged, as are all previous tests, schemas, calculation functions, fixtures and migrations. Package release is 0.4.0; numerical engine remains 0.2.0. Dependencies are unchanged. There is no new database migration: head remains 0002.

Update the code without deleting your existing databases, then run in WSL:

```bash
cd ~/projects/risklens
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install -e '.[test]'
sudo service postgresql start
export RISKLENS_DATABASE_URL="postgresql+psycopg://$USER@/risklens"
alembic upgrade head
# Use your existing disposable test DB, or create it once if absent.
export RISKLENS_TEST_DATABASE_URL="postgresql+psycopg://$USER@/risklens_test"
python -m pytest -q -rs
python -m pip check
```

Offline investigation includes invalid fixtures and does not require PostgreSQL:

```bash
risklens investigate sample-data/demo/clean --output outputs/investigation-clean
risklens investigate sample-data/demo/split --output outputs/investigation-split
risklens investigate sample-data/demo/stale --output outputs/investigation-stale
risklens investigate sample-data/demo/missing --output outputs/investigation-missing
risklens investigate sample-data/demo/duplicate --output outputs/investigation-duplicate
```

Each output contains report.json, issues.csv and capture.json preserving the six exact raw inputs. The diagnostic command exits successfully when it produces evidence, including for blocked datasets; inspect calculation_blocked. Normal `risklens report` still rejects missing/duplicate/invalid prices. Output folders must be new. Threshold options: `--unchanged-sessions 5 --large-return-threshold 0.20 --split-relative-tolerance 0.03`.

Compare locally generated standalone risk reports:

```bash
risklens report sample-data/demo/split --output outputs/risk-split
risklens report sample-data/demo/clean --output outputs/risk-clean
risklens compare outputs/risk-split/report.json outputs/risk-clean/report.json \
  --output outputs/comparison-local
```

This produces report.json and comparison.csv. Compatible standalone differences are not labelled approved correction impact. No report can be generated from the invalid missing/duplicate fixture, so those cannot have original risk metric comparisons.

For an approved PostgreSQL comparison, use the two-phase demo already provided:

```bash
python scripts/demo_stage3.py --prepare reviews/stage4-demo
cat reviews/stage4-demo/review.json
less reviews/stage4-demo/changes.diff
nano reviews/stage4-demo/approval.json
# Record actual reviewer/time/reason/evidence and explicit approval after inspection.
python scripts/demo_stage3.py --apply reviews/stage4-demo \
  --approval reviews/stage4-demo/approval.json
# Use the original and corrected report ids printed by the demo:
risklens compare-saved ORIGINAL_REPORT_UUID CORRECTED_REPORT_UUID \
  --output outputs/comparison-approved
```

The clean dataset above is an explicitly proposed synthetic correction, not an input to the detector. Detection itself never reads clean reference data or fixture.json. Inspect issues.csv and comparison.csv in Power BI; use decimal fractions as percentages and keep percentage-point difference columns separate.

Run database checks alone if needed:

```bash
python -m pytest -q tests/test_stage3_postgres.py tests/test_stage4_postgres.py -rs
```

See docs/investigation-methodology.md for exact thresholds, structured evidence, calendar/false-positive limits, compatibility criteria and raw-capture handling. The included sample-analyses are backend-generated synthetic demonstrations. Stage 4 does not add a frontend or backtesting.

## Stage 5: rolling VaR backtesting and stress scenarios

Stages 1–5 are included. Pure numerical modules are `backtesting.py` and `stress.py`; `scenario_reporting.py` adapts validated snapshots. Existing report engine 0.2.0 and saved-report compatibility/approval safeguards are unchanged; new analyses have version 0.5.0. No migration was added (head remains 0002). Frontend, API and Power BI remain for later work.

### Exact Ubuntu WSL setup

Keep the verified baseline directory as a backup. Extract this ZIP into a new parent directory, not on top of an existing repository:

```bash
sudo apt update
sudo apt install -y python3.12-venv python3-pip unzip postgresql postgresql-contrib
mkdir -p ~/projects/risklens-stage5
unzip /mnt/c/Users/YOUR_WINDOWS_USER/Downloads/RiskLens-Stage-5.zip -d ~/projects/risklens-stage5
cd ~/projects/risklens-stage5/risklens
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-lock.txt
python -m pip install -e '.[test]'
python -m pip check
python -m pytest -q -rs
```

The first suite run skips PostgreSQL tests when RISKLENS_TEST_DATABASE_URL is unset. For an existing Stage 4 installation you can reuse its virtual environment and databases, but install this new repository with `python -m pip install -e '.[test]'` first.

Local PostgreSQL without Docker, for a **fresh** user/database setup:

```bash
sudo service postgresql start
sudo -u postgres createuser --createdb "$USER"
createdb risklens
createdb risklens_test
```

If those names already exist, retain them and skip the corresponding creation command. Peer authentication uses your Ubuntu username. Never point test settings at an unrelated database. Tests create isolated schemas and drop only those schemas; the test role needs schema-creation privileges.

```bash
cd ~/projects/risklens-stage5/risklens
source .venv/bin/activate
export RISKLENS_DATABASE_URL="postgresql+psycopg://$USER@/risklens"
export RISKLENS_TEST_DATABASE_URL="postgresql+psycopg://$USER@/risklens_test"
alembic upgrade head
alembic current
python -m pytest -q -rs tests/test_stage3_postgres.py tests/test_stage4_postgres.py
python -m pytest -q -rs
```

A configured unreachable PostgreSQL database fails tests rather than being treated as a successful skip. Stage 5 adds no database persistence; previous database migration, immutability, approval binding and replay tests remain intact.

### Clearly labelled synthetic demonstration

Use new output names each time; existing directories are rejected, including the bundled sample directories.

```bash
risklens backtest sample-data/demo/clean --confidence 0.95 --lookback 250 --output outputs/stage5-bt95
risklens backtest sample-data/demo/clean --confidence 0.99 --lookback 500 --output outputs/stage5-bt99
risklens stress sample-data/demo/clean --scenario examples/synthetic-shock.json --output outputs/stage5-shock
risklens window-replay sample-data/demo/clean --start 2023-01-02 --end 2023-01-16 --output outputs/stage5-window
```

JSON, detailed CSV, summary CSV, metadata and raw input capture are exported together. Complete configuration/scenario definitions live in JSON and CSV metadata. Bundled results are in `sample-analyses/stage5-*`. Do not use the legacy original-versus-corrected risk-report comparison command for Stage 5 analysis envelopes.

The bundled 95%/250-session backtest has 505 evaluated days, 30 exceptions (5.94%), and 25.25 expected exceptions. Kupiec LR ≈ 0.88946 and asymptotic p-value ≈ 0.34562. This does not prove reliability, independence of exceptions or sufficient sample size. The hypothetical stress applies −30% technology, −10% financials, −5% healthcare and −15% energy, yielding −17.5% / −USD 17,500 at the USD 100,000 starting valuation. The Technology sector's −20% scenario shock is explicitly overridden by SYN_TECH's −30% shock. The synthetic window returns approximately −1.6256% / −USD 1,625.59; it is not a real historical crisis.

See [Stage 5 methodology](docs/backtesting-stress-methodology.md) for signs, units, tie rules, training requirements, coverage, overlap, contribution and compounding definitions, statistical limitations and hashes. No auto-repair or automatic approval occurs. Frontend, API, Power BI, conditional coverage tests, multi-day VaR and Stage 5 persisted analysis replay remain unfinished.
