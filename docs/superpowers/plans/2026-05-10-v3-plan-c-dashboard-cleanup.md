# Model A v3 — Plan C: Dashboard, Safety, Rollback, and Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Plan B helpers that were left dangling, finish the three deferred Plan B tasks (T33/T34/T37), then ship the operator-facing surface — `/models` dashboard, `/api/models/*` and adjacent JSON endpoints, kill switch + rollback flows with confirmations, and the audit trail UI.

**Architecture:** Two phases. Phase 1 (Cleanup, Tasks C1–C10) closes the wiring gap surfaced by the Plan B verification subagent: `regime_features` not threaded into `_emit_prediction_rows`, three helper methods defined but uncalled (`_evaluate_paper_filters`, `refresh_decay_metrics`, `record_overlap_for_boundary`), plus the deferred T33/T34/T37 and one test rename. Phase 2 (Surface, Tasks C11–C27) adds FastAPI endpoints in `ofi-lab-v3/dashboard_api/routers/`, a `/models` page in the existing dashboard, confirmation flows for live trading, kill switch + rollback flows with post-restart re-confirm (Steering 10k, 10l), and an audit trail view. Baseline-protected models surface as locked rows in the UI but the locking decision is enforced server-side in `ModelRegistry`. All UI rendering uses `textContent` and safe DOM construction (no `innerHTML` with untrusted data) — server-rendered HTML uses Jinja2's auto-escaping.

**Tech Stack:** Python 3.13, FastAPI, SQLite (WAL), pytest, the existing `ofi-lab-v3/dashboard_api/` FastAPI app, Jinja2 templates (matching existing dashboard pattern), vanilla JS for confirmation modals, no new frontend framework.

**Plan B verification baseline (from haiku subagent run 2026-05-10):**
- Plan A + B test count: **267 passed / 7 failed / 2 errors / 11 skipped** (not the 156 the user reported — discrepancy traced to stale local count).
- 7 failures + 2 errors all root-cause to `_run_predictions` not passing `regime_features=features` into `_emit_prediction_rows`.
- 3 helper methods defined in `trading/paper_trader.py` but never invoked: `_evaluate_paper_filters` (line 615), `refresh_decay_metrics` (line 640), `record_overlap_for_boundary` (line 699).
- T33, T34, T37 deferred per user.
- `tests/test_psi_integration2.py` exists; canonical name from spec is `test_psi_integration.py`.

---

## File Structure

### Phase 1 — Cleanup (modify only)
- `ofi-lab-v3/trading/paper_trader.py` — wire helpers into `_run_predictions` and boundary loop.
- `ofi-lab-v3/tests/test_paper_trader_regime.py` — fix path fixtures.
- `ofi-lab-v3/tests/test_lifecycle_loop.py` *(new)* — T33 wiring test.
- `ofi-lab-v3/tests/test_paper_live_independence.py` *(new)* — T34.
- `ofi-lab-v3/tests/test_dynamic_range_integration.py` *(new)* — T37.
- `ofi-lab-v3/tests/test_psi_integration.py` *(rename of `test_psi_integration2.py`)*.

### Phase 2 — Dashboard API (new files)
- `ofi-lab-v3/dashboard_api/routers/models_admin.py` — POST mutations: enable/disable paper, enable/disable live, reload, rollback.
- `ofi-lab-v3/dashboard_api/routers/overlap.py` — GET `/api/overlap`.
- `ofi-lab-v3/dashboard_api/routers/regime.py` — GET `/api/regime/current`, `/api/regime/thresholds`.
- `ofi-lab-v3/dashboard_api/routers/calibration.py` — GET `/api/calibration/<model>`.
- `ofi-lab-v3/dashboard_api/routers/kill_switch.py` — POST `/api/kill_switch`, POST `/api/kill_switch/confirm_resume`.
- `ofi-lab-v3/dashboard_api/routers/audit.py` — GET `/api/audit`.
- `ofi-lab-v3/dashboard_api/services/admin_auth.py` — confirmation token issuance + verification.
- `ofi-lab-v3/dashboard_api/services/kill_switch_state.py` — persistent kill switch flag (`/data/kill_switch.json`).
- `ofi-lab-v3/dashboard_api/main.py` — register the new routers.

### Phase 2 — Dashboard UI (new files)
- `ofi-lab-v3/dashboard_api/templates/models.html` — `/models` page.
- `ofi-lab-v3/dashboard_api/templates/_model_card.html` — per-model card include.
- `ofi-lab-v3/dashboard_api/templates/audit.html` — audit trail view.
- `ofi-lab-v3/dashboard_api/static/js/models_admin.js` — toggle handlers + confirmation modal.
- `ofi-lab-v3/dashboard_api/static/js/audit.js` — audit page renderer.
- `ofi-lab-v3/dashboard_api/static/js/kill_switch_banner.js` — global banner poller.
- `ofi-lab-v3/dashboard_api/static/css/models.css` — locked-baseline styling, eligibility progress bars.
- `ofi-lab-v3/dashboard_api/routers/pages.py` — already exists; add `/models` and `/audit` routes.

### Tests (new)
- `ofi-lab-v3/tests/test_dashboard_models_admin.py`
- `ofi-lab-v3/tests/test_dashboard_kill_switch.py`
- `ofi-lab-v3/tests/test_dashboard_rollback.py`
- `ofi-lab-v3/tests/test_dashboard_audit.py`
- `ofi-lab-v3/tests/test_dashboard_overlap.py`
- `ofi-lab-v3/tests/test_dashboard_regime.py`
- `ofi-lab-v3/tests/test_dashboard_calibration.py`
- `ofi-lab-v3/tests/test_admin_auth.py`
- `ofi-lab-v3/tests/test_agent_native_parity.py`

---

# Phase 1 — Cleanup

## Task C1: Thread `regime_features` into `_emit_prediction_rows`

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py:1208-1226`

This fixes 7 test failures + 2 errors at once. `features` dict is in scope at the call site (line 1225 already passes `features.get("relative_spread")`).

- [ ] **Step 1: Run failing tests to confirm baseline**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3
source .venv/bin/activate
pytest tests/test_paper_trader_regime.py tests/test_paper_trader_resolution.py tests/test_paper_trader_scoring.py tests/test_replay_smoke.py tests/test_verbose_decision_trace.py tests/test_warmup_tagging.py -q
```
Expected: 7 failures + 2 errors, all KeyError on regime threshold dict.

- [ ] **Step 2: Add the kwarg**

In `_run_predictions`, change the call at lines 1208–1226. Add `regime_features=features` as the last kwarg before the closing paren:

```python
prediction_id = self._emit_prediction_rows(
    model_name=model_name,
    symbol=symbol,
    boundary_ms=boundary_ms,
    ts_model_ran_ms=ts_model_ran_ms,
    pred_proba_raw=pred_proba,
    pred_proba_calibrated=pred_proba,
    pred_direction=pred_direction,
    above_threshold=above_threshold,
    warmup=in_warmup,
    platform="paper",
    price_at_open=mid_price,
    p_market=p_market,
    p_model_minus_market=p_model_minus_market,
    utc_hour=utc_hour,
    day_of_week=day_of_week,
    is_weekend=(day_of_week >= 5),
    relative_spread=features.get("relative_spread"),
    regime_features=features,
)
```

- [ ] **Step 3: Re-run the same tests**

```bash
pytest tests/test_paper_trader_regime.py tests/test_paper_trader_resolution.py tests/test_paper_trader_scoring.py tests/test_replay_smoke.py tests/test_verbose_decision_trace.py tests/test_warmup_tagging.py -q
```
Expected: all pass (or remaining failures are unrelated path-fixture errors handled in C2).

- [ ] **Step 4: Run full suite**

```bash
pytest tests/ -q 2>&1 | tail -5
```
Expected: failures dropped from 7→0 (errors may remain for fixture path issues — those are C2).

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py
git commit -m "v3-C: thread regime_features into _emit_prediction_rows (Plan B T18 wiring fix)"
```

---

## Task C2: Fix `test_paper_trader_regime.py` fixture path errors

**Files:**
- Modify: `ofi-lab-v3/tests/test_paper_trader_regime.py`

The 2 errors are fixture-path issues, not code defects.

- [ ] **Step 1: Inspect the failing fixtures**

```bash
pytest tests/test_paper_trader_regime.py -v 2>&1 | head -60
```
Note the exact error (likely `FileNotFoundError` on a model artifact or threshold JSON path).

- [ ] **Step 2: Replace hard-coded paths with `tmp_path` fixture**

For each test that errored, change any `Path("/data/...")` or repo-relative path to `tmp_path / "..."` and seed the file inside the test. Example skeleton:

```python
def test_paper_trader_tag_regime_uses_thresholds(tmp_path):
    thresholds_path = tmp_path / "regime_thresholds.json"
    thresholds_path.write_text(json.dumps({
        "BTCUSDT": {"vol_q33": 0.1, "vol_q66": 0.3, "liq_q33": 100, "liq_q66": 500}
    }))
    trader = _make_trader(thresholds_path=thresholds_path)
    tags = trader._tag_regime("BTCUSDT", {"vol": 0.2, "liq": 300})
    assert tags.vol_bucket == "med"
```

- [ ] **Step 3: Re-run**

```bash
pytest tests/test_paper_trader_regime.py -v
```
Expected: all pass, 0 errors.

- [ ] **Step 4: Commit**

```bash
git add ofi-lab-v3/tests/test_paper_trader_regime.py
git commit -m "v3-C: fix tmp_path fixtures in test_paper_trader_regime"
```

---

## Task C3: Wire `_evaluate_paper_filters` into `_run_predictions` (T26 finish)

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py:1208-1240` (after `_emit_prediction_rows`, before the trade-execution branch).
- Test: `ofi-lab-v3/tests/test_paper_filter_wiring.py` (new).

