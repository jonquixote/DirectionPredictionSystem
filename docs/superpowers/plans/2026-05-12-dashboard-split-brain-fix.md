# Dashboard Split-Brain Fix: JSONL → SQLite + Fleet Visibility

> **Goal:** Make v3 fleet data visible on bet.octavo.press by rewiring the dashboard API from stale JSONL files to the live SQLite DB, and updating the frontend to know about fleet models and XRP.

**Date:** 2026-05-12
**Predecessor:** `2026-05-11-v3-fleet-activation.md` (Tasks 1-10 complete — 84 models trading on VPS)

---

## Root Cause

The `PaperTrader` writes **only** to SQLite (`/data/v3.db`). The dashboard API reads **only** from JSONL files (`jsonl_reader.py`). JSONL files stopped updating May 3 when the trader was cut over to SQLite. The dashboard is therefore frozen at May 3 data and blind to all 84 fleet models.

Two independent problems:
1. **Backend:** 5 files import `jsonl_reader.get_store()` — they read from stale JSONL, not from the live SQLite tables (`predictions`, `paper_trades`)
2. **Frontend:** `visibility.ts` `ALL_COMBOS` and filter dropdowns in `Predictions.tsx`/`Trades.tsx` only know 2 legacy models (h60, h300) × 3 symbols × 2 durations = 12 combos. No h60_v3, no fleet models, no XRPUSDT, no 1800s.

---

## Architecture Decision

**Replace `jsonl_reader.DataStore` with a new `sqlite_store.DataStore`** that reads from the same SQLite DB the trader writes to. Both sides use WAL mode, so reads are non-blocking.

**Why not patch jsonl_reader?** The JSONL file format is fundamentally different from the SQLite schema (different column names, different merge logic for predictions+resolutions, different model metadata structure). A clean replacement is simpler and less error-prone than a hybrid.

**Why keep the same `DataStore` interface?** All 5 consumers (`predictions.py`, `trades.py`, `performance.py`, `logs.py`, `live_state.py`) use the same methods: `.predictions`, `.trades`, `.get_predictions()`, `.get_trades()`, `.get_resolved_trades()`, `.get_open_trades()`. Preserving this interface means minimal router changes.

---

## Task Breakdown

### Task 1: Create `sqlite_store.py` — new DataStore backed by SQLite

**File:** `ofi-lab-v3/dashboard_api/services/sqlite_store.py` (NEW)

Create a new `DataStore` class with the **same public interface** as the old `jsonl_reader.DataStore`:

```python
class DataStore:
    predictions: list[dict]   # materialized on refresh
    trades: list[dict]        # materialized on refresh
    model_metadata: dict[str, dict]

    def refresh(self) -> None
    def get_predictions(...) -> tuple[list[dict], int]
    def get_trades(...) -> tuple[list[dict], int]
    def get_open_trades() -> list[dict]
    def get_resolved_trades(model=None, symbol=None) -> list[dict]
    def _filter_records(records, model, symbol, from_ms, to_ms, ...) -> list[dict]
```

**Key design decisions:**

1. **Field name mapping** — SQLite columns differ from JSONL field names. The store must normalize:
   | SQLite column | JSONL / API field |
   |---|---|
   | `model_name` | `model` |
   | `market_window_seconds` | `contract_duration_seconds` |
   | `pred_proba_calibrated` | `pred_proba` |
   | `prediction_correct` (0/1/NULL) | `prediction_correct` (bool/None) |
   | `resolved` (0/1) | `resolved` (bool) |
   | `suppressed_reason` (from paper_trades) | `suppressed_reason` |
   | `warmup` (0/1) | `warmup` (bool) |
   | `ts_contract_open_ms` | `ts_model_ran_ms` (for sort — note: predictions table has both `ts_model_ran_ms` and `ts_contract_open_ms`) |

2. **Resolution merge** — In JSONL, predictions and resolutions were separate records merged by `prediction_id`. In SQLite, the `predictions` table already has resolution columns (`prediction_correct`, `resolved`, `price_at_close`, etc.) filled in by `record_native_resolution()`. So a single SELECT gives us the merged record — no client-side merge needed.

3. **Trades** — `paper_trades` table already has both entry + resolution columns (similar merge as above). A single SELECT with LEFT JOIN to `predictions` for any missing fields.

4. **Model metadata** — Query `model_registry` table instead of reading filesystem JSON files. This gives us all 84 fleet models automatically.

