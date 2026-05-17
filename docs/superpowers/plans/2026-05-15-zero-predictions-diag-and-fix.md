# Plan D: Diagnose & Fix Zero Predictions + Secondary Bugs

## Problem
Paper trader produces 0 predictions per boundary despite:
- 84 fleet models loaded correctly
- Warmup completing (no "Skipping — not warmed up" messages after first boundary)
- Feature bars available (no "No 1-min bar" messages)
- DB `model_registry.symbol` values are correct (21 models per symbol, 4 symbols)
- ModelSelector strategy is "all" (no blocking)
- No exceptions logged ("Fatal error" never appears)
- Boundary loop running normally every 5 minutes

## Root Cause Investigation (Still Unknown)

### Hypotheses (in order of likelihood)
1. **Symbol guard silently skipping all models**: `meta.get("symbol")` at line 1339 returns a value that doesn't match the current `symbol` iteration. Despite DB having correct values, `self._model_meta` might be populated differently (e.g., different DB connection, stale .pyc, race condition).
2. **Model.predict() exception swallowed somewhere**: There might be a lightgbm exception on NaN/Inf features that somehow doesn't propagate. Unlikely since no try/except wraps the predict call.
3. **Feature bar has all-zero/NaN values**: `bar.get(col, 0.0)` returns 0.0 for all features, causing predict() to return exactly 0.5 for all models, which might trigger some silent skip.
4. **`self.models` dict is empty at runtime despite boot log**: Some post-boot cleanup empties the dict. Very unlikely.

### Diagnostic Logging Plan
Add INFO-level logs at these points in `_run_predictions()`:

```python
# After ModelSelector block (line ~1334):
logger.info("[DIAG] %s: candidates_by_sh=%d groups, blocked=%d", 
            symbol, len(_candidates_by_sh), len(blocked_models))

# Inside model loop, add counters:
_diag_symbol_guard_skip = 0
_diag_blocked_skip = 0  
_diag_predicted = 0

# At symbol guard (line 1339):
if meta.get("symbol") != symbol:
    _diag_symbol_guard_skip += 1
    continue

# At blocked check (line 1343):
if model_name in blocked_models:
    _diag_blocked_skip += 1
    continue

# After _emit_prediction_rows (line 1409):
_diag_predicted += 1

# At end of symbol loop, before moving to next symbol:
logger.info("[DIAG] %s: models=%d symbol_skip=%d blocked_skip=%d predicted=%d",
            symbol, len(self.models), _diag_symbol_guard_skip, _diag_blocked_skip, _diag_predicted)

# At end of method (line ~1735):
# Log first model's meta dict for inspection:
if self._model_meta:
    first_meta = next(iter(self._model_meta.values()))
    logger.info("[DIAG] first_model_meta=%s", first_meta)
```

## Step-by-Step Execution Plan

### Step 1: Add diagnostic logging to `paper_trader.py`
File: `ofi-lab-v3/trading/paper_trader.py`

In `_run_predictions()`, add diagnostic INFO logs at:
1. **Line ~1315** (after bar check, before ModelSelector): Log that symbol entered model scoring
2. **Line ~1334** (after ModelSelector): Log candidates count and blocked count
3. **Line ~1339** (symbol guard): Add counter instead of silent continue
4. **Line ~1343** (blocked check): Add counter instead of silent continue  
5. **Line ~1409** (after prediction emit): Add predicted counter
6. **Line ~1725** (before overlap recording): Log per-symbol diagnostics
7. **Line ~1735** (boundary completion): Log first model's metadata

### Step 2: Fix lifecycle.py `ts_ms` column error
File: `ofi-lab-v3/storage/lifecycle.py`

Line 148: Change `ORDER BY ts_ms DESC` to `ORDER BY ts DESC`

The `decay_metrics` table has column `ts` (TEXT, ISO 8601 strings), not `ts_ms` (integer).
This causes `sqlite3.OperationalError: no such column: ts_ms` every 16 boundaries.

### Step 3: Fix `/status` API endpoint crash
File: `ofi-lab-v3/trading/api_server.py`

Line 504-505: Remove references to non-existent attributes:
- `trader._pending_resolutions` → removed (was legacy)
- `trader._pending_pred_resolutions` → removed (was legacy)

Replace with pending queue length:
```python
"pending_resolutions": len(trader.pending_queue._queue) if hasattr(trader, 'pending_queue') else 0,
```

### Step 4: Deploy to VPS
```bash
# Rsync code
rsync -avz --exclude='.venv' --exclude='__pycache__' \
  ofi-lab-v3/ johnny@34.67.75.48:/home/johnny/ofi-lab-v3/

# Clear __pycache__ on VPS
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
  "find /home/johnny/ofi-lab-v3 -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; true"

# Restart paper trader
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
  "sudo systemctl restart v3-paper-trader.service"
```

### Step 5: Wait for warmup + 1 boundary, then check logs
```bash
# Wait ~35 minutes (30 min warmup + 5 min for first boundary)
# Then check:
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
  "journalctl -u v3-paper-trader.service --since '5 min ago' --no-pager"
```

Look for `[DIAG]` lines showing:
- `candidates_by_sh=N` — should be 7 groups (7 horizons per symbol)
- `blocked=N` — should be 0 (strategy is "all")
- `symbol_skip=N` — if this is 84, the symbol guard is the problem
- `predicted=N` — should be 21 (21 models per symbol)
- `first_model_meta=...` — shows actual metadata dict values

### Step 6: Based on diagnosis, implement fix
- If `symbol_skip=84` for all symbols → fix `_model_meta` symbol key
- If `blocked=84` → fix ModelSelector logic
- If `predicted=0` but `symbol_skip=0` and `blocked=0` → deeper issue in predict/emit

### Step 7: Remove diagnostic logging (or downgrade to DEBUG)
Once root cause is identified and fixed, convert `[DIAG]` logs to DEBUG level or remove them.

### Step 8: Verify prediction volume
After fix, expect:
- 84 native predictions per boundary (21 models × 4 symbols)
- 84 × 3 = 252 evaluation rows per boundary (3 eval windows)
- Total: 336 prediction rows per boundary
- Trades may be 0 initially (confidence gate + filter pipeline)

### Step 9: Verify `/status` endpoint works
```bash
curl -s http://localhost:8080/status | python3 -m json.tool
```

### Step 10: Verify lifecycle.py fix
Check that no more `no such column: ts_ms` errors in logs.

## Secondary Fixes (can be done in parallel with Step 1)

### lifecycle.py ts_ms fix
**File**: `ofi-lab-v3/storage/lifecycle.py`, line 148
**Change**: `ORDER BY ts_ms DESC` → `ORDER BY ts DESC`
**Also check**: line 60-63 in `model_selector.py` — uses `ORDER BY ts DESC` (CORRECT already)

### /status API fix
**File**: `ofi-lab-v3/trading/api_server.py`, lines 504-505
**Remove**: `trader._pending_resolutions` and `trader._pending_pred_resolutions`
**Replace with**: `len(trader.pending_queue._queue)` or similar

## Risk Assessment
- Diagnostic logging is INFO-level, so it WILL appear in logs (current log level = INFO)
- Adding counters has near-zero performance impact
- The fixes are minimal and isolated
- If root cause is in `_model_meta` population, the diagnostic will make it obvious
