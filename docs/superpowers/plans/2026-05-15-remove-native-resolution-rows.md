# Plan E: Remove Native Resolution Rows + Align Evaluation Rows to Market Windows

## Problem

Paper trader produces 300 prediction rows per 5-min boundary (84 native + 216 evaluation) at EVERY boundary regardless of alignment. Expected:
- **:05, :10, :20, :25** → 84 rows (300s eval only)
- **:15, :45** → 168 rows (300s + 900s eval)
- **:00, :30** → 252 rows (300s + 900s + 1800s eval)

## Design Decisions (Confirmed)

- ALL prediction rows will be `resolution_type='evaluation'` — no native rows at all
- Every model gets evaluation rows at ALL aligned windows in `[300, 900, 1800]` regardless of its `training_horizon_seconds`
- Alignment gate: emit eval row for window `w` only if `(boundary_ms // 1000) % w == 0`
- 300s eval row is the canonical/primary row — its prediction_id is returned by `_emit_prediction_rows()`
- Suppressed/gated trades: REMOVE `log_paper_trade()` calls (lines 1520, 1578) — compact decision already records the outcome
- Executed trades: fix `market_window_seconds` from `meta["training_horizon_seconds"]` → `duration`
- Decay metrics: filter `resolution_type='evaluation'` (all windows, per (model, symbol, window) grouping)
- Deployment order: deploy new code FIRST, verify row counts, THEN clean stale native data

## Risk Assessment (from subagent analysis)

### Confirmed Safe (no breakage expected)
- `_check_trade_resolutions_v3()` — no resolution_type filter
- Kalshi dispatch — no resolution_type reference
- Filter pipeline / EV / stake computation — pure functions, no DB queries on resolution_type
- Dashboard API / sqlite_store — no resolution_type filter on reads
- `live_eligibility.py` — counts by model, no resolution_type filter
- FK integrity — paper_trades.prediction_id will point to valid 300e eval rows
- Pending queue — uses prediction_id as opaque key, doesn't parse suffixes
- CHECK constraint — `IN ('native','evaluation')` still accepts 'evaluation'
- `calibration_outcomes` table — write-only in production (zero SELECT queries in prod code)

### Critical (will crash or break if not addressed)
- `sqlite_ledger.py:120` — `assert native_id is not None` will crash
- `calibrator_registry.py:105,113,141` — SQL filter `'native'` returns zero rows → calibration never refits
- `paper_trader.py:738,750` — decay metric queries return zero rows → lifecycle FSM gets stale metrics
- `paper_trader.py:514` — resolution routing branches on `"native"` → calibration_outcomes never written

### Medium (data integrity / performance)
- 3 dead partial indexes on `resolution_type='native'` need replacement
- 2 plain indexes on `resolution_type` become single-value (useless)
- DELETE order: paper_trades rows must be deleted BEFORE linked prediction rows (FK constraint)
- VACUUM + ANALYZE needed after bulk deletion
- Duplicate UPDATE bug in both `record_native_resolution()` and `record_evaluation_resolution()` (same UPDATE executed twice)

---

## Tasks

### Task 1: Refactor `window_planner.py` — remove native rows, add alignment gate

**File:** `ofi-lab-v3/storage/window_planner.py`

Changes:
- Remove the native row emission (lines 30–36: the `ResolutionRow` with `resolution_type="native"`)
- Remove the `if w == training_horizon_seconds: continue` skip (lines 38–39) — all models get all aligned evaluation windows
- Add alignment gate: for each `w` in `evaluation_windows`, emit eval row only if `(boundary_ms // 1000) % w == 0`
- `training_horizon_seconds` param becomes unused for row emission — keep it in the signature for API compat (or remove if no external callers pass it meaningfully)
- Update docstring (lines 1–9): replace native-centric description with evaluation-only + alignment
- Update `ResolutionRow.resolution_type` comment (line 19): remove `'native'` from the union