The helper exists at line 615 with signature `_evaluate_paper_filters(self, ctx: dict)`. It needs to be invoked once per prediction with the filter context, and the verdict it returns must gate trade emission.

- [ ] **Step 1: Read the helper to confirm its contract**

```bash
sed -n '615,690p' ofi-lab-v3/trading/paper_trader.py
```
Confirm: takes `ctx` dict with keys like `prediction_id`, `model_name`, `symbol`, `pred_proba`, `confidence_threshold`, `staleness_ms`, `regime_tags`. Returns `FilterVerdict` with `.passed: bool` and `.reasons: list[str]`.

- [ ] **Step 2: Write the failing wiring test**

`ofi-lab-v3/tests/test_paper_filter_wiring.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from trading.paper_trader import PaperTrader

def test_run_predictions_calls_evaluate_paper_filters(make_trader, fake_features):
    trader = make_trader()
    with patch.object(trader, "_evaluate_paper_filters",
                      return_value=MagicMock(passed=True, reasons=[])) as spy:
        trader._run_predictions_for_test_sync(now_ms=1, boundary_ms=900_000)
    assert spy.called, "T26 helper not wired into _run_predictions"
    ctx = spy.call_args.args[0]
    assert "prediction_id" in ctx
    assert "model_name" in ctx
    assert "pred_proba" in ctx

def test_failed_paper_filter_blocks_trade(make_trader):
    trader = make_trader()
    with patch.object(trader, "_evaluate_paper_filters",
                      return_value=MagicMock(passed=False, reasons=["confidence_below_threshold"])):
        trader._run_predictions_for_test_sync(now_ms=1, boundary_ms=900_000)
    assert trader._paper_trade_count == 0
```

- [ ] **Step 3: Run, expect failure**

```bash
pytest tests/test_paper_filter_wiring.py -v
```
Expected: AssertionError "T26 helper not wired".

- [ ] **Step 4: Wire the call**

In `_run_predictions`, immediately after the `_emit_prediction_rows` call (right after the `self._prediction_count += 1` line near 1227), add:

```python
filter_ctx = {
    "prediction_id": prediction_id,
    "model_name": model_name,
    "symbol": symbol,
    "boundary_ms": boundary_ms,
    "pred_proba": pred_proba,
    "pred_direction": pred_direction,
    "above_threshold": above_threshold,
    "warmup": in_warmup,
    "confidence_threshold": _ct,
    "active_filter_mode": _active_filter_mode,
    "p_market": p_market,
    "regime_features": features,
}
verdict = self._evaluate_paper_filters(filter_ctx)
if not verdict.passed:
    self._log.info("paper_filter_blocked",
                   extra={"reasons": verdict.reasons,
                          "prediction_id": prediction_id})
    continue  # skip trade emission for this model/symbol
```

The `continue` must target the model/symbol loop — confirm scope before committing.

- [ ] **Step 5: Re-run wiring test + full suite**

```bash
pytest tests/test_paper_filter_wiring.py tests/ -q 2>&1 | tail -5
```
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_paper_filter_wiring.py
git commit -m "v3-C: wire _evaluate_paper_filters into _run_predictions (Plan B T26)"
```

---

## Task C4: Schedule `refresh_decay_metrics` every N boundaries (T29 finish)

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` — add a counter + call site in the boundary loop near line 1125.
- Test: `ofi-lab-v3/tests/test_decay_refresh_wiring.py` (new).

Decay metrics need to refresh on a schedule. Plan B spec: every 4 boundaries (≈1 hour for 900s window). Use a simple modulo on `_boundary_count`.

- [ ] **Step 1: Write the failing wiring test**

`ofi-lab-v3/tests/test_decay_refresh_wiring.py`:

```python
from unittest.mock import patch
def test_refresh_decay_metrics_called_every_4_boundaries(make_trader):
    trader = make_trader()
    with patch.object(trader, "refresh_decay_metrics") as spy:
        for i in range(8):
            trader._on_boundary(boundary_ms=i * 900_000)
    assert spy.call_count == 2, f"expected 2 calls in 8 boundaries (every 4), got {spy.call_count}"
```

- [ ] **Step 2: Run, expect failure**

```bash
pytest tests/test_decay_refresh_wiring.py -v
```

- [ ] **Step 3: Add counter + call**

In `__init__`, add `self._decay_refresh_interval = 4` and `self._boundary_count = 0`.

In `_on_boundary` (or wherever `_run_predictions` is dispatched per boundary, near line 1125), add after the run:

```python
self._boundary_count += 1
if self._boundary_count % self._decay_refresh_interval == 0:
    try:
        self.refresh_decay_metrics()
    except Exception as e:
        self._log.exception("decay_refresh_failed", extra={"err": str(e)})
```

- [ ] **Step 4: Re-run**

```bash
pytest tests/test_decay_refresh_wiring.py tests/ -q 2>&1 | tail -5
```

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_decay_refresh_wiring.py
git commit -m "v3-C: schedule refresh_decay_metrics every 4 boundaries (Plan B T29)"
```

---

## Task C5: Wire `record_overlap_for_boundary` into the boundary loop (T36 finish)

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` — call after all model predictions for a boundary settle.
- Test: `ofi-lab-v3/tests/test_overlap_wiring.py` (new).

The overlap writer needs the per-(model, symbol, boundary) score map for the boundary just processed.

- [ ] **Step 1: Read helper signature**

```bash
sed -n '699,760p' ofi-lab-v3/trading/paper_trader.py
```
Confirm signature, e.g. `record_overlap_for_boundary(self, boundary_ms: int, scores: dict[tuple[str, str], ModelScore])`.

- [ ] **Step 2: Write failing test**

`ofi-lab-v3/tests/test_overlap_wiring.py`:

```python
from unittest.mock import patch
def test_record_overlap_called_after_boundary(make_trader):
    trader = make_trader()
    with patch.object(trader, "record_overlap_for_boundary") as spy:
        trader._run_predictions_for_test_sync(now_ms=1, boundary_ms=900_000)
    assert spy.called
    args, kwargs = spy.call_args
    assert kwargs.get("boundary_ms", args[0] if args else None) == 900_000
    assert "scores" in kwargs or len(args) >= 2
```

- [ ] **Step 3: Run, expect failure**

```bash
pytest tests/test_overlap_wiring.py -v
```

- [ ] **Step 4: Wire call**

At the end of `_run_predictions`, after the model/symbol loops complete for the boundary, accumulate scores then call:

```python
# Define at top of _run_predictions:
per_boundary_scores: dict = {}

# Inside inner loop, after _evaluate_paper_filters:
per_boundary_scores[(model_name, symbol)] = ModelScore(
    proba=pred_proba, direction=pred_direction, ev=verdict_ev_or_none)

# At end of _run_predictions:
self.record_overlap_for_boundary(boundary_ms=boundary_ms,
                                 scores=per_boundary_scores)
```

Add `from trading.overlap_writer import ModelScore` near the top of the file.

- [ ] **Step 5: Re-run**

```bash
pytest tests/test_overlap_wiring.py tests/ -q 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_overlap_wiring.py
git commit -m "v3-C: wire record_overlap_for_boundary into _run_predictions (Plan B T36)"
```

---

## Task C6: Implement T33 — `evaluate_lifecycle_transitions` periodic call

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` — add periodic call.
- Test: `ofi-lab-v3/tests/test_lifecycle_loop.py` (new).

The lifecycle FSM exists in `storage/lifecycle.py`. It needs to run every M boundaries (spec: every 16, ≈4 hours at 900s).

- [ ] **Step 1: Confirm the FSM entry point**

```bash
grep -n "def evaluate_lifecycle_transitions\|class LifecycleStateMachine" ofi-lab-v3/storage/lifecycle.py
```
Note signature, e.g. `evaluate_lifecycle_transitions(conn, *, now_ms: int) -> list[Transition]`.

- [ ] **Step 2: Write failing test**

`ofi-lab-v3/tests/test_lifecycle_loop.py`:

```python
from unittest.mock import patch

def test_lifecycle_evaluated_every_16_boundaries(make_trader):
    trader = make_trader()
    with patch("trading.paper_trader.evaluate_lifecycle_transitions") as spy:
        spy.return_value = []
        for i in range(32):
            trader._on_boundary(boundary_ms=i * 900_000)
    assert spy.call_count == 2

def test_lifecycle_transitions_persisted(make_trader, db_conn):
    trader = make_trader()
    db_conn.execute("INSERT INTO decay_metrics (model_name, symbol, ewma_ev, "
                    "ewma_brier, psi, ts_ms) VALUES ('h60_xrp', 'XRPUSDT', 0.005, 0.25, 0.05, 1)")
    db_conn.commit()
    for i in range(16):
        trader._on_boundary(boundary_ms=i * 900_000)
    state = db_conn.execute(
        "SELECT lifecycle_state FROM model_registry WHERE name='h60_xrp'"
    ).fetchone()[0]
    assert state == "suspended"

def test_baseline_model_never_suspended(make_trader, db_conn):
    trader = make_trader()
    db_conn.execute("INSERT INTO decay_metrics (model_name, symbol, ewma_ev, "
                    "ewma_brier, psi, ts_ms) VALUES ('h300_btc', 'BTCUSDT', -0.5, 0.5, 0.5, 1)")
    db_conn.commit()
    for i in range(16):
        trader._on_boundary(boundary_ms=i * 900_000)
    state = db_conn.execute(
        "SELECT lifecycle_state FROM model_registry WHERE name='h300_btc'"
    ).fetchone()[0]
    assert state == "active", "baseline must never suspend"
