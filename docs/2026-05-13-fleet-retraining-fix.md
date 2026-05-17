# Fleet Retraining Fix — 2026-05-13

## Summary

Diagnosed and fixed a critical training pipeline bug where all 84 fleet models were trained on cross-symbol contaminated data (all 4 symbols instead of 1 per model). Retrained 84 genuinely unique single-symbol models and deployed them to the VPS.

## Problem

The original 84 fleet models (trained 2026-05-11) were all effectively identical within each horizon — only 7 unique models existed despite having 84 model directories. Root cause: `load_features()` loaded ALL 4 symbols' parquet files (~2.1M rows) regardless of which `--symbol` was passed, making every BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT model see the same multi-symbol dataset.

### Evidence

`metrics.json` from the original training showed `train_size=2100031` for a BTCUSDT model — that's ~525K rows × 4 symbols. A manual test confirmed that after the fix, `load_features(symbol="BTCUSDT")` returns only ~545K rows.

## Bugs Fixed

### Bug 1: `load_features()` date filter not wired into main flow

`load_features()` already accepted `start_date`/`end_date` params (added in a prior session) but `main()` in `run_training.py` never passed them. This meant the training pipeline loaded all 379 parquet files per symbol even when only a 90-day window was needed.

**Fix:** `validation/run_training.py:705-708` — pass `start_date=train_start` and `end_date=load_end` (or `None` if no `--test-end`) to `load_features()`.

### Bug 2: `VAL_END` module constant used instead of local `val_end` in test split

Line 736: `df_test = df[df["date"] > VAL_END]` used the hardcoded constant `"2026-02-15"` instead of the CLI-provided `val_end`. This meant the test set was always split at the same date regardless of `--val-end`.

**Fix:** Changed to `df["date"] > val_end` (the local variable resolved from CLI args).

### Bug 3: `config_snapshot.json` used module constants `TRAIN_END`/`VAL_END`

Lines 541-542 in `train_final_model()` used `TRAIN_END` and `VAL_END` module-level constants instead of the actual dates used for training. This made `config_snapshot.json` always show `"2025-12-31"` / `"2026-02-15"` regardless of what `--train-end` / `--val-end` were passed.

**Fix:** Added `val_end_date` parameter to `train_final_model()`. Config snapshot now uses `train_window_end or TRAIN_END` and `val_end_date or VAL_END`, reflecting the actual CLI values.

### Bug 4: `train_days_val` computation crashed if `--train-start` not provided

Lines 763-765 unconditionally did `pd.to_datetime(args.train_start)` which would crash with `None`. The `--train-start` flag is optional (defaults to None for full-history training).

**Fix:** Three-way fallback: (1) compute from `--train-start`/`--train-end` if both provided, (2) use `--train-days` if provided, (3) derive from actual data date range as last resort.

### Bug 5: `load_end` filter excluded test data

When `--test-end` was not provided, the code set `load_end = val_end`, which meant `load_features()` only loaded files up to `val_end`. The test split (`df["date"] > val_end`) then found zero test rows, causing a crash in `model.predict_proba()`.

**Fix:** Set `load_end = args.test_end if args.test_end else None` — when no explicit test-end, load all available data and let the date-based split handle it.

## Deployment Steps

1. **Fixed all 5 bugs** in `validation/run_training.py` locally
2. **Rsync'd to VPS** (excluding `.venv` to avoid python3.12/3.13 mismatch)
3. **Cleared `__pycache__`** on VPS
4. **Verified single-model training** end-to-end on VPS — confirmed:
   - Only 97/379 files loaded (date-filtered)
   - 139K rows for BTCUSDT 90d (not 2.1M)
   - `metrics.json` shows `train_size=138125`, `symbol=BTCUSDT`
   - `config_snapshot.json` shows correct `TRAIN_END`/`VAL_END`
5. **Deleted all 84 old model directories** from `/data/models/fleet/`
6. **Cleared `model_registry` table** (85 rows → 0)
7. **Reset `state.json`** for fresh training
8. **Ran fleet training** with `--parallel 1` (avoids OOM on 16GB VPS):
   ```bash
   .venv/bin/python3 scripts/train_fleet.py \
     --train-end 2026-04-27 --val-days 10 --test-days 5 \
     --feature-dir /data/features_v3 --output-root /data/models/fleet \
     --db /data/v3.db --evaluation-windows 300,900,1800 \
     --parallel 1 --state /data/models/fleet/state.json
   ```
9. **84/84 completed, 0 failures** (~45 min total)
10. **Cleared stale DB data** (predictions, paper_trades, decision_traces, calibration tables, pending queue)
11. **Restarted paper trader** — confirmed all 84 models loaded, predictions flowing

## Verification

### Model uniqueness confirmed

Each symbol now has genuinely different training data:
- `h300_btc_v3_90d`: `train_size=143881`
- `h300_btc_v3_180d`: `train_size=273391`
- `h300_btc_v3_330d`: `train_size=489239`

All are BTCUSDT-only (not 2.1M multi-symbol).

### Registry populated correctly

84 rows in `model_registry`, all `paper_active=1`. Model names follow the pattern `h{horizon}_{sym}_v3_{days}d` where `{days}` is off-by-one from the directory name (89/179/329 vs 90/180/330) due to `timedelta` calculation in `retrain.py`.

### Predictions flowing

Paper trader loaded all 84 models at startup. Each 5-minute boundary produces 84 new predictions and resolves prior predictions. Resolution pipeline working end-to-end.

## Key Files Changed

| File | Change |
|---|---|
| `ofi-lab-v3/validation/run_training.py` | 5 bug fixes (see above) |
| `vps_deployment_guide.md` | Updated for 3-service arch, `.venv` exclusion, fleet training, DB management |

## Lessons Learned

1. **Always exclude `.venv` from rsync.** Local python3.13 venv overwrote VPS python3.12 venv, breaking all services. Recovery requires manual venv recreation.
2. **Use `--parallel 1` for fleet training.** 330d models load ~500K rows (~1GB RAM each). 2+ workers trigger OOM killer on 16GB VPS (exit status 247).
3. **The `--test-end` param affects data loading.** When omitted, `load_features()` must load beyond `val_end` to provide test data — the `end_date` filter should be `None`, not `val_end`.
4. **Module-level constants vs CLI args.** The `TRAIN_END`/`VAL_END` constants at lines 58-59 are defaults only — the actual dates come from CLI args. Using the constants directly in split logic or config snapshots silently ignores user-provided dates.
5. **`load_features` date filtering is critical for memory.** Without it, every model loads 379 days of parquet files even for a 90-day window. With it, only the relevant files are loaded (e.g. 97/379 files for a 90-day window).
