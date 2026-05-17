# Per-Model Trade Gates — Design Spec

**Status:** Draft for review
**Date:** 2026-05-17
**Author:** Opus (orchestrator) — context built from Sonnet gate-landscape investigation
**Branch:** `v3-plan-a-foundation`

---

## Context

Production v3 paper trader runs 84 fleet models (4 symbols × 7 horizons × 3 train-day windows) but writes **0 paper_trades rows in 24h** despite emitting 33,168 predictions. The investigation surfaced three causes:

1. **All 84 models share a single global confidence threshold** (`PAPER_CONFIDENCE_THRESHOLD = 0.52`). Tuned reasonably for h300 BTC but inappropriate for the rest of the fleet — long-horizon models inherently produce lower-confidence outputs and a global 0.52 rejects nearly all of them.
2. **Per-model override path is plumbed but unpopulated.** `model_registry.filter_config_json` column exists; `_get_meta()` parses it into `meta["filter_config"]`; `_run_predictions` reads `meta["filter_config"].get("confidence_threshold")` with fallback to the global. Every row currently contains `'{}'`, so no model gets an override.
3. **Calibrator silently broken** (separate spec — see Phase C). All models gate on raw probabilities, not calibrated ones, because `paper_trader.py:1354` calls `CalibratorRegistry.get(model_name)` with 1 arg where 3 are required, `TypeError` swallowed by bare except.

This spec covers (1) and (2). The calibrator fix is independent — see `2026-05-17-calibrator-audit-design.md` (forthcoming, Phase C).

**User intent (captured 2026-05-16):** "gates should work on a per model basis." All four trade gates — confidence_threshold, ev_threshold, blackout_hours, warmup_seconds — must be configurable per-model with fallback to global defaults. Configuration via both a CLI for batch updates and a dashboard UI for one-off changes.

---

## Decisions

### D1. Schema: extend `filter_config_json`, no new columns.

`model_registry.filter_config_json TEXT DEFAULT '{}'` already exists. Adding typed columns for each gate (confidence_threshold REAL, ev_threshold REAL, blackout_hours_json TEXT, warmup_seconds INTEGER) would require migrations and produce schema churn every time a new gate is added. The JSON blob is the right level of flexibility for operator-facing config that varies model-by-model.

**Trade-off:** no SQL index on threshold values. Acceptable — we only read on boot and on a 16-boundary refresh, not in the hot inner loop.

### D2. Canonical JSON shape

```json
{
  "confidence_threshold": 0.55,
  "ev_threshold": 0.005,
  "blackout_hours": [21, 22, 23, 0, 1, 2, 3],
  "warmup_seconds": 1800
}
```

Rules:
- Any key omitted → fall back to global default in `self.filters` / module constants.
- `confidence_threshold` and `ev_threshold` are floats in `[0.0, 1.0]` (confidence) and `[-1.0, 1.0]` (EV).
- `blackout_hours` is a JSON array of UTC integer hours in `[0, 23]`. Empty array `[]` means "no blackout" (overrides any default).
- `warmup_seconds` is a non-negative integer.
- Unknown keys logged at WARN but ignored — forward-compatible.

### D3. Fallback hierarchy (highest → lowest priority)

1. Per-model `filter_config_json` value (if key present + non-null)
2. Per-symbol override in `self.filters["per_symbol_confidence"]` (legacy — keep but deprecate; only applies to confidence)
3. Global default in `self.filters` (from `__init__` `filter_config` kwarg, env vars, or module constants)
4. Hardcoded constants as last resort (`MAD_WARMUP_SECONDS = 1800`, `H60_BLACKOUT_HOURS`)

### D4. Refresh cadence: every 16 boundaries (same as lifecycle FSM)

`PaperTrader._reload_model_meta()` re-queries `model_registry` and updates `self._model_meta` in place. Called from `_contract_boundary_loop` every 16 contract boundaries (~80 minutes for 5-min boundaries). Logs which models changed.

**Trade-off:** updates lag by up to 80 minutes. For dashboard-driven one-off changes that need to take effect immediately, the dashboard endpoint triggers a manual refresh via `POST http://localhost:8080/reload_meta` on the runtime API (already exposed at port 8080).

### D5. Audit: every change writes to `model_audit`

Existing `model_audit` table (created in Plan C) captures registry mutations. Every CLI or API change to `filter_config_json` writes an audit row with operator identity, before/after JSON, and timestamp.

### D6. Defaults — what the global `self.filters` should hold post-refactor