```

- [ ] **Step 3: Run, expect failure**

```bash
pytest tests/test_lifecycle_loop.py -v
```

- [ ] **Step 4: Wire the call**

In `paper_trader.py`, add import at top:

```python
from storage.lifecycle import evaluate_lifecycle_transitions
```

In `__init__`: `self._lifecycle_interval = 16`.

After the decay-refresh block in `_on_boundary`:

```python
if self._boundary_count % self._lifecycle_interval == 0:
    try:
        transitions = evaluate_lifecycle_transitions(
            self._conn, now_ms=int(time.time() * 1000))
        for t in transitions:
            self._log.info("lifecycle_transition", extra=t._asdict())
            self._model_registry.reload(reason="lifecycle_transition")
    except Exception as e:
        self._log.exception("lifecycle_eval_failed", extra={"err": str(e)})
```

- [ ] **Step 5: Run all three tests**

```bash
pytest tests/test_lifecycle_loop.py -v
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_lifecycle_loop.py
git commit -m "v3-C: schedule lifecycle FSM every 16 boundaries with baseline protection (Plan B T33)"
```

---

## Task C7: Implement T34 — paper-active vs live-status independence test

**Files:**
- Test: `ofi-lab-v3/tests/test_paper_live_independence.py` (new).
- Modify (if methods absent): `ofi-lab-v3/storage/model_registry.py`.

Spec invariant: a model can be `paper_active=True, live_eligible=False` (the normal pre-promotion state) and `paper_active=False, live_eligible=False` (suspended), but flipping one must not auto-flip the other.

- [ ] **Step 1: Write the test**

```python
def test_disabling_live_does_not_disable_paper(db_conn, registry):
    registry.set_live(model="h60_xrp", enabled=True, by="op")
    registry.set_paper(model="h60_xrp", enabled=True, by="op")
    registry.set_live(model="h60_xrp", enabled=False, by="op")
    row = registry.get("h60_xrp")
    assert row.paper_active is True
    assert row.live_eligible is False

def test_disabling_paper_does_not_disable_live(db_conn, registry):
    registry.set_paper(model="h60_xrp", enabled=True, by="op")
    registry.set_live(model="h60_xrp", enabled=True, by="op")
    registry.set_paper(model="h60_xrp", enabled=False, by="op")
    row = registry.get("h60_xrp")
    assert row.live_eligible is True
    assert row.paper_active is False

def test_lifecycle_suspend_disables_both(db_conn, registry):
    registry.set_paper(model="h60_xrp", enabled=True, by="op")
    registry.set_live(model="h60_xrp", enabled=True, by="op")
    registry.suspend(model="h60_xrp", reason="ev_below_threshold")
    row = registry.get("h60_xrp")
    assert row.paper_active is False
    assert row.live_eligible is False
```

- [ ] **Step 2: Run; if `set_paper`/`set_live`/`suspend` missing, add them**

```bash
pytest tests/test_paper_live_independence.py -v
```

If absent, append to `storage/model_registry.py`:

```python
def set_paper(self, *, model: str, enabled: bool, by: str) -> None:
    self._conn.execute(
        "UPDATE model_registry SET paper_active=? WHERE name=?",
        (1 if enabled else 0, model))
    self._audit(model, "enable_paper" if enabled else "disable_paper", by, None)

def set_live(self, *, model: str, enabled: bool, by: str) -> None:
    self._conn.execute(
        "UPDATE model_registry SET live_eligible=? WHERE name=?",
        (1 if enabled else 0, model))
    self._audit(model, "enable_live" if enabled else "disable_live", by, None)

def suspend(self, *, model: str, reason: str) -> None:
    row = self._conn.execute(
        "SELECT is_baseline FROM model_registry WHERE name=?", (model,)).fetchone()
    if row and row["is_baseline"]:
        return  # baseline never suspended
    self._conn.execute(
        "UPDATE model_registry SET paper_active=0, live_eligible=0, "
        "lifecycle_state='suspended' WHERE name=?", (model,))
    self._audit(model, "suspend", "lifecycle_fsm", reason)
```

- [ ] **Step 3: Commit**

```bash
git add ofi-lab-v3/tests/test_paper_live_independence.py ofi-lab-v3/storage/model_registry.py
git commit -m "v3-C: test paper/live independence invariant + registry setters (Plan B T34)"
```

---

## Task C8: Implement T37 — dynamic XRP range integration test

**Files:**
- Test: `ofi-lab-v3/tests/test_dynamic_range_integration.py` (new).
- Modify (if absent): `ofi-lab-v3/trading/paper_trader.py` — add `refresh_price_ranges`.

Verifies `data/range_computer.py` output flows into the live feature pipeline correctly (Steering 10g fix).

- [ ] **Step 1: Write the test**

```python
import sqlite3
from data.range_computer import compute_mid_price_range

def test_xrp_range_recomputes_when_price_drifts(db_conn):
    db_conn.execute(
        "INSERT INTO klines (symbol, ts_ms, open, high, low, close, volume) "
        "VALUES ('XRPUSDT', 1, 0.5, 0.51, 0.49, 0.50, 100)")
    rng_v1 = compute_mid_price_range("XRPUSDT", db_conn, lookback_days=1)

    db_conn.execute(
        "INSERT INTO klines VALUES ('XRPUSDT', 86_400_000, 2.5, 2.6, 2.4, 2.55, 200)")
    rng_v2 = compute_mid_price_range("XRPUSDT", db_conn, lookback_days=1)

    assert rng_v2.high > rng_v1.high * 2

def test_paper_trader_uses_dynamic_range(make_trader_with_xrp):
    trader = make_trader_with_xrp()
    trader.refresh_price_ranges()
    assert "XRPUSDT" in trader._price_ranges
    assert trader._price_ranges["XRPUSDT"].high > 0
```

- [ ] **Step 2: Add `refresh_price_ranges` if missing**

```python
def refresh_price_ranges(self) -> None:
    from data.range_computer import compute_mid_price_range
    self._price_ranges = {}
    for symbol in self._tracked_symbols:
        try:
            self._price_ranges[symbol] = compute_mid_price_range(
                symbol, self._conn, lookback_days=7)
        except Exception as e:
            self._log.exception("range_compute_failed",
                                extra={"symbol": symbol, "err": str(e)})
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/test_dynamic_range_integration.py -v
git add -A
git commit -m "v3-C: dynamic price range integration test (Plan B T37)"
```

---

## Task C9: Rename `test_psi_integration2.py` → `test_psi_integration.py`

**Files:**
- Rename: `ofi-lab-v3/tests/test_psi_integration2.py` → `ofi-lab-v3/tests/test_psi_integration.py`.

- [ ] **Step 1: Confirm no canonical file exists**

```bash
ls ofi-lab-v3/tests/test_psi_integration*.py
```
Expected: only `test_psi_integration2.py`.

- [ ] **Step 2: Rename + commit**

```bash
git mv ofi-lab-v3/tests/test_psi_integration2.py ofi-lab-v3/tests/test_psi_integration.py
pytest ofi-lab-v3/tests/test_psi_integration.py -v
git add -A
git commit -m "v3-C: rename test_psi_integration2.py to canonical name"
```

---

## Task C10: Cleanup phase verification gate

**Files:** none — verification only.

- [ ] **Step 1: Full suite**

```bash
cd ofi-lab-v3 && source .venv/bin/activate && pytest tests/ -q 2>&1 | tail -10
```
Expected: 0 failures, 0 errors. Pass count should be ≥ baseline_267 + 8 new tests across C3/C4/C5/C6/C7/C8 ≈ 285+.

- [ ] **Step 2: Tag**

```bash
git tag v3-plan-c-cleanup-complete
```

---

# Phase 2 — Dashboard Surface

## Task C11: Admin auth — confirmation token issuance

**Files:**
- Create: `ofi-lab-v3/dashboard_api/services/admin_auth.py`
- Test: `ofi-lab-v3/tests/test_admin_auth.py`

Live-trading and rollback mutations require a fresh confirmation token (5-minute TTL) issued via `POST /api/admin/confirm_intent`. This prevents accidental clicks.

- [ ] **Step 1: Write the failing test**

```python
import time, pytest
from dashboard_api.services.admin_auth import (
    issue_confirmation_token, verify_confirmation_token, ConfirmationError
)

def test_issued_token_verifies_within_ttl():
    tok = issue_confirmation_token(action="enable_live", target="h60_xrp", by="op")
    payload = verify_confirmation_token(tok, action="enable_live", target="h60_xrp")
    assert payload["by"] == "op"

def test_wrong_action_rejects():
    tok = issue_confirmation_token(action="enable_live", target="h60_xrp", by="op")
    with pytest.raises(ConfirmationError):
        verify_confirmation_token(tok, action="rollback", target="h60_xrp")

def test_expired_token_rejects(monkeypatch):
    tok = issue_confirmation_token(action="enable_live", target="h60_xrp", by="op")
    monkeypatch.setattr("time.time", lambda: time.time() + 600)
    with pytest.raises(ConfirmationError):
        verify_confirmation_token(tok, action="enable_live", target="h60_xrp")

def test_token_single_use():
    tok = issue_confirmation_token(action="enable_live", target="h60_xrp", by="op")
    verify_confirmation_token(tok, action="enable_live", target="h60_xrp")
    with pytest.raises(ConfirmationError):
        verify_confirmation_token(tok, action="enable_live", target="h60_xrp")
```

- [ ] **Step 2: Implement**

`ofi-lab-v3/dashboard_api/services/admin_auth.py`:

```python
"""Confirmation token issuance for high-risk admin actions.

5-minute TTL, single-use, signed with HMAC-SHA256.
"""
from __future__ import annotations
import hmac, hashlib, json, os, time, secrets
from threading import Lock

