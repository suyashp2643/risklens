# Stage 3 persistence, correction and replay contract

## Stored inputs and exact-byte calculation
An InputSnapshot contains exactly prices.csv, instruments.csv, calendar.csv, corporate_actions.csv, portfolio.json and provenance.json. Each source file is read once into immutable bytes. Hashing, validation and calculation operate on that same captured set. Validation materializes only the captured bytes into a private temporary directory and calls the unchanged Stage 1 loader. Replay materializes bytes from PostgreSQL, never from the original directory and never from a download.

The six reads are not a cross-file atomic filesystem transaction. Do not edit files while capturing a dataset. Even if the directory changes, the hashes refer to exactly the captured bytes that are validated and used for calculation, never a later reread of the source. A coherent but mixed-generation set cannot be identified without a producer-side transaction/manifest. This limitation is separate from the closed hash/read race.

Only validated complete datasets can be saved in Stage 3. Suspected-but-schema-valid split and stale fixtures remain eligible; missing/duplicate fixtures still fail. Quarantined invalid snapshots and investigation workflows are not implemented.

## Canonical serialization and hashes
`risklens-canonical-json-v1` is a project-specific format, not a claim of RFC 8785 compliance. It is UTF-8 JSON produced by Python json.dumps with sort_keys=True, separators=(',', ':'), ensure_ascii=False and allow_nan=False, with no trailing newline. Object keys are sorted, list order is preserved, finite numbers use Python's JSON representation, and Unicode is not normalized. Integers and floating point numbers are not canonicalized into the same representation. Creation times are aware UTC ISO strings. Approval timestamps are parsed and serialized by the Pydantic approval model (UTC may serialize as Z). Names and format identifiers are case-sensitive.

- Each raw file: SHA-256 over the exact original bytes, including whitespace and line endings, plus byte length and RFC 4648 base64 encoding.
- Input hash: SHA-256 over the canonical payload containing the format and all six file manifests/base64 bodies. Formatting-only input changes create a different hash, even if the parsed data and calculated numbers agree.
- Snapshot record hash: SHA-256 over its entire canonical envelope, including id, timestamp, parent, version and explicit approval.
- Configuration/result hashes: SHA-256 over each canonical object.
- Report record hash: SHA-256 over its full envelope, including snapshot references and hash, snapshot record hash, configuration, engine version, result, report kind, empty scenario definitions and correction chain.

Canonical envelopes are stored as PostgreSQL TEXT, not reconstructed from JSONB. SQL checks recompute the whole-record SHA-256 using PostgreSQL's built-in sha256(bytea); no pgcrypto extension is required. Replay independently verifies record canonical form, raw-file hashes and lengths, input hashes, snapshot references, configuration/result hashes and correction chain.

Hashes detect inconsistencies; they are not digital signatures or proof of who approved a correction. A database administrator who can replace data and hashes is outside the protection boundary. Input subhashes are checked in application code; database checks protect the entire supplied record bytes.

## Database immutability and correction lineage
Alembic migration 0001 creates dataset_snapshots and saved_reports with foreign keys and hash/reference checks. ENABLE ALWAYS triggers reject UPDATE, DELETE and TRUNCATE, including a no-op UPDATE. INSERT triggers validate creation timestamps, snapshot versions, report kind and the presence of explicit correction approval. Every correction has an existing parent, parent.version+1, changed captured input bytes, a unique approval record identifier, reason, evidence, approver, timezone-aware approval timestamp and decision=approved. The root has version=1 and no approval.

Versions are lineage depths, not a global sequence: two corrections branching from one parent may both have version=2, distinguished by UUID. Corrections create new snapshots; they do not edit parent inputs or reports. No suspected observation is repaired or approved automatically. Approval records are operator-supplied attestations, not an authentication/authorization system. There is no approval UI yet. A pending template is deliberately invalid for correction creation.

The runtime database role can be granted SELECT/INSERT only; see README. Owners/superusers can intentionally change schema or disable/drop triggers. Administrative Alembic downgrade drops tables and loses saved history, so it is for disposable test databases only. This application does not run downgrade, disable triggers or update records. No ordinary operation can overwrite raw saved data.

SQLAlchemy Session.begin scopes writes. Snapshot + report creation inside one transaction commits together or rolls back together. CLI success is printed only after commit. The snapshot and save-report CLI commands are separate transactions by design; a failed report leaves an already committed snapshot intact. Correction creation is one transaction. A filesystem export is separate from database persistence.

## Report and engine semantics
Package release 0.3.0 retains calculation engine version 0.2.0 because calculations.py is unchanged. Saved reports include the entire Stage 2 calculation configuration: confidence, annualization assumption, horizon, sample floor, weighting convention, VaR/ES estimator, sign and unit conventions. Scenario definitions are explicitly empty because scenarios are unfinished. Correction records are stored in lineage order.

The numerical result structure remains the Stage 2 export structure, including its standalone-export limitation text. The enclosing saved-report record supplies persistence metadata; a standalone report.json/CSV is still not a PostgreSQL record. Saved kind is original_report for an uncorrected snapshot and corrected_report for a child. Replay is read-only and returns exact_replay or replay_mismatch; it does not create a new risk report or rewrite the old one.