Expected behavior:
- Boundary at 300s-aligned-only (e.g., :05): returns `[ResolutionRow(300, "evaluation", ...)]`
- Boundary at 900s-aligned (e.g., :15): returns `[ResolutionRow(300, "evaluation", ...), ResolutionRow(900, "evaluation", ...)]`
- Boundary at 1800s-aligned (e.g., :00): returns `[ResolutionRow(300, "evaluation", ...), ResolutionRow(900, "evaluation", ...), ResolutionRow(1800, "evaluation", ...)]`

Verification:
- `pytest tests/test_window_planner.py` (after Task 9 updates)

### Task 2: Refactor `sqlite_ledger.py` — unify resolution methods, fix bugs

**File:** `ofi-lab-v3/storage/sqlite_ledger.py`

#### 2a: `log_prediction_set()` (lines 70–121)
- Remove `native_id` tracking (line 76: `native_id: Optional[str] = None`, line 80–81: `if row.resolution_type == "native": native_id = pid`)
- Remove `assert native_id is not None` (line 120) — will crash with no native rows
- Return the **first row's prediction_id** as the canonical ID (always the 300s eval row since `EVALUATION_WINDOWS = [300, 900, 1800]` and 300 is always first/aligned)
- Update docstring (line 70–73): remove "native row's prediction_id" language

#### 2b: Merge `record_native_resolution()` + `record_evaluation_resolution()` into `record_resolution()`
- Create a single `record_resolution()` method with signature:
  ```python
  def record_resolution(
      self,
      *,
      prediction_id: str,
      ts_resolved_ms: int,
      price_at_open: float,
      price_at_close: float,
      contract_result: str,
      prediction_correct: bool,
      feed_calibrator: bool = False,
  ) -> None:
  ```
- If `feed_calibrator=True`: write to `calibration_outcomes` with `resolution_type='evaluation'` (not `'native'`)
- If `feed_calibrator=False`: just resolve the row, no calibration_outcomes insert
- Remove the `resolution_type != "native"` / `!= "evaluation"` ValueError guards (lines 254–258, 310–313)
- Fix the duplicate UPDATE bug: both old methods execute the same `UPDATE predictions SET resolved=1...` twice (lines 259–266 + 268–276, and lines 315–322 + 324–332). The merged method executes it once.
- Remove both `record_native_resolution()` and `record_evaluation_resolution()` entirely

#### 2c: Update `calibration_outcomes` insert
- Line 286: change hardcoded `"native"` → `"evaluation"` (or read from the prediction row if available)
- The `calibration_outcomes` table has no CHECK constraint on `resolution_type`, so `'evaluation'` is valid

### Task 3: Refactor `paper_trader.py` — resolution routing, trade logging, decay metrics

**File:** `ofi-lab-v3/trading/paper_trader.py`

#### 3a: `_emit_prediction_rows()` (lines 428–482)
- No functional change needed — `log_prediction_set()` already returns the first row's ID (now 300e instead of native)
- Rename `native_pid` → `canonical_pid` (lines 442, 466, 482) for clarity
- Update docstring (line 429–431): remove "native row's prediction_id"

#### 3b: `_check_prediction_resolutions_v3()` (lines 484–547)
- Remove the `if entry.resolution_type == "native"` / `else` branch (lines 514–545)
- Replace with: always call `self.sqlite_ledger.record_resolution()`
- Pass `feed_calibrator=True` when `entry.market_window_seconds == 300` (the canonical 300s eval row)
- Pass `feed_calibrator=False` for 900s/1800s eval rows
- Move the calibrator `.record_outcome()` call (lines 525–536) INSIDE the `feed_calibrator=True` path, or keep it as a separate step after `record_resolution()` — either way, only for 300s eval rows, only when not warmup

#### 3c: Remove suppressed trade `log_paper_trade()` calls (lines 1520, 1578)
- Line 1520–1532: UTC blackout suppression — remove entire `self.sqlite_ledger.log_paper_trade(...)` block
- Line 1578–1594: filter-pipeline suppression — remove entire `self.sqlite_ledger.log_paper_trade(...)` block
- The `_record_compact_decision()` calls at lines 1465 and 1518 already record the outcome on the prediction row — that's sufficient
- Also remove the `if above_threshold:` guard around the line 1520 call (line 1518–1532 block), since compact decision already handles this