class ConfirmationError(Exception):
    pass

_SECRET = os.environ.get("V3_ADMIN_SECRET", "").encode() or secrets.token_bytes(32)
_TTL_SECONDS = 300
_USED_TOKENS: set[str] = set()
_LOCK = Lock()

def _sign(payload: bytes) -> str:
    return hmac.new(_SECRET, payload, hashlib.sha256).hexdigest()

def issue_confirmation_token(*, action: str, target: str, by: str) -> str:
    body = {"action": action, "target": target, "by": by,
            "iat": int(time.time()), "nonce": secrets.token_hex(8)}
    payload = json.dumps(body, sort_keys=True).encode()
    sig = _sign(payload)
    return f"{payload.hex()}.{sig}"

def verify_confirmation_token(token: str, *, action: str, target: str) -> dict:
    try:
        payload_hex, sig = token.split(".")
        payload = bytes.fromhex(payload_hex)
    except ValueError:
        raise ConfirmationError("malformed token")
    if not hmac.compare_digest(sig, _sign(payload)):
        raise ConfirmationError("bad signature")
    body = json.loads(payload)
    if body["action"] != action or body["target"] != target:
        raise ConfirmationError("action/target mismatch")
    if time.time() - body["iat"] > _TTL_SECONDS:
        raise ConfirmationError("expired")
    with _LOCK:
        if token in _USED_TOKENS:
            raise ConfirmationError("token already used")
        _USED_TOKENS.add(token)
    return body
```

- [ ] **Step 3: Run**

```bash
pytest tests/test_admin_auth.py -v
```
Expected: 4 pass.

- [ ] **Step 4: Commit**

```bash
git add ofi-lab-v3/dashboard_api/services/admin_auth.py ofi-lab-v3/tests/test_admin_auth.py
git commit -m "v3-C: HMAC confirmation tokens for high-risk admin actions"
```

---

## Task C12: `/api/models/list` and `/api/models/<name>` (read)

**Files:**
- Create: `ofi-lab-v3/dashboard_api/routers/models_admin.py` (read endpoints first; mutations in C13).
- Test: `ofi-lab-v3/tests/test_dashboard_models_admin.py`.

- [ ] **Step 1: Failing test**

```python
from fastapi.testclient import TestClient
from dashboard_api.main import app

def test_list_models_returns_all_rows(seeded_db):
    c = TestClient(app)
    r = c.get("/api/models/list")
    assert r.status_code == 200
    data = r.json()
    assert "models" in data
    names = [m["name"] for m in data["models"]]
    assert "h300_btc" in names
    h300 = next(m for m in data["models"] if m["name"] == "h300_btc")
    assert h300["is_baseline"] is True
    assert "lifecycle_state" in h300
    assert "paper_active" in h300
    assert "live_eligible" in h300
    assert "ewma_ev" in h300
    assert "psi" in h300

def test_get_model_detail_returns_overlap_and_calibration(seeded_db):
    c = TestClient(app)
    r = c.get("/api/models/h300_btc")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "h300_btc"
    assert "calibration_summary" in body
    assert "overlap" in body
    assert "recent_audit" in body
```

- [ ] **Step 2: Implement router**

```python
# ofi-lab-v3/dashboard_api/routers/models_admin.py
from fastapi import APIRouter, HTTPException, Depends
from services.db import get_conn  # existing helper

router = APIRouter(prefix="/api/models", tags=["models-admin"])

@router.get("/list")
def list_models(conn=Depends(get_conn)):
    rows = conn.execute("""
        SELECT m.name, m.is_baseline, m.lifecycle_state,
               m.paper_active, m.live_eligible,
               m.symbol, m.horizon, m.generation, m.created_at,
               d.ewma_ev, d.ewma_brier, d.psi
          FROM model_registry m
          LEFT JOIN (
              SELECT model_name,
                     ewma_ev, ewma_brier, psi,
                     ROW_NUMBER() OVER (PARTITION BY model_name ORDER BY ts_ms DESC) rn
                FROM decay_metrics
          ) d ON d.model_name = m.name AND d.rn = 1
         ORDER BY m.is_baseline DESC, m.name
    """).fetchall()
    return {"models": [dict(r) for r in rows]}

@router.get("/{name}")
def get_model(name: str, conn=Depends(get_conn)):
    row = conn.execute("SELECT * FROM model_registry WHERE name = ?", (name,)).fetchone()
    if not row:
        raise HTTPException(404, f"model {name} not found")
    overlap = conn.execute(
        "SELECT * FROM model_overlap WHERE model_a = ? OR model_b = ? "
        "ORDER BY boundary_ms DESC LIMIT 50", (name, name)).fetchall()
    calib = conn.execute(
        "SELECT bin_lo, bin_hi, observed_freq, n FROM calibration_bins "
        "WHERE model_name = ? ORDER BY bin_lo", (name,)).fetchall()
    audit = conn.execute(
        "SELECT * FROM model_audit WHERE model_name = ? "
        "ORDER BY ts_ms DESC LIMIT 25", (name,)).fetchall()
    return {**dict(row),
            "overlap": [dict(o) for o in overlap],
            "calibration_summary": [dict(c) for c in calib],
            "recent_audit": [dict(a) for a in audit]}
```

- [ ] **Step 3: Register in `main.py`**

In `ofi-lab-v3/dashboard_api/main.py`, add to imports + `app.include_router(models_admin.router)`.

- [ ] **Step 4: Run**

```bash
pytest tests/test_dashboard_models_admin.py::test_list_models_returns_all_rows tests/test_dashboard_models_admin.py::test_get_model_detail_returns_overlap_and_calibration -v
```

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/dashboard_api/routers/models_admin.py ofi-lab-v3/dashboard_api/main.py ofi-lab-v3/tests/test_dashboard_models_admin.py
git commit -m "v3-C: GET /api/models/list and /api/models/{name}"
```

---

## Task C13: `/api/models/<name>/{enable_paper,disable_paper}` mutations

**Files:**
- Modify: `ofi-lab-v3/dashboard_api/routers/models_admin.py` (append).
- Test: `ofi-lab-v3/tests/test_dashboard_models_admin.py` (append).

Paper toggles do **not** require confirmation tokens — they're low-risk.

- [ ] **Step 1: Failing tests**

```python
def test_enable_paper(seeded_db):
    c = TestClient(app)
    r = c.post("/api/models/h60_xrp/enable_paper", json={"by": "op"})
    assert r.status_code == 200
    assert r.json()["paper_active"] is True
    audit = seeded_db.execute(
        "SELECT * FROM model_audit WHERE model_name='h60_xrp' ORDER BY ts_ms DESC"
    ).fetchone()
    assert audit["action"] == "enable_paper"

def test_disable_paper(seeded_db):
    c = TestClient(app)
    c.post("/api/models/h60_xrp/enable_paper", json={"by": "op"})
    r = c.post("/api/models/h60_xrp/disable_paper", json={"by": "op", "reason": "drift"})
    assert r.status_code == 200
    assert r.json()["paper_active"] is False

def test_paper_toggle_does_not_affect_live(seeded_db):
    c = TestClient(app)
    seeded_db.execute("UPDATE model_registry SET live_eligible=1 WHERE name='h60_xrp'")
    seeded_db.commit()
    c.post("/api/models/h60_xrp/disable_paper", json={"by": "op"})
    row = seeded_db.execute(
        "SELECT live_eligible FROM model_registry WHERE name='h60_xrp'").fetchone()
    assert row["live_eligible"] == 1
```

- [ ] **Step 2: Implement**

Append to `routers/models_admin.py`:

```python
from pydantic import BaseModel
import time

class ToggleRequest(BaseModel):
    by: str
    reason: str | None = None

def _audit(conn, name, action, by, reason, before, after):
    conn.execute(
        "INSERT INTO model_audit (ts_ms, model_name, action, actor, reason, before_state, after_state) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(time.time()*1000), name, action, by, reason or "", before, after))
    conn.commit()

@router.post("/{name}/enable_paper")
def enable_paper(name: str, req: ToggleRequest, conn=Depends(get_conn)):
    row = conn.execute("SELECT paper_active FROM model_registry WHERE name=?", (name,)).fetchone()
    if not row:
        raise HTTPException(404)
    conn.execute("UPDATE model_registry SET paper_active=1 WHERE name=?", (name,))
    _audit(conn, name, "enable_paper", req.by, req.reason, row["paper_active"], 1)
    return {"name": name, "paper_active": True}

@router.post("/{name}/disable_paper")
def disable_paper(name: str, req: ToggleRequest, conn=Depends(get_conn)):
    row = conn.execute("SELECT paper_active FROM model_registry WHERE name=?", (name,)).fetchone()
    if not row:
        raise HTTPException(404)
    conn.execute("UPDATE model_registry SET paper_active=0 WHERE name=?", (name,))
    _audit(conn, name, "disable_paper", req.by, req.reason, row["paper_active"], 0)
    return {"name": name, "paper_active": False}
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/test_dashboard_models_admin.py -v
git add -A && git commit -m "v3-C: POST enable_paper/disable_paper with audit"
```

---

## Task C14: `/api/models/<name>/{enable_live,disable_live}` with confirmation

**Files:**
- Modify: `routers/models_admin.py`, test file.

Live toggles **require** a confirmation token from `/api/admin/confirm_intent` and **enforce baseline + eligibility checks**.

- [ ] **Step 1: Failing tests**

