## Fleet Training Pipeline Fix — 2026-05-23

### Problem Discovered

All 84 weekly fleet models were failing with `ValueError: Found array with 0 sample(s) (shape=(0, 32))` at `run_training.py:412`. The root cause: `--train-end 2026-05-19` was too close to "today", pushing the val window (May 20–29) and test window (May 30–Jun 3) into future dates where no feature data existed. Feature data only went through `2026-05-20`, so the test set was empty, causing sklearn to crash.

The previous successful fleet (May 13) used `--train-end 2026-04-27` with val=10d/test=5d, which kept all windows within available data. But as the weeks progressed, no one adjusted `--train-end` backward, so the windows drifted past the data frontier.

### Plan

1. **Auto-derive `train_end`** from available feature data instead of hardcoding it. Formula: `train_end = latest_feature_date - val_days - test_days - buffer_days`. This ensures val+test windows always fall within existing data.
2. **Add defensive `empty_test` guard** in `run_training.py` — if test set is somehow still empty, save the model but mark `gate_passed=False` and skip SHAP/calibration plots.
3. **Add preflight validation** in `train_fleet.py` to verify feature data coverage before launching 84 jobs.
4. **Kill stuck processes**, **reset stale state.json**, and **re-deploy** to VPS.

### Execution

| Step | What | Result |
|------|------|--------|
| 1 | Added `discover_latest_feature_date()` and `resolve_train_end()` to `train_fleet.py` | Auto-derives `train_end=2026-05-06` from data through `2026-05-21` |
| 2 | Made `--train-end` optional (defaults to auto-derive), added `--buffer-days` flag | Backward compatible; manual override still works with warning |
| 3 | Added `empty_test` guard in `run_training.py:train_final_model()` | Empty test → saves model, `gate_passed=False`, all test metrics `None` |
| 4 | Fixed indentation bugs in calibration `try` block (0-indent → 4-indent) and SHAP `else` block | Syntax errors resolved |
| 5 | **Critical bug fix**: `train_fleet.py:335` passed `args.train_end` (None) instead of resolved `train_end` | All 84 models now receive correct `--train-end 2026-05-06` |
| 6 | Killed stuck fleet PIDs (253484/253485/259217/259218) on VPS | Cleared doomed processes |
| 7 | Reset stale `state.json` (had bogus "completed" entries from failed run) | Clean state for re-run |
| 8 | rsync'd fixes to VPS, cleared `__pycache__`, chmod'd scripts | Deployed |
| 9 | Launched fleet: `python -m scripts.train_fleet --parallel 1` (no `--train-end`) | **84/84 completed, 0 failed** |

### Key Files Changed

- `scripts/train_fleet.py`: auto-derive logic, `--buffer-days`, preflight validation, bug fix on line 335
- `validation/run_training.py`: `empty_test` guard, indentation fixes for calibration + SHAP blocks

### Architecture Decision

`discover_latest_feature_date()` finds the latest date common to **ALL** symbols (not per-symbol max), ensuring consistent train/val/test splits across the entire fleet.