#### 3d: Fix executed trade `log_paper_trade()` (lines 1665–1688)
- Line 1669: change `market_window_seconds=meta["training_horizon_seconds"]` → `market_window_seconds=duration`
- Line 1670: change `resolution_type="native"` → `resolution_type="evaluation"`

#### 3e: Fix decay metrics queries (lines 735–752)
- Line 738: change `WHERE resolution_type = 'native'` → `WHERE resolution_type = 'evaluation'`
- Line 750: change `AND resolution_type = 'native'` → `AND resolution_type = 'evaluation'`
- No market_window_seconds restriction — decay computed per (model, symbol, window)

### Task 4: Update `calibrator_registry.py` SQL filters

**File:** `ofi-lab-v3/storage/calibrator_registry.py`

- Line 105: change `resolution_type='native'` → `resolution_type='evaluation'`
- Line 113: change `resolution_type='native'` → `resolution_type='evaluation'`
- Line 141: change `resolution_type='native'` → `resolution_type='evaluation'`
- Update docstrings/comments that mention "native" (lines 3, 33, 102, 129, 219)

### Task 5: Update `scripts/backfill_calibration.py`

**File:** `ofi-lab-v3/scripts/backfill_calibration.py`

- Line 75: change `AND resolution_type = 'native'` → `AND resolution_type = 'evaluation'`

### Task 6: Update `scripts/migrate_jsonl_to_sqlite.py`

**File:** `ofi-lab-v3/scripts/migrate_jsonl_to_sqlite.py`

- Line 104: change `"native"` → `"evaluation"` (predictions insert)
- Line 158: change `"native"` → `"evaluation"` (paper_trades insert)

### Task 7: Update `schema.sql` — replace dead partial indexes

**File:** `ofi-lab-v3/storage/schema.sql`

- Lines 90–91: Replace `idx_pred_native_for_decay` with `idx_pred_eval_for_decay`:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_pred_eval_for_decay ON predictions(model_name, symbol, resolution_type, ts_contract_open_ms)
  WHERE resolution_type = 'evaluation' AND resolved = 1;
  ```
- Lines 164–165: Replace `idx_trade_native_for_decay` with `idx_trade_eval_for_decay`:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_trade_eval_for_decay ON paper_trades(model_name, symbol, resolution_type, ts_contract_open_ms)
  WHERE resolution_type = 'evaluation' AND resolved = 1;
  ```
- Line 222: Replace `idx_cal_native` with `idx_cal_eval`:
  ```sql
  CREATE INDEX IF NOT EXISTS idx_cal_eval ON calibration_outcomes(model_name, resolution_type) WHERE resolution_type = 'evaluation';
  ```
- Lines 88, 163: The plain `idx_pred_resolution_type` and `idx_trade_resolution_type` indexes become single-value. Leave them for now (harmless, just unused).

### Task 8: Add DB migration for index rebuild on VPS

**File:** `ofi-lab-v3/storage/db.py`

- Add migration in `_run_migrations()` to:
  1. `DROP INDEX IF EXISTS idx_pred_native_for_decay`
  2. `DROP INDEX IF EXISTS idx_trade_native_for_decay`
  3. `DROP INDEX IF EXISTS idx_cal_native`
  4. `CREATE INDEX IF NOT EXISTS idx_pred_eval_for_decay ...`
  5. `CREATE INDEX IF NOT EXISTS idx_trade_eval_for_decay ...`
  6. `CREATE INDEX IF NOT EXISTS idx_cal_eval ...`
- This runs automatically on next DB open, no manual SQL needed

### Task 9: Update all test files

**Files:** `ofi-lab-v3/tests/` (15 files, 46 `native` references)

#### 9a: `tests/test_window_planner.py`
- Rewrite: remove native row assertions, add alignment gating tests
- Test boundary 300s-only → 1 row (300s eval)
- Test boundary 900s-aligned → 2 rows (300s + 900s eval)
- Test boundary 1800s-aligned → 3 rows (300s + 900s + 1800s eval)
- Test that no row has `resolution_type="native"`