```python
def test_enable_live_requires_confirmation_token(seeded_db):
    c = TestClient(app)
    r = c.post("/api/models/h60_xrp/enable_live", json={"by": "op"})
    assert r.status_code == 400
    assert "confirmation" in r.json()["detail"].lower()

def test_enable_live_with_valid_token_succeeds(seeded_db, eligible_h60_xrp):
    c = TestClient(app)
    tok_resp = c.post("/api/admin/confirm_intent",
                      json={"action": "enable_live", "target": "h60_xrp", "by": "op"})
    tok = tok_resp.json()["token"]
    r = c.post("/api/models/h60_xrp/enable_live",
               json={"by": "op", "confirmation_token": tok})
    assert r.status_code == 200
    assert r.json()["live_eligible"] is True

def test_enable_live_blocked_when_ineligible(seeded_db, ineligible_h60_xrp):
    """Model with insufficient paper history can't go live."""
    c = TestClient(app)
    tok_resp = c.post("/api/admin/confirm_intent",
                      json={"action": "enable_live", "target": "h60_xrp", "by": "op"})
    tok = tok_resp.json()["token"]
    r = c.post("/api/models/h60_xrp/enable_live",
               json={"by": "op", "confirmation_token": tok})
    assert r.status_code == 412  # precondition failed
    assert "eligibility" in r.json()["detail"].lower()
```

- [ ] **Step 2: Implement**

Add to `routers/models_admin.py`:

```python
from dashboard_api.services.admin_auth import (
    issue_confirmation_token, verify_confirmation_token, ConfirmationError
)
from filters.live_eligibility import check_live_eligibility

class LiveToggleRequest(ToggleRequest):
    confirmation_token: str | None = None

# Top-level admin router for /api/admin/confirm_intent
admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

class IntentRequest(BaseModel):
    action: str
    target: str
    by: str

@admin_router.post("/confirm_intent")
def confirm_intent(req: IntentRequest):
    return {"token": issue_confirmation_token(
        action=req.action, target=req.target, by=req.by)}

@router.post("/{name}/enable_live")
def enable_live(name: str, req: LiveToggleRequest, conn=Depends(get_conn)):
    if not req.confirmation_token:
        raise HTTPException(400, "confirmation_token required for live toggles")
    try:
        verify_confirmation_token(req.confirmation_token,
                                  action="enable_live", target=name)
    except ConfirmationError as e:
        raise HTTPException(400, f"confirmation failed: {e}")
    row = conn.execute("SELECT * FROM model_registry WHERE name=?", (name,)).fetchone()
    if not row:
        raise HTTPException(404)
    elig = check_live_eligibility(conn, name)
    if not elig.eligible:
        raise HTTPException(412, f"eligibility checks failed: {', '.join(elig.reasons)}")
    conn.execute("UPDATE model_registry SET live_eligible=1 WHERE name=?", (name,))
    _audit(conn, name, "enable_live", req.by, req.reason, row["live_eligible"], 1)
    return {"name": name, "live_eligible": True}

@router.post("/{name}/disable_live")
def disable_live(name: str, req: LiveToggleRequest, conn=Depends(get_conn)):
    # disabling does NOT require token — always safe to stop
    row = conn.execute("SELECT live_eligible FROM model_registry WHERE name=?", (name,)).fetchone()
    if not row:
        raise HTTPException(404)
    conn.execute("UPDATE model_registry SET live_eligible=0 WHERE name=?", (name,))
    _audit(conn, name, "disable_live", req.by, req.reason, row["live_eligible"], 0)
    return {"name": name, "live_eligible": False}
```

Register `admin_router` in `main.py`.

- [ ] **Step 3: Run + commit**

```bash
pytest tests/test_dashboard_models_admin.py -v
git add -A && git commit -m "v3-C: POST enable_live/disable_live with token + eligibility gate"
```

---

## Task C15: `/api/models/<name>/reload` with generation increment

**Files:**
- Modify: `routers/models_admin.py`, test file.

Reload is medium-risk: it bumps generation, drains pending queue. Requires confirmation token.

- [ ] **Step 1: Failing test**

```python
def test_reload_increments_generation(seeded_db):
    c = TestClient(app)
    tok = c.post("/api/admin/confirm_intent",
                 json={"action": "reload", "target": "h60_xrp", "by": "op"}
                 ).json()["token"]
    r = c.post("/api/models/h60_xrp/reload",
               json={"by": "op", "confirmation_token": tok})
    assert r.status_code == 200
    assert r.json()["generation"] >= 1
```

- [ ] **Step 2: Implement**

```python
@router.post("/{name}/reload")
def reload_model(name: str, req: LiveToggleRequest, conn=Depends(get_conn)):
    if not req.confirmation_token:
        raise HTTPException(400, "confirmation_token required for reload")
    try:
        verify_confirmation_token(req.confirmation_token,
                                  action="reload", target=name)
    except ConfirmationError as e:
        raise HTTPException(400, str(e))
    from storage.model_registry import ModelRegistry
    reg = ModelRegistry(conn=conn)
    new_gen = reg.reload(model_name=name, reason=f"manual:{req.by}")
    return {"name": name, "generation": new_gen}
```

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "v3-C: POST /api/models/{name}/reload"
```

---

## Task C16: `/api/overlap` endpoint

**Files:**
- Create: `routers/overlap.py`, test file.

Returns recent `model_overlap` rows for the matrix view.

- [ ] **Step 1: Failing test**

```python
def test_overlap_returns_recent_pairs(seeded_db_with_overlap):
    c = TestClient(app)
    r = c.get("/api/overlap?limit=20")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) > 0
    assert {"model_a", "model_b", "agree", "ewma_agree", "boundary_ms"} <= set(rows[0])
```

- [ ] **Step 2: Implement**

```python
# routers/overlap.py
from fastapi import APIRouter, Depends
from services.db import get_conn
router = APIRouter(prefix="/api/overlap", tags=["overlap"])

@router.get("")
def get_overlap(limit: int = 50, model: str | None = None, conn=Depends(get_conn)):
    if model:
        rows = conn.execute(
            "SELECT * FROM model_overlap WHERE model_a=? OR model_b=? "
            "ORDER BY boundary_ms DESC LIMIT ?", (model, model, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM model_overlap ORDER BY boundary_ms DESC LIMIT ?",
            (limit,)).fetchall()
    return {"rows": [dict(r) for r in rows]}
```

- [ ] **Step 3: Register in main.py, run, commit**

```bash
pytest tests/test_dashboard_overlap.py -v
git add -A && git commit -m "v3-C: GET /api/overlap"
```

---

## Task C17: `/api/regime/current` and `/api/regime/thresholds`

**Files:**
- Create: `routers/regime.py`, test file.

- [ ] **Step 1: Failing test**

```python
def test_regime_current_returns_per_symbol_tags(seeded_db_with_regime):
    c = TestClient(app)
    r = c.get("/api/regime/current?symbol=BTCUSDT")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["vol_bucket"] in {"low", "med", "high"}
    assert body["liq_bucket"] in {"low", "med", "high"}

def test_regime_thresholds_returns_quartiles(seeded_db_with_regime):
    c = TestClient(app)
    r = c.get("/api/regime/thresholds")
    assert r.status_code == 200
    body = r.json()
    assert "BTCUSDT" in body["thresholds"]
    btc = body["thresholds"]["BTCUSDT"]
    assert "vol_q33" in btc and "vol_q66" in btc
```

- [ ] **Step 2: Implement**

```python
# routers/regime.py
from fastapi import APIRouter, Depends, HTTPException
from services.db import get_conn
from regime.tagger import compute_regime
import json

router = APIRouter(prefix="/api/regime", tags=["regime"])

@router.get("/current")
def current(symbol: str, conn=Depends(get_conn)):
    row = conn.execute(
        "SELECT * FROM regime_features_latest WHERE symbol=?", (symbol,)).fetchone()
    if not row:
        raise HTTPException(404, f"no regime data for {symbol}")
    th = conn.execute(
        "SELECT thresholds_json FROM regime_thresholds WHERE symbol=?", (symbol,)).fetchone()
    thresholds = json.loads(th["thresholds_json"]) if th else {}
    tags = compute_regime(symbol, dict(row), {symbol: thresholds})
    return {"symbol": symbol, **tags._asdict()}

@router.get("/thresholds")
def thresholds(conn=Depends(get_conn)):
    rows = conn.execute("SELECT symbol, thresholds_json FROM regime_thresholds").fetchall()
    return {"thresholds": {r["symbol"]: json.loads(r["thresholds_json"]) for r in rows}}
```

- [ ] **Step 3: Register, run, commit**

```bash
pytest tests/test_dashboard_regime.py -v
git add -A && git commit -m "v3-C: GET /api/regime/current and /api/regime/thresholds"
```

---

## Task C18: `/api/calibration/<model>` endpoint

**Files:**
- Create: `routers/calibration.py`, test file.

- [ ] **Step 1: Failing test**

```python
def test_calibration_returns_bins_for_model(seeded_db_with_calibration):
    c = TestClient(app)
    r = c.get("/api/calibration/h300_btc")
    assert r.status_code == 200
    body = r.json()
    assert body["model_name"] == "h300_btc"
    assert len(body["bins"]) > 0
    assert {"bin_lo", "bin_hi", "observed_freq", "n"} <= set(body["bins"][0])
    assert "brier" in body
    assert "log_loss" in body
```

- [ ] **Step 2: Implement**

```python
# routers/calibration.py
from fastapi import APIRouter, Depends, HTTPException
from services.db import get_conn
router = APIRouter(prefix="/api/calibration", tags=["calibration"])

@router.get("/{model_name}")
def calibration(model_name: str, conn=Depends(get_conn)):
    bins = conn.execute(
        "SELECT bin_lo, bin_hi, observed_freq, n "
        "FROM calibration_bins WHERE model_name=? ORDER BY bin_lo",
        (model_name,)).fetchall()
    if not bins:
        raise HTTPException(404)
    summary = conn.execute(
        "SELECT brier, log_loss, n_obs FROM calibration_summary WHERE model_name=?",
        (model_name,)).fetchone()
    return {"model_name": model_name,
            "bins": [dict(b) for b in bins],
            "brier": summary["brier"] if summary else None,
            "log_loss": summary["log_loss"] if summary else None,
            "n_obs": summary["n_obs"] if summary else 0}
```

- [ ] **Step 3: Register, run, commit**

```bash
pytest tests/test_dashboard_calibration.py -v
git add -A && git commit -m "v3-C: GET /api/calibration/{model}"
```

---

## Task C19: Kill switch — POST `/api/kill_switch` and persistent state

**Files:**
- Create: `services/kill_switch_state.py`, `routers/kill_switch.py`, test file.

Implements Steering 10l: kill switch persists to `/data/kill_switch.json` and **survives restart**. After restart, the state remains "killed" until an operator hits `confirm_resume` with a fresh confirmation token.

- [ ] **Step 1: Failing tests**

```python
def test_kill_switch_persists_to_disk(tmp_path, monkeypatch):
    from dashboard_api.services import kill_switch_state as ks
    monkeypatch.setattr(ks, "STATE_PATH", tmp_path / "kill.json")
    ks.engage(reason="drawdown_exceeded", by="op")
    assert ks.is_engaged() is True
    state = ks.read_state()
    assert state["reason"] == "drawdown_exceeded"

def test_engage_kill_switch_blocks_predictions(tmp_path, monkeypatch, make_trader):
    from dashboard_api.services import kill_switch_state as ks
    monkeypatch.setattr(ks, "STATE_PATH", tmp_path / "kill.json")
    trader = make_trader()
    ks.engage(reason="manual", by="op")
    trader._on_boundary(boundary_ms=900_000)
    assert trader._prediction_count == 0

def test_resume_requires_confirmation_token(seeded_db, tmp_path, monkeypatch):
    from dashboard_api.services import kill_switch_state as ks
    monkeypatch.setattr(ks, "STATE_PATH", tmp_path / "kill.json")
    ks.engage(reason="manual", by="op")
    c = TestClient(app)
    r = c.post("/api/kill_switch/confirm_resume", json={"by": "op"})
    assert r.status_code == 400
    tok = c.post("/api/admin/confirm_intent",
                 json={"action": "kill_switch_resume", "target": "global", "by": "op"}
                 ).json()["token"]
    r = c.post("/api/kill_switch/confirm_resume",
               json={"by": "op", "confirmation_token": tok})
    assert r.status_code == 200
    assert ks.is_engaged() is False
```

- [ ] **Step 2: Implement state**

```python
# dashboard_api/services/kill_switch_state.py
import json, os, time
from pathlib import Path
from threading import Lock

STATE_PATH = Path(os.environ.get("V3_KILL_SWITCH_PATH", "/data/kill_switch.json"))
_LOCK = Lock()

def is_engaged() -> bool:
    return STATE_PATH.exists()

def read_state() -> dict | None:
    if not STATE_PATH.exists():
        return None
    return json.loads(STATE_PATH.read_text())

def engage(*, reason: str, by: str) -> None:
    with _LOCK:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({
            "engaged_at_ms": int(time.time()*1000),
            "reason": reason, "by": by}))