Keep current values for compatibility:
- `confidence_threshold = 0.52` (env: `PAPER_CONFIDENCE_THRESHOLD`)
- `ev_threshold = 0.0` (no-op by default)
- `max_book_age_seconds = 30` (NOT per-model — see D7)
- `MAD_WARMUP_SECONDS = 1800` (module constant, retained)
- `H60_BLACKOUT_HOURS = {21,22,23,0,1,2,3}` (retained as fallback for h60 family when `blackout_hours` absent in filter_config)

### D7. Out of scope: per-model staleness, circuit breaker revival

`max_book_age_seconds` stays global — staleness is a market-data property, not a model property.

`circuit_breaker`, `clob_divergence`, `volatility`, `per_symbol_confidence` (the dead `_check_filters` gates) — out of scope for this spec. Revival is a future decision.

---

## Schema

No schema changes. The existing column is sufficient:

```sql
-- storage/schema.sql, model_registry table (already exists)
filter_config_json TEXT DEFAULT '{}'
```

Validation happens at the application layer (CLI / API), not at the schema layer.

---

## Read Path (paper_trader.py changes)

### R1. `_get_meta()` already parses `filter_config_json`

Existing code at `paper_trader.py:346–354`:
```python
def _get_meta(self, model_name: str) -> dict:
    if model_name in self._model_meta:
        return self._model_meta[model_name]
    # fallback to PAPER_TRADING["model_metadata"]
    ...
```
And in `__init__` at lines 276–306, `filter_config_json` is read from each `model_registry` row and parsed into `meta["filter_config"]` as a dict.

**No edit needed here.**

### R2. `filter_ctx` construction at `_run_predictions:1421–1451`

Existing per-model override path (already in place):
```python
_filter_cfg = meta.get("filter_config", {}) or {}
effective_ct = _filter_cfg.get("confidence_threshold", self.filters["confidence_threshold"])
effective_ev_thresh = _filter_cfg.get("ev_threshold", self.filters["ev_threshold"])
```

Add **blackout_hours** and **warmup_seconds** per-model overrides:
```python
effective_blackout_hours = _filter_cfg.get(
    "blackout_hours",
    list(H60_BLACKOUT_HOURS) if (model_name in H60_BLACKOUT_MODELS or meta.get("training_horizon_seconds") == 60) else []
)
effective_warmup_seconds = _filter_cfg.get("warmup_seconds", MAD_WARMUP_SECONDS)
```

`filter_ctx["blackout_hours"]` already exists — change its source. `filter_ctx["warmup_seconds"]` is new — add it.

### R3. Warmup check uses per-model value

`_is_in_warmup()` and `is_in_warmup(now_ms)` (currently global) must accept a model context. Add `is_model_in_warmup(now_ms, warmup_seconds)` that compares `(now_ms - self._first_data_time_ms) / 1000` to the per-model `warmup_seconds`. Existing global methods retained for non-model-scoped checks.

### R4. `_reload_model_meta()` method

```python
def _reload_model_meta(self) -> None:
    """Re-read filter_config_json + lifecycle_state for every registered model.
    Called every 16 boundaries from _contract_boundary_loop, or on demand via
    runtime API POST /reload_meta.
    """
    if not self._registry_conn:
        return
    rows = self._registry_conn.execute(
        "SELECT name, filter_config_json, lifecycle_state, platform_active_json FROM model_registry"
    ).fetchall()
    changed = []
    for row in rows:
        name = row["name"]
        if name not in self._model_meta:
            continue
        try:
            new_fc = json.loads(row["filter_config_json"] or "{}")
        except json.JSONDecodeError:
            logger.warning("model %s has invalid filter_config_json — skipping", name)
            continue
        old_fc = self._model_meta[name].get("filter_config", {})
        if new_fc != old_fc:
            self._model_meta[name]["filter_config"] = new_fc
            changed.append(name)
    if changed:
        logger.info("reloaded filter_config for %d models: %s", len(changed), changed)
```

### R5. Boundary-loop integration

In `_contract_boundary_loop` around line 1240 (where lifecycle FSM eval runs every 16 boundaries), add:
```python
if self._boundary_counter % 16 == 0:
    self._reload_model_meta()
    self.lifecycle_state_machine.evaluate(...)
```

---

## Write Path

### W1. CLI: `scripts/set_model_filter.py`

**Usage:**
```bash
.venv/bin/python3 scripts/set_model_filter.py \
  --model-pattern "h60_*" \
  --confidence-threshold 0.58 \
  --ev-threshold 0.003 \
  --blackout-hours "21,22,23,0,1,2,3"

.venv/bin/python3 scripts/set_model_filter.py \
  --model "h300_btc_30d" \
  --warmup-seconds 600

.venv/bin/python3 scripts/set_model_filter.py \
  --model-pattern "h1800_*" \
  --clear-keys "blackout_hours,warmup_seconds"

.venv/bin/python3 scripts/set_model_filter.py \
  --model-pattern "h60_*" \
  --confidence-threshold 0.58 \
  --dry-run
```