5. **Divergence computation** — Compute `divergence = abs(pred_proba_calibrated - p_market)` and `signed_divergence` at load time (same as jsonl_reader).

6. **Outcome derivation** — Same logic as jsonl_reader: `prediction_correct is None → "unresolved"`, `True → "correct"`, `False → "incorrect"`.

7. **Prediction + trade linkage** — The `paper_trades` table has `prediction_id` FK. We can enrich trade records with prediction fields (e.g., `pred_proba`) via JOIN.

8. **Refresh strategy** — Same 5s refresh loop via `LiveState`. On refresh, compare `MAX(rowid)` or `COUNT(*)` to detect changes (cheaper than file-size check). Only re-read if data changed.

**SQL queries:**

```sql
-- Predictions (native + evaluation, merged with resolution)
SELECT
  p.prediction_id, p.model_name, p.symbol,
  p.market_window_seconds, p.resolution_type,
  p.ts_model_ran_ms, p.ts_contract_open_ms, p.ts_resolve_at_ms,
  p.pred_proba_raw, p.pred_proba_calibrated, p.pred_direction,
  p.above_threshold, p.warmup, p.trade_eligible, p.platform,
  p.p_market, p.p_model_minus_market,
  p.utc_hour, p.day_of_week,
  p.regime_volatility, p.regime_liquidity, p.regime_trend,
  p.price_at_open, p.price_at_close, p.contract_result,
  p.prediction_correct, p.resolved, p.ts_resolved_ms,
  p.decision_outcome, p.decision_reason, p.ev_estimate
FROM predictions p
ORDER BY p.ts_model_ran_ms;

-- Paper trades (with prediction enrichment)
SELECT
  t.trade_id, t.prediction_id, t.model_name, t.symbol,
  t.market_window_seconds, t.resolution_type,
  t.ts_model_ran_ms, t.ts_contract_open_ms, t.ts_resolve_at_ms,
  t.pred_proba_raw, t.pred_proba_calibrated, t.pred_direction,
  t.confidence_threshold_used, t.simulated_stake_usdc,
  t.p_market, t.suppressed_reason, t.filter_mode, t.warmup, t.platform,
  t.decision_outcome, t.decision_reason, t.ev_estimate,
  t.kelly_fraction_capped, t.final_size_usdc, t.order_type,
  t.price_at_open, t.price_at_close, t.contract_result,
  t.prediction_correct, t.gross_pnl, t.fee_paid, t.net_pnl,
  t.trade_result, t.resolved, t.ts_resolved_ms
FROM paper_trades t
ORDER BY t.ts_model_ran_ms;

-- Model registry (for metadata)
SELECT name, symbol, training_horizon_seconds, is_baseline,
       paper_active, live_eligible, lifecycle_state,
       artifact_path, feature_names_path, train_window_start,
       train_window_end, train_days, feature_version, evaluation_windows
FROM model_registry;
```

**Normalization in Python:**
- `model_name` → rename to `model`
- `market_window_seconds` → rename to `contract_duration_seconds`
- `pred_proba_calibrated` → rename to `pred_proba`
- `prediction_correct` 0/1/NULL → bool/None
- `resolved` 0/1 → bool
- `warmup` 0/1 → bool
- Compute `divergence = abs(pred_proba_calibrated - p_market)` and `signed_divergence`
- Derive `outcome` from `prediction_correct`

---

### Task 2: Update 5 consumers to use `sqlite_store` instead of `jsonl_reader`

**Files to change:**

| File | Change |
|---|---|
| `services/live_state.py` | `from services.jsonl_reader import get_store` → `from services.sqlite_store import get_store` |
| `routers/predictions.py` | Same import swap |
| `routers/trades.py` | Same import swap |
| `routers/performance.py` | Same import swap + fix hardcoded model/symbol lists |
| `routers/logs.py` | Same import swap |

**Performance.py hardcoded fixes:**

- Line 23: `models = [model] if model else ["h60", "h300", "h60_v3"]` → `models = [model] if model else list(store.model_metadata.keys())`
- Line 24: `symbols = [symbol] if symbol else ["BTCUSDT", "SOLUSDT", "ETHUSDT", "ALL"]` → `symbols = [symbol] if symbol else [*store.unique_symbols, "ALL"]`
- Line 28: `contract_durations = ["ALL", 300, 900]` → `contract_durations = ["ALL", 300, 900, 1800]`
- Line 504: `symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]` → `symbols = store.unique_symbols`

**LiveState.py hardcoded fixes:**