def disengage(*, by: str) -> None:
    with _LOCK:
        if STATE_PATH.exists():
            archive = STATE_PATH.with_suffix(f".resumed.{int(time.time())}.json")
            STATE_PATH.rename(archive)
```

- [ ] **Step 3: Implement router**

```python
# dashboard_api/routers/kill_switch.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dashboard_api.services import kill_switch_state as ks
from dashboard_api.services.admin_auth import verify_confirmation_token, ConfirmationError

router = APIRouter(prefix="/api/kill_switch", tags=["kill-switch"])

class EngageReq(BaseModel):
    reason: str
    by: str

class ResumeReq(BaseModel):
    by: str
    confirmation_token: str | None = None

@router.get("")
def status():
    return {"engaged": ks.is_engaged(), "state": ks.read_state()}

@router.post("")
def engage(req: EngageReq):
    ks.engage(reason=req.reason, by=req.by)
    return {"engaged": True, "reason": req.reason}

@router.post("/confirm_resume")
def confirm_resume(req: ResumeReq):
    if not req.confirmation_token:
        raise HTTPException(400, "confirmation_token required")
    try:
        verify_confirmation_token(req.confirmation_token,
                                  action="kill_switch_resume", target="global")
    except ConfirmationError as e:
        raise HTTPException(400, str(e))
    ks.disengage(by=req.by)
    return {"engaged": False}
```

- [ ] **Step 4: Wire into PaperTrader**

In `_on_boundary`, before any prediction work:

```python
from dashboard_api.services import kill_switch_state as ks
if ks.is_engaged():
    self._log.warning("kill_switch_engaged_skipping_boundary",
                      extra=ks.read_state() or {})
    return
```

- [ ] **Step 5: Run + commit**

```bash
pytest tests/test_dashboard_kill_switch.py -v
git add -A && git commit -m "v3-C: persistent kill switch with post-restart confirm (Steering 10l)"
```

---

## Task C20: Rollback flow — POST `/api/models/<name>/rollback`

**Files:**
- Modify: `routers/models_admin.py`, test file.

Steering 10k: roll a model back to its previous artifact generation. Requires confirmation token + writes audit row.

- [ ] **Step 1: Failing test**

```python
def test_rollback_restores_previous_generation(seeded_db_with_two_generations):
    c = TestClient(app)
    tok = c.post("/api/admin/confirm_intent",
                 json={"action": "rollback", "target": "h60_xrp", "by": "op"}
                 ).json()["token"]
    before = seeded_db_with_two_generations.execute(
        "SELECT generation FROM model_registry WHERE name='h60_xrp'").fetchone()[0]
    r = c.post("/api/models/h60_xrp/rollback",
               json={"by": "op", "confirmation_token": tok,
                     "target_generation": before - 1})
    assert r.status_code == 200
    assert r.json()["generation"] == before - 1

def test_rollback_blocked_for_baseline(seeded_db):
    c = TestClient(app)
    tok = c.post("/api/admin/confirm_intent",
                 json={"action": "rollback", "target": "h300_btc", "by": "op"}
                 ).json()["token"]
    r = c.post("/api/models/h300_btc/rollback",
               json={"by": "op", "confirmation_token": tok, "target_generation": 0})
    assert r.status_code == 412
    assert "baseline" in r.json()["detail"].lower()
```

- [ ] **Step 2: Implement**

Append to `routers/models_admin.py`:

```python
class RollbackRequest(LiveToggleRequest):
    target_generation: int

@router.post("/{name}/rollback")
def rollback(name: str, req: RollbackRequest, conn=Depends(get_conn)):
    if not req.confirmation_token:
        raise HTTPException(400, "confirmation_token required")
    try:
        verify_confirmation_token(req.confirmation_token,
                                  action="rollback", target=name)
    except ConfirmationError as e:
        raise HTTPException(400, str(e))
    row = conn.execute(
        "SELECT * FROM model_registry WHERE name=?", (name,)).fetchone()
    if not row:
        raise HTTPException(404)
    if row["is_baseline"]:
        raise HTTPException(412, "baseline models cannot be rolled back")
    archive = conn.execute(
        "SELECT artifact_hash FROM model_artifact_archive "
        "WHERE model_name=? AND generation=?", (name, req.target_generation)
    ).fetchone()
    if not archive:
        raise HTTPException(404, f"no archive for generation {req.target_generation}")
    conn.execute(
        "UPDATE model_registry SET artifact_hash=?, generation=? WHERE name=?",
        (archive["artifact_hash"], req.target_generation, name))
    _audit(conn, name, "rollback", req.by,
           req.reason or f"rollback to gen {req.target_generation}",
           row["generation"], req.target_generation)
    from storage.model_registry import ModelRegistry
    ModelRegistry(conn=conn).reload(model_name=name, reason="rollback")
    return {"name": name, "generation": req.target_generation}
```

- [ ] **Step 3: Run + commit**

```bash
pytest tests/test_dashboard_rollback.py -v
git add -A && git commit -m "v3-C: POST /api/models/{name}/rollback (Steering 10k)"
```

---

## Task C21: `/api/audit` endpoint

**Files:**
- Create: `routers/audit.py`, test file.

- [ ] **Step 1: Failing test**

```python
def test_audit_returns_recent_entries(seeded_db_with_audit):
    c = TestClient(app)
    r = c.get("/api/audit?limit=20")
    assert r.status_code == 200
    rows = r.json()["entries"]
    assert len(rows) > 0
    assert {"ts_ms", "model_name", "action", "actor"} <= set(rows[0])

def test_audit_filter_by_model(seeded_db_with_audit):
    c = TestClient(app)
    r = c.get("/api/audit?model=h60_xrp&limit=10")
    assert all(e["model_name"] == "h60_xrp" for e in r.json()["entries"])
```

- [ ] **Step 2: Implement**

```python
# routers/audit.py
from fastapi import APIRouter, Depends
from services.db import get_conn
router = APIRouter(prefix="/api/audit", tags=["audit"])

@router.get("")
def audit(limit: int = 50, model: str | None = None, action: str | None = None,
          conn=Depends(get_conn)):
    sql = "SELECT * FROM model_audit WHERE 1=1"
    params: list = []
    if model:
        sql += " AND model_name=?"
        params.append(model)
    if action:
        sql += " AND action=?"
        params.append(action)
    sql += " ORDER BY ts_ms DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return {"entries": [dict(r) for r in rows]}