A saved engine version different from the available engine is rejected explicitly: exact replay unsupported. A saved configuration not fully reproduced by the engine is also rejected. There is no automatic fallback to a newer engine. A future recalculation under a newer engine must be separately labelled and persisted; that command is unfinished.

## Byte integrity versus numerical agreement
Integrity checks run before calculation and do not use floating tolerances. Numerical comparison recursively covers the full saved result: keys, list lengths/order, text, null and booleans must match exactly; finite numbers use abs(a-b) <= max(1e-12*max(abs(a),abs(b)), 1e-12). These tolerances are decimal-return units, not currency percentages. The same absolute/relative rule applies to currency fields. A complete saved configuration is checked exactly before this comparison.

Replay reports separately: input_byte_integrity, saved_record_integrity, numerical_agreement, and result_bytes_identical (canonical saved versus recalculated result bytes). exact_replay means the same saved inputs/config and available engine produced numerically agreeing outputs; bitwise output identity is reported separately, not assumed. CLI exits 3 for a numerical mismatch and 2 for invalid hashes, unavailable engine/configuration or database errors. NumPy/CPU floating behavior can differ across installations; same-engine numerical replay is not a guarantee of cross-platform bitwise equality.

## Historical information limits
Snapshot creation records the actual time this program saved the captured input set. The supplied synthetic acquired_at values and weight dates are fixture metadata, not evidence of what was known at a past real-world time. No complete historical information-set reconstruction, real historical ingestion audit or point-in-time market-data certification is claimed.

## Verification scope
The database-independent tests cover capture/hash correctness, numerical behavior, approval validation and replay/correction service logic with an explicitly in-memory record repository. That repository does not verify PostgreSQL transactions, SQL constraints or triggers. Real PostgreSQL tests separately exercise migration upgrade/downgrade, direct UPDATE/DELETE/TRUNCATE, malformed inserted hashes, rollback, approval/lineage, engine mismatch, modified result hash/numerical mismatch and replay after source removal. Missing RISKLENS_TEST_DATABASE_URL skips those checks; a configured but unreachable server fails rather than skips. Each real test uses a new disposable schema and removes only that schema afterward.

## Stage 3.1: approvals bound to exact inputs

Every newly submitted approval must include `parent_snapshot_id`, `parent_input_hash` and `proposed_input_hash`. The parent hash is read from verified saved parent bytes; the proposed hash is calculated from the single immutable capture supplied to the correction operation. The service checks all three values before adding any row and rejects a reused record_id. PostgreSQL migration 0002 installs a BEFORE INSERT guard comparing those bindings to the referenced parent and new snapshot input hash; the unique approval identifier index from 0001 also prevents concurrent identifier reuse. Raw payload hashes continue to be independently verified by the application; SQL binding checks use the verified input-hash field and do not independently reproduce Python's nested canonical serialization.

Changing any captured input byte after approval, including whitespace-only changes, invalidates proposed_input_hash. Changing the source directory after the correction capture does not alter that capture. Do not change binding values to bypass a mismatch: prepare and review a new proposal, with a new explicit approval record instead.

`risklens review-correction PARENT_UUID DATASET --output NEW_DIRECTORY` creates `review.json` with the exact bindings and changed-file hashes, `changes.diff` with full textual differences, `proposed-inputs/` containing captured bytes, and a pending `approval.json`. No correction is saved and no approval is granted. Review those artifacts, then record reviewer, actual approval time, reason/evidence and decision=approved. Apply the exact reviewed directory via `risklens correct PARENT_UUID REVIEW_DIRECTORY/proposed-inputs --approval REVIEW_DIRECTORY/approval.json`. Using a source directory instead remains supported but its captured bytes must match the approved hash. Review files are normal editable files; approval binding, not filesystem permissions, rejects post-approval edits.

### Existing history at migration 0002

Migration 0001 is unchanged. In the same transaction, migration 0002 locks snapshot inserts while it records pre-existing unbound correction ids and their full immutable record hashes in `legacy_correction_approvals`, then installs the binding guard. It does not update any snapshot, report, approval, content hash or timestamp. The registry rejects INSERT/UPDATE/DELETE/TRUNCATE after migration; routine code cannot exempt a new correction. Old approval identifiers remain subject to the existing unique index, including when new corrections use a legacy parent.

Legacy unbound records remain readable/replayable only when their id and record hash match this migration-created registry. Replay returns `approval_binding_status` with `legacy_unbound` or `bound` in correction-lineage order. This is an explicit historical limitation, not a retroactive claim that older approvals authorized exact bytes. Binding cannot be invented for old records or patched into immutable history. A new correction from an old parent requires a fresh approval bound to that parent's actual verified hash.

Update to migration 0002 before using package 0.3.1. Owners granting SELECT/INSERT only to a runtime role must additionally grant SELECT on legacy_correction_approvals. It needs no INSERT permission. Downgrading to 0001 drops the classification registry and binding guard without rewriting history; use the matching older application or upgrade again before replaying legacy history under 0.3.1. Administrative downgrades are not an application workflow.