- Lines 91, 102, 116, 155, 168: All `for model in ["h60", "h300", "h60_v3"]:` → `for model in store.model_metadata:`
- `SUPPRESSION_RULES` list: Keep as-is for now (it's display-only), but note it's stale for fleet models. We can derive suppression rules from `model_registry` later.

**Add `unique_symbols` and `unique_models` properties to DataStore:**
```python
@property
def unique_symbols(self) -> list[str]:
    return sorted(set(p["symbol"] for p in self.predictions))

@property
def unique_models(self) -> list[str]:
    return sorted(set(p["model"] for p in self.predictions))
```

---

### Task 3: Add `/api/combos` endpoint for dynamic combo discovery

**File:** `ofi-lab-v3/dashboard_api/routers/predictions.py` (or new `routers/combos.py`)

Add a new endpoint that returns the full list of active (model × symbol × duration) combos from the DB. This replaces the need for `ALL_COMBOS` to be hardcoded in the frontend.

```python
@router.get("/combos")
async def list_combos():
    """Return all active (model × symbol × duration) combos from live data."""
    store = get_store()
    combos = set()
    for p in store.predictions:
        combos.add((p["model"], p["symbol"], p.get("contract_duration_seconds")))
    # Also include from trades
    for t in store.trades:
        combos.add((t["model"], t["symbol"], t.get("contract_duration_seconds")))
    return {
        "data": [
            {"model": m, "symbol": s, "duration": d}
            for m, s, d in sorted(combos)
        ]
    }
```

This endpoint will be used by the frontend to populate `ALL_COMBOS` dynamically.

---

### Task 4: Update frontend `visibility.ts` — dynamic combo loading

**File:** `dashboard/src/lib/visibility.ts`

Replace the hardcoded `ALL_COMBOS` array with a runtime-fetched value:

```typescript
let _allCombos: Combo[] | null = null;

export async function fetchAllCombos(): Promise<Combo[]> {
  if (_allCombos) return _allCombos;
  try {
    const resp = await fetch('/api/combos', { credentials: 'same-origin' });
    if (!resp.ok) throw new Error('failed');
    const body = await resp.json();
    _allCombos = (body.data || []).map((c: any) => ({
      model: c.model,
      symbol: c.symbol,
      duration: c.duration,
    }));
  } catch {
    _allCombos = FALLBACK_COMBOS; // keep old 12-combo list as fallback
  }
  return _allCombos;
}

export function getAllCombos(): Combo[] {
  return _allCombos || FALLBACK_COMBOS;
}
```

Keep `FALLBACK_COMBOS` as the current 12-combo list for SSR / initial render / API failure.

The `Settings.tsx` `VisibilityMatrix` and `AllowListEditor` components will call `fetchAllCombos()` on mount and use the result.

---

### Task 5: Update frontend filter dropdowns in `Predictions.tsx` and `Trades.tsx`

**Files:** `dashboard/src/pages/Predictions.tsx`, `dashboard/src/pages/Trades.tsx`

Changes:
1. Replace `type ModelFilter = 'all' | 'h300' | 'h60'` with `type ModelFilter = string` (or `'all' | string`)
2. Replace `type SymbolFilter = 'all' | 'BTCUSDT' | 'SOLUSDT' | 'ETHUSDT'` with `type SymbolFilter = string`
3. Replace hardcoded `choices` arrays in `<Segment>` components with dynamic values derived from `fetchAllCombos()`:
   ```typescript
   const [comboData, setComboData] = useState<Combo[]>([]);
   useEffect(() => { fetchAllCombos().then(setComboData); }, []);
   
   const modelChoices = useMemo(() => [
     { value: 'all', label: 'all' },
     ...Array.from(new Set(comboData.map(c => c.model)))
       .sort()
       .map(m => ({ value: m, label: m })),
   ], [comboData]);
   
   const symbolChoices = useMemo(() => [
     { value: 'all', label: 'all' },
     ...Array.from(new Set(comboData.map(c => c.symbol)))
       .sort()
       .map(s => ({ value: s, label: s.replace('USDT','').toLowerCase() })),
   ], [comboData]);
   ```

4. Add a duration filter `<Segment>` (currently missing — the frontend has no duration filter at all):
   ```typescript
   type DurationFilter = 'all' | number;
   // choices: all, 300, 900, 1800
   const durationChoices = useMemo(() => [
     { value: 'all', label: 'all' },
     ...Array.from(new Set(comboData.map(c => c.duration)))
       .sort((a,b) => a - b)
       .map(d => ({ value: String(d), label: `${d/60}m` })),
   ], [comboData]);
   ```

5. Pass `duration` param to API calls (currently not sent).

---

### Task 6: Update `Settings.tsx` VisibilityMatrix

**File:** `dashboard/src/pages/Settings.tsx`

- Replace `ALL_COMBOS` static import with `fetchAllCombos()` / `getAllCombos()`
- The `VisibilityMatrix` component iterates `ALL_COMBOS` — change to iterate the dynamically fetched combo list
- The `AllowListEditor` deduces `(symbol, duration)` pairs from `ALL_COMBOS` — update similarly
- Ensure new combos default to visible (or at least present in the matrix for toggling)

---

### Task 7: Fix `features.py` hardcoded `fs_model_map`

**File:** `ofi-lab-v3/dashboard_api/routers/features.py`

- Lines 31-35: `fs_model_map = {"h60_v1": "latest_h60", "h60_v3": "latest_h60", "h300": "latest_h300"}` — This maps frontend model version strings to filesystem directory names for feature importance JSON. Fleet models have artifacts at different paths. 
- **Fix:** Query `model_registry` table for `artifact_path` and `feature_names_path`, use those to locate `feature_importance.json` alongside the model artifact.
- Alternatively, since fleet models may not have `feature_importance.json` yet, add a graceful fallback (return empty list with a warning).

---

### Task 8: Add busy timeout to dashboard `get_db()`

**File:** `ofi-lab-v3/dashboard_api/services/db.py`

The dashboard's `get_db()` doesn't set a busy timeout, while the writer uses 5s. Under heavy write load, the API could get `SQLITE_BUSY` errors. Add:
```python
conn.execute("PRAGMA busy_timeout=5000")
```

Also add `PRAGMA synchronous=NORMAL` for consistency with the writer.

---

## Execution Order

1. **Task 1** — Create `sqlite_store.py` (no deps, foundation for everything)
2. **Task 8** — Fix `get_db()` busy timeout (independent, quick)
3. **Task 2** — Swap imports in 5 consumers + fix hardcoded lists
4. **Task 7** — Fix `features.py` model map (independent but related)
5. **Task 3** — Add `/api/combos` endpoint (needs Task 1)
6. **Task 4** — Update `visibility.ts` for dynamic combos (needs Task 3)
7. **Task 5** — Update `Predictions.tsx` and `Trades.tsx` filter dropdowns (needs Task 4)
8. **Task 6** — Update `Settings.tsx` (needs Task 4)

---

## Field Name Mapping: SQLite → API

This is the critical mapping that must be correct. The SQLite columns (from `schema.sql`) need to be normalized to match what the dashboard API endpoints currently return.

### Predictions

| SQLite Column | API Field | Transform |
|---|---|---|
| `prediction_id` | `prediction_id` | direct |
| `model_name` | `model` | rename |
| `symbol` | `symbol` | direct |
| `market_window_seconds` | `contract_duration_seconds` | rename |
| `resolution_type` | `resolution_type` | direct |
| `ts_model_ran_ms` | `ts_model_ran_ms` | direct |
| `ts_contract_open_ms` | `ts_contract_open_ms` | direct |
| `pred_proba_raw` | `pred_proba_raw` | direct |
| `pred_proba_calibrated` | `pred_proba` | rename (the "main" proba shown in UI) |
| `pred_direction` | `pred_direction` | direct |
| `above_threshold` | `above_threshold` | direct |
| `warmup` | `warmup` | 0/1 → bool |
| `trade_eligible` | `trade_eligible` | 0/1 → bool |
| `p_market` | `p_market` | direct |
| `p_model_minus_market` | `signed_divergence` | direct (= calibrated - market) |
| *(computed)* | `divergence` | `abs(pred_proba_calibrated - p_market)` |
| `prediction_correct` | `prediction_correct` | 0/1/NULL → bool/None |
| `resolved` | `resolved` | 0/1 → bool |
| `price_at_open` | `price_at_open` | direct |
| `price_at_close` | `price_at_close` | direct |
| `suppressed_reason` | *(not on predictions table)* | comes from paper_trades join |
| *(derived)* | `outcome` | from `prediction_correct` |
| `decision_outcome` | `decision_outcome` | direct |
| `decision_reason` | `decision_reason` | direct |
| `ev_estimate` | `ev_estimate` | direct |

### Paper Trades

| SQLite Column | API Field | Transform |
|---|---|---|
| `trade_id` | `id` / `trade_id` | direct |
| `prediction_id` | `prediction_id` | direct |
| `model_name` | `model` | rename |
| `symbol` | `symbol` | direct |
| `market_window_seconds` | `contract_duration_seconds` | rename |
| `ts_model_ran_ms` | `ts_model_ran_ms` | direct |
| `pred_proba_calibrated` | `pred_proba` | rename |
| `pred_direction` | `pred_direction` / `direction` | both |
| `p_market` | `p_market` | direct |
| `simulated_stake_usdc` | `simulated_stake_usdc` | direct |
| `suppressed_reason` | `suppressed_reason` | direct |
| `filter_mode` | `filter_mode` | direct |
| `warmup` | `warmup` | 0/1 → bool |
| `prediction_correct` | `prediction_correct` | 0/1/NULL → bool/None |
| `price_at_open` | `price_at_contract_open` | rename |
| `price_at_close` | `price_at_contract_close` | rename |
| `gross_pnl` | `gross_pnl` | direct |
| `fee_paid` | `fee_paid` | direct |
| `net_pnl` | `net_pnl` | direct |
| `resolved` | `resolved` | 0/1 → bool |
| `ts_resolved_ms` | `ts_contract_close_ms` | rename (API convention) |
| `decision_outcome` | `decision_outcome` | direct |
| `decision_reason` | `decision_reason` | direct |
| `ev_estimate` | `ev_estimate` | direct |
| `kelly_fraction_capped` | `kelly_fraction_capped` | direct |
| `final_size_usdc` | `final_size_usdc` | direct |
| *(derived)* | `outcome` | from `prediction_correct` |

---

## What We're NOT Changing (Out of Scope)

1. **`models_registry.py`** — Already reads from filesystem, not from jsonl_reader. Will be replaced by a proper `model_registry` SQLite query later, but not in this plan.
2. **`/api/status` endpoint** — Frontend doesn't consume it. The `LiveState.snapshot()` will automatically improve once it reads from SQLite (more models in `gate_status`, more symbols in predictions_per_hour).
3. **`SUPPRESSION_RULES` in `live_state.py`** — Display-only metadata. Will be made dynamic later.
4. **Double filter bug** in `paper_trader.py` — Separate fix, not part of this plan.
5. **`refresh_price_ranges()`** call in the periodic boundary loop — Separate fix.
6. **Overlap recording hardcoded "BTCUSDT"** — Separate fix.
7. **`kalshi_proxy.py`** — Not related to the JSONL/SQLite split.
8. **New frontend pages** (audit, overlap, calibration, parquet exports) — Future work.

---

## Verification Checklist

After deployment, verify on the VPS:

- [ ] `curl /api/predictions?model=h60_btc_v3_90d` returns fleet model predictions
- [ ] `curl /api/predictions?symbol=XRPUSDT` returns XRP predictions
- [ ] `curl /api/trades?model=h300_sol_v3_180d` returns SOL H300 trades
- [ ] `curl /api/performance/summary` includes entries for fleet models
- [ ] `curl /api/combos` returns 84+ combos (4 symbols × 7 horizons × 3+ durations)
- [ ] Frontend `Predictions` page shows fleet models in model dropdown
- [ ] Frontend `Trades` page shows XRPUSDT in symbol dropdown
- [ ] Frontend `Settings` visibility matrix shows all combos
- [ ] Data is current (within 5 minutes, matching the refresh loop)
- [ ] No JSONL files are being read (check logs for "jsonl" references)
- [ ] `/api/status` gate_status includes fleet models

---

## Risk Matrix

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SQLite read performance with 100K+ rows | Low | Medium | WAL mode + in-memory materialization (same pattern as JSONL). Pagination in API. |
| Field name mismatch causes silent data loss | Medium | High | Careful mapping table above. Test with `curl` on VPS. |
| Frontend combo fetch fails → no combos visible | Low | High | Fallback to hardcoded `FALLBACK_COMBOS`. |
| Dashboard API crashes on startup if v3.db missing | Low | High | `get_db()` should catch + return empty store with warning (same as jsonl_reader catches FileNotFoundError). |
| Performance summary takes too long with 84 models | Medium | Low | Reduce default contract_durations list. Skip zero-count combos (already done: `if n == 0: continue`). |
| `_enrich_prediction()` / `_enrich_trade()` breaks on new field names | Medium | High | The enrichment functions use `.get()` defensively — verify each field they read. |
