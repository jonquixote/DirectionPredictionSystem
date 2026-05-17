# Fleet Retraining — TDD Execution Plan

> **Goal:** Surgically fix the two data-loading bugs in `run_training.py` that caused all 84 fleet models to train on identical data, while preserving the LightGBM training mechanics untouched.

**Date:** 2026-05-12  
**Approach:** Test-Driven Development — write failing tests first, then make the minimal code change to pass them.

---

## Data Available on VPS
- **BTCUSDT:** 365 days (2025-04-29 → 2026-04-28)
- **ETHUSDT:** 365 days (2025-04-29 → 2026-04-28)
- **SOLUSDT:** 365 days (2025-04-29 → 2026-04-28)
- **XRPUSDT:** 365 days (2025-04-29 → 2026-04-28)

---

## Bug #1: `load_features()` ignores `--symbol`

**Root Cause:** `load_features(feature_dir)` iterates over the hardcoded `SYMBOLS` list and concatenates all symbols into one DataFrame. The `--symbol BTCUSDT` argument is accepted by the CLI but never passed to this function.

**Surgical Fix:** Add an optional `symbol` parameter. When provided, load only that symbol's directory. When `None`, retain the original multi-symbol behavior (backward compatible).

### Test 1a: `test_load_features_single_symbol`
- Create a tmp directory with two symbol subdirs (`BTCUSDT/`, `ETHUSDT/`), each containing a small parquet file with distinct `symbol` column values.
- Call `load_features(tmp_dir, symbol="BTCUSDT")`.
- Assert the returned DataFrame contains ONLY `BTCUSDT` rows.
- Assert no `ETHUSDT` rows are present.

### Test 1b: `test_load_features_all_symbols_default`
- Same fixture as 1a.
- Call `load_features(tmp_dir)` (no symbol argument).
- Assert the returned DataFrame contains rows from ALL symbols.
- This confirms backward compatibility is preserved.

---

## Bug #2: Train split ignores `--train-start`

**Root Cause:** In `main()`, the train split is `df[df["date"] <= train_end]` — it never applies `train_start` as a lower bound, so 90-day and 330-day models see identical data.

**Surgical Fix:** When `train_start` is provided, add `df["date"] >= train_start` to the filter.

### Test 2a: `test_train_split_respects_start_date`
- Create a DataFrame with `cts` values spanning 2025-06-01 to 2025-12-31 (multi-month).
- Apply the split with `train_start="2025-10-01"` and `train_end="2025-12-31"`.
- Assert the resulting `df_train` contains ONLY rows on or after 2025-10-01.
- Assert rows from 2025-06-01 through 2025-09-30 are excluded.

### Test 2b: `test_train_split_no_start_loads_all`
- Same DataFrame fixture.
- Apply the split with `train_start=None` and `train_end="2025-12-31"`.
- Assert the resulting `df_train` contains ALL rows up to 2025-12-31 (backward compatible).

---

## Bug #3: `main()` doesn't filter the DataFrame by symbol

**Root Cause:** Even after `load_features()` loads all symbols, `main()` never filters the DataFrame to keep only the requested `--symbol`. The entire multi-symbol blob goes into LightGBM.

**Surgical Fix:** After `load_features()`, if `args.symbol` is set, filter `df = df[df["symbol"] == args.symbol]`.

### Test 3a: `test_main_filters_df_by_symbol`
- Confirm that when `--symbol BTCUSDT` is passed, only BTCUSDT rows survive into `df_train`.

---

## Bug #4: `train_fleet.py` doesn't pass `--walk-forward` flag

**Root Cause:** `train_one()` always appends `--skip-wf`. There's no way to enable walk-forward validation from the fleet driver.

**Surgical Fix:** Add an optional `walk_forward` parameter to `train_one()`. When `True`, don't append `--skip-wf`.

### Test 4a: `test_train_one_skip_wf_default`
- Mock subprocess, call `train_one(...)` with default args.
- Assert `--skip-wf` is in the subprocess command.

### Test 4b: `test_train_one_walk_forward_enabled`
- Mock subprocess, call `train_one(..., walk_forward=True)`.
- Assert `--skip-wf` is NOT in the subprocess command.

---

## Execution Results

### Step 1: Write Tests (RED phase) ✅ DONE
Created `tests/test_fleet_data_isolation.py` with 6 tests.
Initial run: **3 FAILED, 3 PASSED** — confirming bugs #1 and #4 exist in current code.

### Step 2: Fix `load_features()` (GREEN phase — Bug #1) ✅ DONE
- Added `symbol: str | None = None` parameter to `load_features()`.
- When `symbol` is provided, only iterates over `[symbol]` instead of `SYMBOLS`.
- Also fixed `validate_data()` to iterate `symbols_in_data` instead of hardcoded `SYMBOLS`.

### Step 3: Fix `main()` symbol filter + train_start (GREEN phase — Bugs #2, #3) ✅ DONE
- `main()` now passes `args.symbol` to `load_features(symbol=args.symbol)`.
- Train split now enforces `train_start` lower bound when provided:
  ```python
  if train_start:
      df_train = df[(df["date"] >= train_start) & (df["date"] <= train_end)]
  else:
      df_train = df[df["date"] <= train_end]  # backward compat
  ```

### Step 4: Fix `train_one()` walk-forward toggle (GREEN phase — Bug #4) ✅ DONE
- Added `walk_forward: bool = False` to `train_one()` signature.
- `--skip-wf` only appended when `walk_forward is False`.
- Added `--walk-forward` CLI flag to `train_fleet.py` `main()`, wired through to `train_one()`.

### Step 5: Deploy to VPS ✅ DONE
Files deployed:
- `validation/run_training.py`
- `scripts/train_fleet.py`
- `tests/test_fleet_data_isolation.py`

### Verification
**Single-symbol dry-run (BTCUSDT only):**
```
Loading features from /data/features_v3 (symbol=BTCUSDT)
  BTCUSDT: 365 files
Total rows loaded: 525034   ← was 2,100,055 when loading all 4 symbols
```

**Backward-compat dry-run (no --symbol):**
```
Loading features from /data/features_v3 (symbol=ALL)
  BTCUSDT: 365 files
  ETHUSDT: 365 files
  SOLUSDT: 365 files
  XRPUSDT: 365 files
Total rows loaded: 2100055   ← unchanged, backward compat preserved
```

**All 6 tests pass locally:**
```
tests/test_fleet_data_isolation.py   6 passed in 1.08s
```

---

## Summary of Changes

| File | Change | Lines Changed |
|------|--------|---------------|
| `validation/run_training.py` | `load_features()` accepts `symbol` param; `validate_data()` iterates actual symbols; train split enforces `train_start`; `main()` passes `--symbol` to loader | ~25 lines |
| `scripts/train_fleet.py` | `train_one()` accepts `walk_forward` param; conditional `--skip-wf`; `--walk-forward` CLI flag added | ~10 lines |
| `tests/test_fleet_data_isolation.py` | New test file with 6 TDD tests | 170 lines |