**Behavior:**
- `--model-pattern` matches `model_registry.name` via SQL `LIKE` with `*` → `%`. `--model NAME` is exact match.
- Reads current `filter_config_json`, merges supplied keys (set/clear), writes back.
- `--clear-keys "a,b"` removes those keys (falling back to global).
- `--dry-run` shows the planned merged JSON for each matched model without writing.
- Logs every change to `model_audit` with `actor="cli"` and `command="set_model_filter"`.
- Exit code 0 if any change made; 1 if no matches; 2 on JSON validation failure.

**Validation:**
- `confidence_threshold` in `[0.0, 1.0]`
- `ev_threshold` in `[-1.0, 1.0]`
- `blackout_hours` list of int in `[0, 23]`, deduped
- `warmup_seconds` non-negative int

### W2. Dashboard API: `POST /api/models/{name}/filter`

**Request body:**
```json
{
  "confidence_threshold": 0.55,
  "ev_threshold": 0.005,
  "blackout_hours": [21, 22, 23],
  "warmup_seconds": 1800
}
```

**Behavior:**
- Read existing `filter_config_json`, merge supplied keys, write back.
- For models with `live_eligible = 1`: require HMAC-signed confirmation token via `admin_auth.py` flow (same pattern as `/api/models/{name}/enable_live`).
- For models with `live_eligible = 0` (paper-only): standard `DASHBOARD_USER` / `DASHBOARD_PASS` Basic auth is sufficient.
- After writing: `POST http://localhost:8080/reload_meta` (runtime API) to trigger immediate reload on the trader. If runtime API unreachable, log WARN; the 16-boundary refresh will pick it up.
- Audit row written.

**Response:**
```json
{
  "name": "h60_btc_30d",
  "filter_config": { "confidence_threshold": 0.58, "ev_threshold": 0.003 },
  "applied_at": "2026-05-17T08:42:11Z",
  "trader_reloaded": true
}
```

**Errors:**
- 404 if model name not in registry
- 422 if validation fails (with field-level error messages)
- 401 if Basic auth missing
- 403 if model is live-eligible and confirmation token missing/invalid

### W3. Runtime API: `POST /reload_meta` (port 8080)

Add to `trading/api_server.py`:
```python
@app.post("/reload_meta")
async def reload_meta():
    trader._reload_model_meta()
    return {"status": "reloaded"}
```

This is server-internal — bind to `127.0.0.1` only (already the case for the runtime API).

### W4. Frontend UI

Two surfaces:

**(a) Model detail page** — extend `dps-frontend/src/pages/ModelComparison/ModelComparison.tsx` to surface per-model gate values inline. Each model row gets a "Gates" expand affordance. Inline form fields:
- Confidence threshold (number input, step 0.01)
- EV threshold (number input, step 0.001)
- Blackout hours (multi-select of 0–23)
- Warmup seconds (number input)
- "Save" button → calls `POST /api/models/{name}/filter`
- For live-eligible models: HMAC token field appears

**(b) Bulk view** — new page `dps-frontend/src/pages/ModelFilters/ModelFilters.tsx` (or extend ModelComparison) showing a table of all 84 models × 4 gates. Operator can see at a glance which models have which overrides. Filter by symbol / horizon / "uses default."

Defer (a) and (b) to a follow-up commit if the CLI is sufficient for early production tuning. The CLI is the priority deliverable.

---

## Testing

### T1. Unit: `tests/test_per_model_gates.py` (NEW, 6 tests)

```
test_confidence_threshold_per_model_override
test_ev_threshold_per_model_override
test_blackout_hours_per_model_override
test_warmup_seconds_per_model_override
test_missing_keys_fall_back_to_global
test_reload_model_meta_picks_up_sql_changes
```

Each test creates a `PaperTrader` with a mock registry, sets a model's `filter_config_json`, runs one boundary, asserts the verdict / log line / `paper_trades` row reflects the override.

### T2. CLI: `tests/test_set_model_filter_cli.py` (NEW, 5 tests)

```
test_pattern_match
test_exact_model_match
test_merge_preserves_unsupplied_keys
test_clear_keys_removes_keys
test_dry_run_writes_nothing
test_validation_rejects_out_of_range
```

### T3. API: `tests/test_models_filter_endpoint.py` (NEW, 4 tests)

