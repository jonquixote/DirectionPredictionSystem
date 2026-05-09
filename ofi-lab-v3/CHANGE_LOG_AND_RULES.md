# CHANGE LOG & ENFORCEMENT RULES
**Project:** `ofi-lab` (working fork of `polymarket-ofi`)
**Golden Goose Archive:** `freeze-20260428/`
**Rule:** All changes must be logged here BEFORE they are executed.

---

## THE PRIME DIRECTIVE

> **The only permitted changes to the `ofi-lab` system are those that allow it to
> retrain a model using the exact same architecture as `h300_v1`, on updated data.**
>
> Any change that alters the feature schema (33 columns), the model algorithm
> (LightGBM), the hyperparameters, or the training pipeline constitutes a NEW
> EXPERIMENT and must be branched as a separate named experiment, NOT merged
> into `ofi-lab` without explicit sign-off.

---

## ENFORCEMENT RULES

### Rule 1 — The Frozen Archive is Immutable
- The `freeze-20260428/` directory and its contents are READ-ONLY.
- No file in `freeze-20260428/` may be modified, deleted, or overwritten.
- Container tag `golden-goose-h300v1:frozen-20260428` may never be replaced
  or retagged.
- Enforcement: set the freeze directory to read-only immediately after creation:
  ```bash
  chmod -R a-w ~/Code/DirectionPredictionSystem/freeze-20260428/
  ```

### Rule 2 — No Direct Edits to Production Container
- The running container `polymarket-ofi-paper-trader` must NEVER be modified
  while it is running (no `docker exec` edits to source files).
- All changes are made in `ofi-lab/` and deployed as a new named container.

### Rule 3 — All Changes Are Logged Before Execution
- Every change to `ofi-lab/` must have a corresponding entry in the
  CHANGE LOG section below, with: date, author, what changed, why, and
  what tests must pass before it can be merged.
- No undocumented changes. If you made a change and forgot to log it,
  add a retroactive entry immediately and flag it as ⚠️ RETROACTIVE.

### Rule 4 — Feature Schema is Sacred
- The 33-column feature schema in `feature_names.json` MUST NOT change
  unless explicitly creating a new feature version (e.g., `features_v4/`).
- If a new feature version is created, it must be a separate pipeline output
  directory and MUST NOT overwrite `features_v3/`.
- The training command for h300_v1 replication is:
  ```
  python validation/run_training.py --horizon 300 --feature-dir /data/features_v3
  ```
  This exact command, with `--feature-dir /data/features_v3`, is the canonical
  replication command. Any deviation must be logged and justified.

### Rule 5 — Model Versioning Convention
- Retrained models that use the same architecture = `h300_v1_retrain_{YYYYMMDD}`
- Models using new feature versions = `h300_v4_{YYYYMMDD}` (skip v2, v3)
- The name `h300_v2` and `h300_v3` are PERMANENTLY RETIRED.
  - `h300_v2` was never created.
  - `h300_v3` was created but failed its gate and was never deployed.
    It must not be confused with a future successor.
- All new model runs must write to `/data/models_lab/` — NEVER to `/data/models/`.

### Rule 6 — TDD Gate Before Any Deployment
- Every new model must pass the test suite in `tests/` before it can be
  promoted from paper trading to live deployment.
- Minimum required tests:
  1. `test_feature_schema.py` — verifies output of build_features_v3.py
     produces exactly 33 feature columns matching `feature_names.json`
  2. `test_model_determinism.py` — verifies that training with the same
     data, same params, and random_state=42 produces the same MD5 hash
  3. `test_gate_behavior.py` — verifies that the confidence gate correctly
     filters trades (no trades should be logged below the gate threshold)
  4. `test_suppress_durations.py` — verifies `H300_SUPPRESS_DURATIONS`
     prevents h300 from firing on 300s contracts
  5. `test_pipeline_integrity.py` — end-to-end: raw orderbook parquet →
     features_v3 → model prediction (smoke test, no regressions)

### Rule 7 — No Silent Failures
- Every pipeline script must validate its output schema before exiting.
- If `build_features_v3.py` produces a file with != 34 columns (33 + cts),
  it must raise an exception, not silently write a malformed file.
- Add schema validation assertions to all three pipeline steps.

### Rule 8 — H300_SUPPRESS_DURATIONS Must Be Preserved
- The line `H300_SUPPRESS_DURATIONS = {"h300": {300}}` in `paper_trader.py`
  must never be removed or changed.
- This is the configuration that makes h300 work on 900s contracts.
- Any change to this line requires explicit sign-off and a 48-hour
  paper trading comparison before deployment.

---

## CHANGE LOG

| # | Date | Author | File(s) Changed | What Changed | Why | Tests Required | Approved By |
|:--|:-----|:-------|:----------------|:-------------|:----|:---------------|:------------|
| 001 | 2026-04-28 | — | `requirements.txt` | Replaced `py-clob-client>=0.18` with `py-clob-client-v2>=1.0.0` | Polymarket CLOB V2 mandatory migration (V1 deprecated 2026-04-28 11:00 UTC) | test_clob_v2_smoke.py | Pending |
| 002 | 2026-04-28 | — | `api/polymarket.py` | Updated client init to use `py_clob_client_v2.ClobClient`, `create_or_derive_api_key()`, and `creds=` pattern | CLOB V2 migration | test_clob_v2_smoke.py | Pending |
| 003 | 2026-04-28 | — | `trading/polymarket_discovery.py` | Updated imports to `py_clob_client_v2`, updated read-only client instantiation | CLOB V2 migration | test_clob_v2_smoke.py | Pending |
| 004 | TBD | — | `validation/run_training.py` | TRAIN_END = "2026-02-28", VAL_END = "2026-04-01" | Retrain Model A (Shifted Window) on updated market regime | All 5 TDD gates | Pending |
| 005 | TBD | — | `validation/run_training.py` | TRAIN_END = "2026-03-15", VAL_END = "2026-04-15" | Retrain Model B (Extended Window) | All 5 TDD gates | Pending |

*(Add new rows to the bottom of this table. Never delete rows.)*

---

## EXPERIMENT LOG

Separate from routine changes — use this for anything that modifies the
architecture, features, or model type.

| Exp # | Date | Branch Name | Hypothesis | Feature Version | Architecture | Status |
|:------|:-----|:------------|:-----------|:----------------|:-------------|:-------|
| EXP-001 | TBD | `exp/h300-v4-features` | Adding bid/ask depth imbalance at levels 5/10/20 improves edge | `features_v4/` (34 cols) | LightGBM (same params) | Planned |

---

## RETIRED MODEL NAMES (DO NOT REUSE)

| Name | Reason Retired |
|:-----|:---------------|
| `h300_v2` | Never created — name skipped |
| `h300_v3` | Created 2026-03-26, AUC_contract=0.5174, failed gate, never deployed |
| `h60_v2` | Overconfident when wrong — decommissioned 2026-04-27 |
| `btc900s-trader` (container) | Blew up from no confidence gate ($10 → $0.98) |