#### 9b: `tests/test_sqlite_ledger.py`
- Line 63: `assert by_window[900]["resolution_type"] == "native"` → `== "evaluation"`
- Lines 70–71: Remove native-row selection logic, use first row as canonical
- Line 150: `resolution_type="native"` → `"evaluation"` in `log_paper_trade()` call
- Line 234: `assert cal["resolution_type"] == "native"` → `== "evaluation"`
- Line 274: `resolution_type="native"` → `"evaluation"` in `log_paper_trade()` call
- Update `record_native_resolution`/`record_evaluation_resolution` calls → `record_resolution`
- Fix test for `test_native_resolution_writes_calibration_outcome` → test that 300s eval row with `feed_calibrator=True` writes calibration_outcomes

#### 9c: `tests/test_pending_queue.py`
- Lines 17, 36, 54, 67, 81: `resolution_type="native"` → `"evaluation"`

#### 9d: `tests/test_model_registry.py`
- Lines 172, 179: `resolution_type="native"` → `"evaluation"`

#### 9e: `tests/test_calibrator_registry.py`
- Line 36: default `resolution_type: str = "native"` → `"evaluation"`

#### 9f: `tests/test_calibrator_wiring.py`
- Lines 74, 263: `"native"` → `"evaluation"`

#### 9g: `tests/test_eligibility_progress.py`
- Lines 36, 56: `"native"` → `"evaluation"`

#### 9h: `tests/test_preflight_v3.py`
- Lines 81, 104: `"native"` → `"evaluation"`

#### 9i: `tests/test_backfill_calibration.py`
- Lines 75, 119: `"native"` → `"evaluation"`

#### 9j: `tests/test_decay_refresh.py`
- Lines 32, 51: `"native"` → `"evaluation"`

#### 9k: `tests/test_replay_smoke.py`
- Line 27: `WHERE resolution_type='native'` → `WHERE resolution_type='evaluation'`

#### 9l: `tests/test_paper_trader_scoring.py`
- Line 34: `assert by_w[900]["resolution_type"] == "native"` → `== "evaluation"`
- Line 64: `assert by_w[60]["resolution_type"] == "native"` → `== "evaluation"`

#### 9m: `tests/test_migration.py`
- Line 25: `assert p["resolution_type"] == "native"` → `== "evaluation"`

#### 9n: `tests/test_paper_trader_resolution.py`
- Rewrite `test_native_resolution_writes_calibration_outcome` → test that 300s eval row with `feed_calibrator=True` writes calibration_outcomes
- Rewrite `test_evaluation_resolution_does_not_write_calibration` → test that 900s/1800s eval rows with `feed_calibrator=False` do NOT write calibration_outcomes

### Task 10: Run full test suite

```bash
cd ofi-lab-v3 && python -m pytest tests/ -x -v
```

Fix any failures. Iterate until all tests pass.

### Task 11: Rsync code to VPS

```bash
rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
  ofi-lab-v3/ johnny@34.67.75.48:/home/johnny/ofi-lab-v3/
```

### Task 12: Clear pycache on VPS

```bash
ssh johnny@34.67.75.48 -i ~/.ssh/id_vps_n2 \
  "find /home/johnny/ofi-lab-v3/ -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; \
   find /home/johnny/ofi-lab-v3/ -name '*.pyc' -delete 2>/dev/null"
```

### Task 13: Restart paper-trader service on VPS

```bash
ssh johnny@34.67.75.48 -i ~/.ssh/id_vps_n2 \
  "sudo systemctl stop v3-paper-trader.service; \
   sleep 2; \
   sudo systemctl reset-failed v3-paper-trader.service 2>/dev/null; \
   sudo systemctl start v3-paper-trader.service"
```

Note: `stop` can take 90s due to SIGTERM timeout. If stuck, `kill -9` the process, then `start`.

### Task 14: Verify row counts at next boundary

- Check `/api/predictions` or query DB directly for the count of rows with `ts_contract_open_ms` at the most recent 5-min boundary
- Expected:
  - Non-aligned boundary (:05, :10, :20, :25): **84 rows** (all 300s eval)
  - 15-min boundary (:15, :45): **168 rows** (84 × 300s + 84 × 900s eval)
  - 30-min boundary (:00, :30): **252 rows** (84 × 300s + 84 × 900s + 84 × 1800s eval)