```
test_post_filter_updates_registry
test_post_filter_live_model_requires_token
test_post_filter_triggers_reload
test_post_filter_invalid_json_returns_422
```

### T4. Integration: extend `tests/test_paper_trader_scoring.py`

Add one end-to-end test: register two models with different `filter_config_json` (one strict, one permissive), feed identical predictions, assert only the permissive model's verdict passes.

---

## Rollout

### R1. Land Phase B implementation in 3 commits

1. **Backend wiring** — `_reload_model_meta()`, blackout/warmup per-model lookup, `/reload_meta` runtime endpoint. Tests T1, T4.
2. **CLI tool** — `scripts/set_model_filter.py` + tests T2.
3. **Dashboard endpoint** — `POST /api/models/{name}/filter` + tests T3. Frontend UI deferred.

### R2. Populate `filter_config_json` for all 84 models pre-deploy

CLI commands run locally against staging DB, then propagated to VPS via SQL or by running the CLI on VPS:

```bash
# Suggested starting values — tune with backtest data
.venv/bin/python3 scripts/set_model_filter.py --model-pattern "h60_*"   --confidence-threshold 0.58 --ev-threshold 0.003
.venv/bin/python3 scripts/set_model_filter.py --model-pattern "h300_*"  --confidence-threshold 0.55 --ev-threshold 0.002
.venv/bin/python3 scripts/set_model_filter.py --model-pattern "h900_*"  --confidence-threshold 0.54 --ev-threshold 0.003
.venv/bin/python3 scripts/set_model_filter.py --model-pattern "h1800_*" --confidence-threshold 0.53 --ev-threshold 0.005
```

These are starting points based on intuition about horizon-vs-edge tradeoff. Final values come from backtest validation (not part of this spec).

### R3. Deploy + observe DIAG output

After Phase D (decomposition + dead-code cleanup) ships:
- Rsync to VPS, restart services
- Set per-model filter_config_json via CLI
- Wait 30-min warmup
- Read DIAG logs for verdict pass rate per model
- Confirm `paper_trades` table has rows in last hour
- Tune thresholds via CLI as needed (no restart required)

---

## Risks & Open Questions

1. **`_reload_model_meta` only updates `filter_config`, not other metadata.** If `lifecycle_state` changes on the registry side, this method updates it but other code paths may not respect the new state immediately. Scope of this spec: filter_config only.

2. **Validation gaps.** The CLI and API validate threshold ranges, but not "is this confidence_threshold reachable given the model's typical output distribution?" That's an operator concern, not a system concern. Document in the CLI help text.

3. **Concurrent writes.** If the CLI runs while the dashboard endpoint also fires, last-write-wins on `filter_config_json`. Acceptable given the low write rate and audit trail.

4. **Migration of legacy `per_symbol_confidence` dict in `self.filters`.** Currently unused in the active gate path (only `_check_filters` reads it). Not migrating in this spec — see "Out of scope" D7.

5. **Frontend HMAC token UX** — needs a token-input modal pattern. Reuse the one from `enable_live` flow.

6. **`_reload_model_meta()` runs on a SQLite connection that may be locked by the writer.** Solution: use a read-only connection with `PRAGMA journal_mode=WAL` (already the case for the trader's registry connection). Confirmed in `storage/db.py`.

---

## Files Touched (preview for Phase B implementation)

**Backend:**
- `ofi-lab-v3/trading/paper_trader.py` — `_reload_model_meta`, filter_ctx blackout+warmup
- `ofi-lab-v3/trading/api_server.py` — `/reload_meta` endpoint
- `ofi-lab-v3/scripts/set_model_filter.py` — NEW
- `ofi-lab-v3/dashboard_api/routers/models_admin.py` — extend with filter route
- `ofi-lab-v3/dashboard_api/services/admin_auth.py` — no change (reuse pattern)

**Frontend (deferred):**
- `dps-frontend/src/pages/ModelComparison/ModelComparison.tsx` — gate edit form
- `dps-frontend/src/api/client.ts` — `updateModelFilter()` typed call

**Tests:**
- `ofi-lab-v3/tests/test_per_model_gates.py` — NEW
- `ofi-lab-v3/tests/test_set_model_filter_cli.py` — NEW
- `ofi-lab-v3/tests/test_models_filter_endpoint.py` — NEW
- `ofi-lab-v3/tests/test_paper_trader_scoring.py` — extend

**Docs:**
- `docs/superpowers/specs/2026-05-17-per-model-gates-design.md` — THIS DOC
- (later) `docs/superpowers/plans/2026-05-17-per-model-gates-implementation.md` — Phase B execution plan