```

- [ ] **Step 3: Register, run, commit**

```bash
pytest tests/test_dashboard_audit.py -v
git add -A && git commit -m "v3-C: GET /api/audit"
```

---

## Task C22: `/models` page — Jinja template + model card include

**Files:**
- Create: `dashboard_api/templates/models.html`, `templates/_model_card.html`, `static/css/models.css`, `static/js/models_admin.js`.
- Modify: `routers/pages.py` to add `GET /models` and `GET /_partial/model_card`.

All HTML insertion in JS uses safe DOM construction (`document.createElement`, `textContent`) — never `innerHTML` with API data. Server-rendered partials are Jinja2 (auto-escaped).

- [ ] **Step 1: Add page route**

In `routers/pages.py`:

```python
@router.get("/models", response_class=HTMLResponse)
def models_page(request: Request):
    return templates.TemplateResponse("models.html", {"request": request})

@router.get("/_partial/model_card", response_class=HTMLResponse)
def model_card_partial(request: Request, name: str, conn=Depends(get_conn)):
    row = conn.execute("SELECT * FROM model_registry WHERE name=?", (name,)).fetchone()
    if not row:
        raise HTTPException(404)
    return templates.TemplateResponse("_model_card.html",
                                      {"request": request, "model": dict(row)})
```

- [ ] **Step 2: Create `templates/models.html`**

```html
{% extends "base.html" %}
{% block title %}Models{% endblock %}
{% block content %}
<h1>Model Registry</h1>
<div id="models-grid">Loading…</div>

<dialog id="confirm-modal">
  <form method="dialog">
    <h2 id="confirm-title">Confirm action</h2>
    <p id="confirm-body"></p>
    <label>Reason: <input id="confirm-reason" type="text" required></label>
    <p style="color:#a00">⚠ This action affects live trading. Type "I CONFIRM" to proceed:</p>
    <input id="confirm-phrase" type="text" required>
    <menu>
      <button value="cancel">Cancel</button>
      <button id="confirm-go" value="confirm" type="button">Confirm</button>
    </menu>
  </form>
</dialog>

<link rel="stylesheet" href="/static/css/models.css">
<script src="/static/js/models_admin.js" defer></script>
{% endblock %}
```

- [ ] **Step 3: Create `templates/_model_card.html`**

```html
<div class="model-card {% if model.is_baseline %}baseline{% endif %}"
     data-name="{{ model.name }}">
  <header>
    <h3>{{ model.name }}
      {% if model.is_baseline %}<span class="badge-baseline">🔒 BASELINE</span>{% endif %}
    </h3>
    <span class="lifecycle-{{ model.lifecycle_state }}">{{ model.lifecycle_state }}</span>
  </header>
  <dl>
    <dt>Symbol</dt><dd>{{ model.symbol }}</dd>
    <dt>Horizon</dt><dd>{{ model.horizon }}s</dd>
    <dt>Gen</dt><dd>{{ model.generation }}</dd>
    <dt>EWMA EV</dt><dd>{{ "%.4f"|format(model.ewma_ev or 0) }}</dd>
    <dt>PSI</dt><dd>{{ "%.3f"|format(model.psi or 0) }}</dd>
  </dl>
  <div class="toggles">
    <label>
      <input type="checkbox" class="toggle-paper"
             {% if model.paper_active %}checked{% endif %}> Paper
    </label>
    <label>
      <input type="checkbox" class="toggle-live"
             {% if model.live_eligible %}checked{% endif %}
             {% if model.is_baseline %}disabled title="baseline locked"{% endif %}> Live
    </label>
    <button class="btn-reload" {% if model.is_baseline %}disabled{% endif %}>Reload</button>
    <button class="btn-rollback" {% if model.is_baseline %}disabled{% endif %}>Rollback</button>
  </div>
</div>
```

- [ ] **Step 4: Create `static/js/models_admin.js` (safe DOM, no innerHTML)**

```javascript
async function loadModels() {
  const r = await fetch("/api/models/list");
  const {models} = await r.json();
  const grid = document.getElementById("models-grid");
  // Clear children safely
  while (grid.firstChild) grid.removeChild(grid.firstChild);
  for (const m of models) {
    // Fetch server-rendered (Jinja-escaped) card HTML and parse via DOMParser,
    // then adopt into the live DOM. We trust server-rendered output because
    // Jinja autoescapes; we never compose HTML strings from API JSON in JS.
    const cardResp = await fetch(`/_partial/model_card?name=${encodeURIComponent(m.name)}`);
    const cardHtml = await cardResp.text();
    const doc = new DOMParser().parseFromString(cardHtml, "text/html");
    const card = doc.body.firstElementChild;
    if (card) grid.appendChild(document.adoptNode(card));
  }
  wireToggles();
}

async function getConfirmationToken(action, target) {
  const r = await fetch("/api/admin/confirm_intent", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action, target, by: "ui"})
  });
  return (await r.json()).token;
}

function openConfirm(title, body, onConfirm) {
  const dlg = document.getElementById("confirm-modal");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-body").textContent = body;
  const phrase = document.getElementById("confirm-phrase");
  const reason = document.getElementById("confirm-reason");
  phrase.value = "";
  reason.value = "";
  document.getElementById("confirm-go").onclick = async () => {
    if (phrase.value !== "I CONFIRM") {
      alert("Type the exact phrase to confirm.");
      return;
    }
    await onConfirm(reason.value);
    dlg.close();
    loadModels();
  };
  dlg.showModal();
}

function wireToggles() {
  document.querySelectorAll(".model-card").forEach(card => {
    const name = card.dataset.name;
    card.querySelector(".toggle-paper")?.addEventListener("change", async e => {
      const action = e.target.checked ? "enable_paper" : "disable_paper";
      await fetch(`/api/models/${encodeURIComponent(name)}/${action}`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({by: "ui"})
      });
    });
    card.querySelector(".toggle-live")?.addEventListener("change", e => {
      const enabled = e.target.checked;
      e.target.checked = !enabled;
      if (!enabled) {
        fetch(`/api/models/${encodeURIComponent(name)}/disable_live`, {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({by: "ui"})
        }).then(loadModels);
        return;
      }
      openConfirm(
        `Enable live trading for ${name}?`,
        "This will start placing real orders.",
        async (reason) => {
          const token = await getConfirmationToken("enable_live", name);
          const r = await fetch(`/api/models/${encodeURIComponent(name)}/enable_live`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({by: "ui", reason, confirmation_token: token})
          });
          if (!r.ok) {
            const detail = (await r.json()).detail || "request failed";
            alert(detail);
          }
        });
    });
    card.querySelector(".btn-reload")?.addEventListener("click", () => {
      openConfirm(`Reload ${name}?`, "Bumps generation, drains pending queue.",
        async (reason) => {
          const token = await getConfirmationToken("reload", name);
          await fetch(`/api/models/${encodeURIComponent(name)}/reload`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({by: "ui", reason, confirmation_token: token})
          });
        });
    });
    card.querySelector(".btn-rollback")?.addEventListener("click", () => {
      const target = prompt("Target generation?");
      if (!target) return;
      const targetGen = parseInt(target, 10);
      if (Number.isNaN(targetGen)) { alert("Invalid generation"); return; }
      openConfirm(`Roll ${name} back to gen ${targetGen}?`,
        "Restores previous artifact from archive.",
        async (reason) => {
          const token = await getConfirmationToken("rollback", name);
          await fetch(`/api/models/${encodeURIComponent(name)}/rollback`, {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({by: "ui", reason,
                                  confirmation_token: token,
                                  target_generation: targetGen})
          });
        });
    });
  });
}

loadModels();
```

- [ ] **Step 5: Manual smoke**

```bash
cd ofi-lab-v3 && uvicorn dashboard_api.main:app --reload --port 8080 &
sleep 2
curl -s http://localhost:8080/models | grep "Model Registry"
curl -s http://localhost:8080/api/models/list | python -m json.tool | head
kill %1
```

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "v3-C: /models page with paper/live toggles + confirmation modal (safe DOM)"
```

---

## Task C23: Eligibility progress indicators

**Files:**
- Modify: `templates/_model_card.html`, `filters/live_eligibility.py` (export progress).

Show partial-credit bars when a model isn't yet live-eligible (e.g. "needs 100 paper trades, has 73").

- [ ] **Step 1: Failing test**

```python
def test_eligibility_returns_progress(seeded_db, partial_paper_history):
    from filters.live_eligibility import check_live_eligibility
    elig = check_live_eligibility(seeded_db, "h60_xrp")
    assert not elig.eligible
    assert elig.progress["paper_trades"]["have"] >= 0
    assert elig.progress["paper_trades"]["need"] > 0
```

- [ ] **Step 2: Extend `EligibilityResult`**

In `filters/live_eligibility.py`, add `progress: dict[str, dict[str, int]]` field. Populate per check (e.g., `{"paper_trades": {"have": 73, "need": 100}, "ewma_ev_x10000": {"have": 5, "need": 10}}`).

- [ ] **Step 3: Surface in `/api/models/{name}`**

Append `eligibility` field to the model detail endpoint:

```python
from filters.live_eligibility import check_live_eligibility
elig = check_live_eligibility(conn, name)
return {**dict(row),
        "overlap": [...], "calibration_summary": [...], "recent_audit": [...],
        "eligibility": elig._asdict()}
```

- [ ] **Step 4: Render bars (Jinja-escaped, server-side)**

In `_model_card.html` block under toggles:

```html
{% if not model.is_baseline and model.eligibility %}
  {% for key, p in model.eligibility.progress.items() %}
    <div class="elig-row">
      <span>{{ key }}</span>
      <progress value="{{ p.have }}" max="{{ p.need }}"></progress>
      <span>{{ p.have }}/{{ p.need }}</span>
    </div>
  {% endfor %}
{% endif %}
```

Note: `_partial/model_card` must include `eligibility` in its template context. Update the route:

```python
@router.get("/_partial/model_card", response_class=HTMLResponse)
def model_card_partial(request: Request, name: str, conn=Depends(get_conn)):
    row = conn.execute("SELECT * FROM model_registry WHERE name=?", (name,)).fetchone()
    if not row:
        raise HTTPException(404)
    from filters.live_eligibility import check_live_eligibility
    model = dict(row)
    model["eligibility"] = check_live_eligibility(conn, name)._asdict()
    return templates.TemplateResponse("_model_card.html",
                                      {"request": request, "model": model})
```

- [ ] **Step 5: Commit**

```bash
pytest tests/ -q 2>&1 | tail -5
git add -A && git commit -m "v3-C: eligibility progress bars in model card"
```

---

## Task C24: `/audit` page (safe DOM)

**Files:**
- Create: `templates/audit.html`, `static/js/audit.js`.
- Modify: `routers/pages.py`.

- [ ] **Step 1: Add route**

```python
@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    return templates.TemplateResponse("audit.html", {"request": request})
```

- [ ] **Step 2: Template**

```html
{% extends "base.html" %}
{% block content %}
<h1>Audit Trail</h1>
<form id="audit-filter">
  <label>Model <input name="model"></label>
  <label>Action <select name="action">
    <option value="">all</option>
    {% for a in ["enable_paper", "disable_paper", "enable_live", "disable_live",
                 "reload", "rollback", "lifecycle_transition"] %}
      <option>{{ a }}</option>
    {% endfor %}
  </select></label>
  <button>Filter</button>
</form>
<table id="audit-table">
  <thead><tr><th>Time</th><th>Model</th><th>Action</th>
              <th>Actor</th><th>Reason</th><th>Before→After</th></tr></thead>
  <tbody></tbody>
</table>
<script src="/static/js/audit.js" defer></script>
{% endblock %}
```

- [ ] **Step 3: JS — safe DOM only (no innerHTML)**

`static/js/audit.js`:

```javascript
function appendCell(row, text) {
  const td = document.createElement("td");
  td.textContent = text == null ? "" : String(text);
  row.appendChild(td);
}

async function load() {
  const params = new URLSearchParams(
    new FormData(document.getElementById("audit-filter")));
  const r = await fetch("/api/audit?" + params);
  const {entries} = await r.json();
  const tbody = document.querySelector("#audit-table tbody");
  while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
  for (const e of entries) {
    const row = document.createElement("tr");
    appendCell(row, new Date(e.ts_ms).toISOString());
    appendCell(row, e.model_name);
    appendCell(row, e.action);
    appendCell(row, e.actor);
    appendCell(row, e.reason || "");
    appendCell(row, `${e.before_state} → ${e.after_state}`);
    tbody.appendChild(row);
  }
}

document.getElementById("audit-filter").addEventListener("submit", e => {
  e.preventDefault();
  load();
});
load();
```

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "v3-C: /audit page with model + action filters (safe DOM)"
```

---

## Task C25: Kill switch UI banner (safe DOM)

**Files:**
- Modify: `templates/base.html`.
- Create: `static/js/kill_switch_banner.js`.

When kill switch is engaged, show a red banner across all pages. Add a "Resume" button that triggers the post-restart confirm flow.

- [ ] **Step 1: Add banner to `base.html`**

```html
<div id="kill-banner" hidden style="background:#a00;color:#fff;padding:8px">
  ⛔ Kill switch engaged: <span id="kill-reason"></span>
  by <span id="kill-by"></span>.
  <button id="kill-resume">Resume…</button>
</div>
<script src="/static/js/kill_switch_banner.js" defer></script>
```

- [ ] **Step 2: JS (textContent only)**

```javascript
async function pollKill() {
  const r = await fetch("/api/kill_switch");
  const data = await r.json();
  const banner = document.getElementById("kill-banner");
  banner.hidden = !data.engaged;
  if (data.engaged && data.state) {
    document.getElementById("kill-reason").textContent = data.state.reason || "";
    document.getElementById("kill-by").textContent = data.state.by || "";
  }
}
setInterval(pollKill, 5000);
pollKill();

document.getElementById("kill-resume")?.addEventListener("click", async () => {
  if (!confirm("Resume trading? You will be asked to type a confirmation phrase.")) return;
  const phrase = prompt("Type I CONFIRM to proceed:");
  if (phrase !== "I CONFIRM") return;
  const tokR = await fetch("/api/admin/confirm_intent", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action: "kill_switch_resume", target: "global", by: "ui"})
  });
  const {token} = await tokR.json();
  const r = await fetch("/api/kill_switch/confirm_resume", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({by: "ui", confirmation_token: token})
  });
  if (r.ok) location.reload();
  else {
    const body = await r.json();
    alert(body.detail || "resume failed");
  }
});
```

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "v3-C: kill switch banner with confirm-to-resume (safe DOM)"
```

---

## Task C26: Agent-native parity check

**Files:**
- Create: `ofi-lab-v3/tests/test_agent_native_parity.py`.

Verifies every UI toggle/button corresponds to an API endpoint a machine agent can hit.

- [ ] **Step 1: Test**

```python
from fastapi.testclient import TestClient
from dashboard_api.main import app

ENDPOINTS = [
    ("GET",  "/api/models/list"),
    ("GET",  "/api/models/h300_btc"),
    ("POST", "/api/models/h60_xrp/enable_paper"),
    ("POST", "/api/models/h60_xrp/disable_paper"),
    ("POST", "/api/models/h60_xrp/enable_live"),
    ("POST", "/api/models/h60_xrp/disable_live"),
    ("POST", "/api/models/h60_xrp/reload"),
    ("POST", "/api/models/h60_xrp/rollback"),
    ("GET",  "/api/overlap"),
    ("GET",  "/api/regime/current?symbol=BTCUSDT"),
    ("GET",  "/api/regime/thresholds"),
    ("GET",  "/api/calibration/h300_btc"),
    ("GET",  "/api/audit"),
    ("GET",  "/api/kill_switch"),
    ("POST", "/api/kill_switch"),
    ("POST", "/api/kill_switch/confirm_resume"),
    ("POST", "/api/admin/confirm_intent"),
]

def test_all_endpoints_registered(seeded_db):
    c = TestClient(app)
    for method, path in ENDPOINTS:
        r = c.request(method, path, json={})
        assert r.status_code != 404, f"{method} {path} not registered"
```

- [ ] **Step 2: Run, fix any 404s**

```bash
pytest tests/test_agent_native_parity.py -v
```

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "v3-C: agent-native parity invariant test"
```

---

## Task C27: Final verification + tag

**Files:** none.

- [ ] **Step 1: Full suite**

```bash
cd ofi-lab-v3 && pytest tests/ -q 2>&1 | tail -10
```
Expected: 0 failures, 0 errors.

- [ ] **Step 2: Smoke run dashboard**

```bash
uvicorn dashboard_api.main:app --port 8080 &
sleep 2
for p in /models /audit /api/models/list /api/kill_switch /api/audit; do
  echo "=== $p ==="
  curl -s -o /dev/null -w "%{http_code}\n" "http://localhost:8080$p"
done
kill %1
```
Expected: all 200 (or 422 for endpoints needing a body — anything but 404/500).

- [ ] **Step 3: Tag**

```bash
git tag v3-plan-c-complete
git log --oneline v3-plan-a-foundation-complete..HEAD
```

- [ ] **Step 4: Update `MEMORY.md`**

Append:
```
- Plan C complete 2026-05-10 — dashboard + safety + cleanup. Tag v3-plan-c-complete.
```

---

# Self-Review Notes

**Spec coverage check (against compaction summary):**
- `/models` page with paper/live toggles → C12, C13, C14, C22 ✓
- Disabled-with-reason states + eligibility progress → C23 ✓
- Confirmation flows for live → C11, C14, C22 ✓
- Audit trail UI → C21, C24 ✓
- Guardrails → enforced in `check_live_eligibility` (Plan B), surfaced via C14 412 response and C23 progress bars ✓
- Baseline UI protection → C12 lists `is_baseline`, C20 server-side block, C22 disables buttons ✓
- Rollback (Steering 10k) → C20 ✓
- Kill switch post-restart confirm (Steering 10l) → C19, C25 ✓
- All `/api/models/*` endpoints → C12, C13, C14, C15, C20 ✓
- `/api/overlap`, `/api/regime/*`, `/api/calibration/*` → C16, C17, C18 ✓

**Cleanup section spec:**
- T33 wiring → C6 ✓
- T34 independence test → C7 ✓
- T37 dynamic range integration → C8 ✓
- PSI naming → C9 ✓
- Unwired helpers (T18 regime_features, T26, T29, T36) → C1, C3, C4, C5 ✓
- Test path errors → C2 ✓

**Placeholder scan:** no "TBD" / "implement later" / vague handwaving found.

**Type consistency:** `confirmation_token`, `LiveToggleRequest`, `_audit`, `is_baseline`, `lifecycle_state`, `paper_active`, `live_eligible` used consistently across C11–C20. JS uses `encodeURIComponent` for all interpolated path segments and `textContent` for all data-bound text.

**Security note:** No `innerHTML` with API JSON anywhere in the JS. Server-rendered partial HTML is parsed via `DOMParser` and adopted via `document.adoptNode` — Jinja2 autoescape covers data interpolation in those partials.