- Verify ALL rows have `resolution_type='evaluation'` — zero native rows
- Verify `/status` endpoint shows correct prediction counts

### Task 15: Backup VPS database before cleanup

```bash
ssh johnny@34.67.75.48 -i ~/.ssh/id_vps_n2 \
  "sqlite3 /data/v3.db '.backup /data/v3.db.pre-native-cleanup'"
```

### Task 16: Delete stale native rows from VPS DB

**Order matters**: paper_trades (child) before predictions (parent) due to FK constraint.

```bash
ssh johnny@34.67.75.48 -i ~/.ssh/id_vps_n2 \
  "sqlite3 /data/v3.db \"
    DELETE FROM paper_trades WHERE resolution_type = 'native';
    DELETE FROM predictions WHERE resolution_type = 'native';
    DELETE FROM calibration_outcomes WHERE resolution_type = 'native';
    DELETE FROM decision_traces WHERE prediction_id IN (
      SELECT prediction_id FROM predictions WHERE resolution_type = 'native'
    );
  \""
```

Note: The decision_traces DELETE is a safety measure — if any traces reference deleted prediction_ids, clean them too. This subquery may return zero rows since predictions are already deleted, but it's harmless.

### Task 17: VACUUM + ANALYZE on VPS DB

```bash
ssh johnny@34.67.75.48 -i ~/.ssh/id_vps_n2 \
  "sudo systemctl stop v3-paper-trader.service; \
   sleep 2; \
   sqlite3 /data/v3.db 'VACUUM; ANALYZE;' ; \
   sudo systemctl reset-failed v3-paper-trader.service 2>/dev/null; \
   sudo systemctl start v3-paper-trader.service"
```

VACUUM requires no concurrent connections — stop the paper-trader first, then restart after.

### Task 18: Final verification — post-cleanup

- Verify DB row counts: `SELECT resolution_type, COUNT(*) FROM predictions GROUP BY resolution_type;` — should show only `evaluation`
- Verify decay metrics are being written: `SELECT COUNT(*) FROM decay_metrics WHERE ts > datetime('now', '-15 minutes');`
- Verify calibration_outcomes are being written: `SELECT COUNT(*) FROM calibration_outcomes WHERE ts > datetime('now', '-15 minutes');`
- Verify lifecycle FSM is working: check model states in dashboard
- Verify dashboard shows correct prediction counts (84/168/252 instead of 300)

---

## Summary: 18 Tasks

| # | Task | Est. Complexity |
|---|------|----------------|
| 1 | Refactor `window_planner.py` | Low |
| 2 | Refactor `sqlite_ledger.py` | High |
| 3 | Refactor `paper_trader.py` | High |
| 4 | Update `calibrator_registry.py` | Low |
| 5 | Update `scripts/backfill_calibration.py` | Low |
| 6 | Update `scripts/migrate_jsonl_to_sqlite.py` | Low |
| 7 | Update `schema.sql` | Low |
| 8 | Add DB migration for index rebuild | Low |
| 9 | Update all test files (15 files) | Medium |
| 10 | Run full test suite | Low |
| 11 | Rsync code to VPS | Low |
| 12 | Clear pycache on VPS | Low |
| 13 | Restart paper-trader service | Low |
| 14 | Verify row counts at next boundary | Low |
| 15 | Backup VPS database | Low |
| 16 | Delete stale native rows | Low |
| 17 | VACUUM + ANALYZE | Low |
| 18 | Final verification | Low |

## Critical Path

Tasks 1–3 are the core refactor (must be done first, can be done in parallel).
Tasks 4–8 are simple string replacements (can be done in parallel with each other).
Task 9 depends on Tasks 1–8 (test structure must match new API).
Task 10 depends on Task 9.
Tasks 11–14 are deployment + verification (sequential).
Tasks 15–18 are cleanup + verification (sequential, after Task 14 confirms success).
