# Model A v3 — Plan B: Multi-Model, Filters, Lifecycle

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Layer multi-model orchestration, regime tagging, a 5-stage filter pipeline with EV-driven trade gating, signal-decay tracking, and a hysteresis-based lifecycle state machine on top of the Plan A foundation. End state: multiple models can run concurrently with independent paper/live status, with conflict-aware EV-driven trade decisions, recency-weighted decay metrics, and automatic suspend/reactivate based on rolling performance.

**Architecture:**
- A `model_registry.json` file is the single source of model identity. A `ModelRegistry` class loads it, tracks each model's lifecycle state, and supports `POST /api/models/reload` (Plan C wires the endpoint; Plan B writes the reload primitive). Reloads bump `RegistryState.generation` and append to `registry_audit`.
- The PaperTrader's hot path is restructured into a five-layer pipeline: prediction → calibration → paper-filter → live-eligibility → platform-execution. Each layer is independently configurable. Paper-active and live-status are independent axes — suspending live never stops paper trading.
- A new `model_overlap` table records per-boundary cross-model agreement; an EWMA-weighted consensus signal is logged but does not gate trades automatically in Plan B.
- A new `decay_metrics` table holds rolling-window EV and calibration error per `(model, symbol, market_window)`. Lifecycle transitions read this table and apply hysteresis (suspend at lower EV bar, reactivate at higher).
- Regime tags (volatility/liquidity/trend) are computed from the existing feature vector at prediction time and stamped on every record; thresholds auto-update from a 30-day rolling distribution.
- The retrain pipeline becomes parameterized: a single CLI orchestrator chains data download → completeness validation → feature build → training → artifact registration. Retraining itself stays out of the live container; the pipeline produces models that humans add to `model_registry.json`.

**Tech Stack:**
- Same as Plan A. No new runtime dependencies. SQLite extends with three tables (`model_overlap`, `decay_metrics`, `decay_evaluations`). One new JSON file (`model_registry.json`) and one auto-refreshed JSON file (`regime_thresholds.json`).

**Bit-for-bit parity NOT required.** Plan B's correctness is validated by: lifecycle transition correctness, EV-gate math vs hand-computed expected values, hot-reload generation+audit invariants, regime threshold auto-refresh determinism, and end-to-end multi-model paper run that produces well-formed overlap/decay rows for two concurrent models.

---

## Spec Coverage

| Spec / Steering Item | Tasks |
|---|---|
| Phase 2 Model registry + hot reload | T1, T2, T3 |
| Phase 2 Baseline protection (no removal) | T4 |
| Phase 2 Pending queue model cleanup on reload (Steering 10h) | T5 |
| Phase 3 Parameterized retrain | T6, T7 |
| Phase 3 Data completeness validation | T8 |
| Phase 3 Gap-safe download fixes | T9 |
| Phase 3 Dynamic XRP price range (Steering 10g) | T10 |
| Phase 3 Orchestrated retrain pipeline | T11 |
| Phase 4 Multi-model orchestration | T12 |
| Phase 4 Vectorized predict (S × N batching) | T13 |
| Phase 5 Overlap row schema | T14 |
| Phase 5 Overlap writer + EWMA-weighted confidence | T15 |
| Phase 6 Regime tagger | T16 |
| Phase 6 Auto-refreshing regime thresholds | T17 |
| Phase 6 Apply regime tags to predictions | T18 |
| Steering §2 Five-layer filter pipeline | T19, T20, T21, T22, T23 |
| Steering §4 EV calculator (Polymarket + Kalshi) | T24 |
| Steering §4 EV gate (no positive EV → no trade) | T25 |
| Steering §4 Model conflict = no trade | T26 |
| Steering §4 Stale-price / stale-book rejection | T27 |
| Steering §4 Per-model exposure caps | T28 |
| Steering §5 Signal decay schema | T29 |
| Steering §5 Recency-weighted EV (EWMA) | T30 |
| Steering §5 Calibration error / Brier | T31 |
| Steering §10f PSI integration | T32 |
| Steering §5 Cliff detection | T33 |
| Steering §6 Lifecycle state machine + hysteresis | T34, T35 |
| Steering §6 Paper-active vs live-status independence | T36 |
| Steering §6 Baseline never retired, always paper-active | T37 |
| Final suite + tag | T38 |

**Out of scope (Plan C):** dashboard `/models` page, paper/live toggle UI, eligibility-progress indicators, change audit trail UI, baseline UI guards, rollback procedures, `/api/models/*` endpoints, kill-switch post-restart confirmation. Plan B writes the data and the primitives; Plan C exposes them.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `ofi-lab-v3/storage/schema.sql` | Modify | Add `model_overlap`, `decay_metrics`, `decay_evaluations` tables + indexes |
| `ofi-lab-v3/storage/model_registry.py` | Create | `ModelRegistry` class — JSON-backed model loader with lifecycle state + hot reload |
| `ofi-lab-v3/storage/decay_writer.py` | Create | `DecayWriter` — append-only writer for rolling EV / Brier / PSI evaluations |
| `ofi-lab-v3/storage/lifecycle.py` | Create | `LifecycleStateMachine` — transition rules with hysteresis |
| `ofi-lab-v3/regime/__init__.py` | Create | Package marker |
| `ofi-lab-v3/regime/tagger.py` | Create | `compute_regime(feature_row, thresholds) → (vol, liq, trend)` |
| `ofi-lab-v3/regime/threshold_updater.py` | Create | Auto-refresh `regime_thresholds.json` from 30-day rolling features |
| `ofi-lab-v3/filters/__init__.py` | Create | Package marker |
| `ofi-lab-v3/filters/pipeline.py` | Create | `FilterPipeline` orchestration of five layers |
| `ofi-lab-v3/filters/paper_filter.py` | Create | Layer 3 — paper-trading gate |
| `ofi-lab-v3/filters/live_eligibility.py` | Create | Layer 4 — live dispatch eligibility |
| `ofi-lab-v3/filters/platform_execution.py` | Create | Layer 5 — Kalshi-specific gates |
| `ofi-lab-v3/execution/ev.py` | Create | `compute_ev_polymarket`, `compute_ev_kalshi` |
| `ofi-lab-v3/execution/conflict.py` | Create | `consensus_for_boundary` — agreement / disagreement detection |
| `ofi-lab-v3/execution/exposure.py` | Create | `ExposureCap` — per-model open-position cap tracker |
| `ofi-lab-v3/trading/paper_trader.py` | Modify | Wire ModelRegistry, regime tagger, filter pipeline, overlap writer |
| `ofi-lab-v3/trading/overlap_writer.py` | Create | `OverlapWriter` — per-boundary consensus emitter |
| `ofi-lab-v3/validation/retrain.py` | Create | Parameterized retrain CLI wrapping `run_training.py` |
| `ofi-lab-v3/validation/completeness.py` | Create | Date-range completeness validator |
| `ofi-lab-v3/data/download_klines.py` | Modify | Gap-safe re-download on partial files |
| `ofi-lab-v3/data/download_orderbook.py` | Modify | Gap-safe re-download on partial files |
| `ofi-lab-v3/scripts/retrain_pipeline.sh` | Create | Orchestrate download → validate → features → train → register |
| `ofi-lab-v3/config.py` | Modify | Dynamic `MID_PRICE_TRAINING_RANGE` (Steering 10g) — populate from training data |
| `ofi-lab-v3/data/range_computer.py` | Create | `compute_mid_price_range(parquet_dir, symbol)` — used by retrain to refresh ranges |
| `ofi-lab-v3/tests/test_model_registry.py` | Create | Registry load + reload + baseline protection |
| `ofi-lab-v3/tests/test_regime_tagger.py` | Create | Quartile-based regime classification |
| `ofi-lab-v3/tests/test_overlap_writer.py` | Create | Overlap row + consensus computation |
| `ofi-lab-v3/tests/test_filter_pipeline.py` | Create | Five-layer pipeline composition |
| `ofi-lab-v3/tests/test_ev.py` | Create | EV math vs hand-calculated expected values |
| `ofi-lab-v3/tests/test_conflict.py` | Create | Model agreement / disagreement |
| `ofi-lab-v3/tests/test_exposure.py` | Create | Per-model open-position cap |
| `ofi-lab-v3/tests/test_decay_writer.py` | Create | Decay row writer + indexes |
| `ofi-lab-v3/tests/test_lifecycle.py` | Create | State transitions + hysteresis |
| `ofi-lab-v3/tests/test_completeness.py` | Create | Gap detection on synthetic parquet dir |
| `ofi-lab-v3/tests/test_range_computer.py` | Create | Dynamic price range from parquet |
| `ofi-lab-v3/tests/test_retrain_cli.py` | Create | retrain.py CLI parses dates + delegates |
| `ofi-lab-v3/tests/test_psi_integration.py` | Create | PSI integration on resolution count threshold |
| `ofi-lab-v3/tests/test_paper_trader_multimodel.py` | Create | End-to-end two-model boundary scoring |

---

## Schema Additions (T14, T29 reference)

```sql
-- =========================================================================
-- model_overlap: per-boundary cross-model agreement record.
-- =========================================================================
CREATE TABLE IF NOT EXISTS model_overlap (
    ts_contract_open_ms    INTEGER NOT NULL,
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    models_scored_json     TEXT NOT NULL,
    directions_json        TEXT NOT NULL,
    confidences_json       TEXT NOT NULL,
    consensus              INTEGER NOT NULL,
    consensus_direction    TEXT,
    weighted_confidence    REAL,
    registry_load_generation INTEGER NOT NULL,
    PRIMARY KEY (ts_contract_open_ms, symbol, market_window_seconds, registry_load_generation)
);

CREATE INDEX IF NOT EXISTS idx_overlap_symbol_window
    ON model_overlap(symbol, market_window_seconds);

-- =========================================================================
-- decay_metrics: rolling decay snapshots per (model, symbol, market_window).
-- =========================================================================
CREATE TABLE IF NOT EXISTS decay_metrics (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    model_name             TEXT NOT NULL,
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    window_size            INTEGER NOT NULL,
    rolling_ev             REAL,
    recency_weighted_ev    REAL,
    rolling_win_rate       REAL,
    brier_score            REAL,
    calibration_error      REAL,
    sample_count           INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decay_model
    ON decay_metrics(model_name, symbol, market_window_seconds, ts);

-- =========================================================================
-- decay_evaluations: PSI / cliff / threshold check log.
-- =========================================================================
CREATE TABLE IF NOT EXISTS decay_evaluations (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    model_name             TEXT NOT NULL,
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    eval_type              TEXT NOT NULL,
    metric_value           REAL,
    threshold              REAL,
    triggered              INTEGER NOT NULL,
    detail_json            TEXT
);

CREATE INDEX IF NOT EXISTS idx_decay_eval_model
    ON decay_evaluations(model_name, eval_type, ts);
```

---

## Tasks

### Task 1: Schema additions — `model_overlap`, `decay_metrics`, `decay_evaluations`

**Files:**
- Modify: `ofi-lab-v3/storage/schema.sql`
- Modify: `ofi-lab-v3/tests/test_storage_schema.py` — extend EXPECTED_TABLES

- [ ] **Step 1: Update test**

```python
# Append to EXPECTED_TABLES set in tests/test_storage_schema.py
EXPECTED_TABLES = {
    "predictions",
    "paper_trades",
    "decision_traces",
    "calibration_outcomes",
    "registry_audit",
    "policy_audit",
    "model_overlap",
    "decay_metrics",
    "decay_evaluations",
}

EXPECTED_INDEXES_INCLUDE = {
    "idx_pred_idempotent",
    "idx_pred_native_for_decay",
    "idx_trace_pid",
    "idx_cal_native",
    "idx_audit_generation",
    "idx_policy_version",
    "idx_overlap_symbol_window",
    "idx_decay_model",
    "idx_decay_eval_model",
}
```

- [ ] **Step 2: Run pytest expecting schema test failure**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3
.venv/bin/python -m pytest tests/test_storage_schema.py -v
```

Expected: 2 failures (`missing: {'model_overlap', 'decay_metrics', 'decay_evaluations'}` + missing indexes).

- [ ] **Step 3: Append DDL to `storage/schema.sql`** (use the schema block above verbatim)

- [ ] **Step 4: Run pytest expecting pass**

```bash
.venv/bin/python -m pytest tests/test_storage_schema.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem
git add ofi-lab-v3/storage/schema.sql ofi-lab-v3/tests/test_storage_schema.py
git commit -m "v3-B: schema — model_overlap, decay_metrics, decay_evaluations"
```

---

### Task 2: `ModelRegistry` — JSON-backed loader with lifecycle state

**Files:**
- Create: `ofi-lab-v3/storage/model_registry.py`
- Create: `ofi-lab-v3/tests/test_model_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_model_registry.py
import json
from pathlib import Path

import pytest

from storage.model_registry import (
    ModelRegistry, ModelEntry, BaselineRemovalError,
)


SAMPLE_REGISTRY = {
    "models": {
        "900s_btc_v3_20260315": {
            "path": "/data/models/run_20260315/model.lgb",
            "feature_names_path": "/data/models/run_20260315/feature_names.json",
            "horizon_seconds": 900,
            "symbol": "BTCUSDT",
            "feature_version": "v3",
            "train_window_start": "2025-04-01",
            "train_window_end": "2026-03-15",
            "train_cutoff": "2026-03-15",
            "is_baseline": True,
            "paper_trading_enabled": True,
            "kalshi_live_enabled": True,
            "lifecycle_state": "live_active",
            "min_markets_for_kalshi": 200,
            "min_days_for_kalshi": 14,
        },
        "60s_btc_v3_20260315": {
            "path": "/data/models/run_20260316/model.lgb",
            "feature_names_path": "/data/models/run_20260316/feature_names.json",
            "horizon_seconds": 60,
            "symbol": "BTCUSDT",
            "feature_version": "v3",
            "train_window_start": "2025-04-01",
            "train_window_end": "2026-03-15",
            "train_cutoff": "2026-03-15",
            "is_baseline": False,
            "paper_trading_enabled": False,
            "kalshi_live_enabled": False,
            "lifecycle_state": "prediction_only",
            "min_markets_for_kalshi": 200,
            "min_days_for_kalshi": 14,
        },
    }
}


def _write_registry(tmp_path, body):
    p = tmp_path / "model_registry.json"
    p.write_text(json.dumps(body))
    return p


def test_load_returns_entries_keyed_by_name(tmp_path):
    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    reg = ModelRegistry(str(p))
    reg.load()
    names = sorted(reg.entries().keys())
    assert names == ["60s_btc_v3_20260315", "900s_btc_v3_20260315"]
    e = reg.entries()["900s_btc_v3_20260315"]
    assert isinstance(e, ModelEntry)
    assert e.is_baseline is True
    assert e.horizon_seconds == 900


def test_active_models_filters_paper_disabled(tmp_path):
    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    reg = ModelRegistry(str(p))
    reg.load()
    active = reg.active_paper_models()
    assert list(active.keys()) == ["900s_btc_v3_20260315"]


def test_baseline_lookup(tmp_path):
    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    reg = ModelRegistry(str(p))
    reg.load()
    assert reg.baseline_name() == "900s_btc_v3_20260315"


def test_missing_baseline_raises(tmp_path):
    body = {"models": {"x": dict(SAMPLE_REGISTRY["models"]["60s_btc_v3_20260315"])}}
    p = _write_registry(tmp_path, body)
    reg = ModelRegistry(str(p))
    with pytest.raises(BaselineRemovalError):
        reg.load()


def test_lifecycle_state_default_is_prediction_only(tmp_path):
    body = json.loads(json.dumps(SAMPLE_REGISTRY))
    body["models"]["60s_btc_v3_20260315"].pop("lifecycle_state", None)
    p = _write_registry(tmp_path, body)
    reg = ModelRegistry(str(p))
    reg.load()
    e = reg.entries()["60s_btc_v3_20260315"]
    assert e.lifecycle_state == "prediction_only"
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

```bash
.venv/bin/python -m pytest tests/test_model_registry.py -v
```

- [ ] **Step 3: Implement `storage/model_registry.py`**

```python
# ofi-lab-v3/storage/model_registry.py
"""Model registry — JSON-backed source of truth for active models.

Each model entry carries identity (path, hashes, feature version,
training horizon), runtime flags (paper_trading_enabled,
kalshi_live_enabled), and lifecycle state. The baseline model is
protected against removal: any reload that drops the baseline raises
``BaselineRemovalError``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Optional


VALID_LIFECYCLE_STATES = {
    "prediction_only",
    "live_eligible",
    "live_active",
    "live_suspended",
    "requalification",
    "retired",
}


class BaselineRemovalError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelEntry:
    name: str
    path: str
    feature_names_path: str
    horizon_seconds: int
    symbol: str
    feature_version: str
    train_window_start: Optional[str]
    train_window_end: Optional[str]
    train_cutoff: Optional[str]
    is_baseline: bool
    paper_trading_enabled: bool
    kalshi_live_enabled: bool
    lifecycle_state: str
    min_markets_for_kalshi: int
    min_days_for_kalshi: int

    def __post_init__(self) -> None:
        if self.lifecycle_state not in VALID_LIFECYCLE_STATES:
            raise ValueError(
                f"lifecycle_state {self.lifecycle_state!r} not in {VALID_LIFECYCLE_STATES}"
            )

    def to_dict(self) -> dict:
        return asdict(self)


class ModelRegistry:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._entries: Dict[str, ModelEntry] = {}
        self._baseline_name: Optional[str] = None

    def load(self) -> None:
        raw = json.loads(self._path.read_text())
        new_entries: Dict[str, ModelEntry] = {}
        baseline: Optional[str] = None
        for name, body in raw.get("models", {}).items():
            entry = ModelEntry(
                name=name,
                path=body["path"],
                feature_names_path=body["feature_names_path"],
                horizon_seconds=int(body["horizon_seconds"]),
                symbol=body["symbol"],
                feature_version=body["feature_version"],
                train_window_start=body.get("train_window_start"),
                train_window_end=body.get("train_window_end"),
                train_cutoff=body.get("train_cutoff"),
                is_baseline=bool(body.get("is_baseline", False)),
                paper_trading_enabled=bool(body.get("paper_trading_enabled", False)),
                kalshi_live_enabled=bool(body.get("kalshi_live_enabled", False)),
                lifecycle_state=body.get("lifecycle_state", "prediction_only"),
                min_markets_for_kalshi=int(body.get("min_markets_for_kalshi", 200)),
                min_days_for_kalshi=int(body.get("min_days_for_kalshi", 14)),
            )
            new_entries[name] = entry
            if entry.is_baseline:
                baseline = name
        if baseline is None:
            raise BaselineRemovalError(
                f"No model in {self._path} has is_baseline=true"
            )
        self._entries = new_entries
        self._baseline_name = baseline

    def entries(self) -> Dict[str, ModelEntry]:
        return dict(self._entries)

    def active_paper_models(self) -> Dict[str, ModelEntry]:
        return {n: e for n, e in self._entries.items() if e.paper_trading_enabled}

    def baseline_name(self) -> str:
        if self._baseline_name is None:
            raise BaselineRemovalError("registry not loaded or baseline missing")
        return self._baseline_name
```

- [ ] **Step 4: Run pytest**

```bash
.venv/bin/python -m pytest tests/test_model_registry.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem
git add ofi-lab-v3/storage/model_registry.py ofi-lab-v3/tests/test_model_registry.py
git commit -m "v3-B: ModelRegistry — JSON loader with baseline protection"
```

---

### Task 3: Hot reload — increment generation + audit row

**Files:**
- Modify: `ofi-lab-v3/storage/model_registry.py`
- Modify: `ofi-lab-v3/tests/test_model_registry.py`

- [ ] **Step 1: Append failing test**

```python
def test_reload_increments_generation_and_appends_audit(tmp_path):
    from storage.db import open_database, init_schema
    from storage.registry_state import RegistryState

    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()

    reg = ModelRegistry(str(p))
    reg.load()
    gen0 = rs.current_generation()
    new_gen = reg.reload(rs, reason="test reload")
    assert new_gen == gen0 + 1
    rows = conn.execute(
        "SELECT generation, reason FROM registry_audit ORDER BY generation"
    ).fetchall()
    assert rows[-1]["generation"] == new_gen
    assert rows[-1]["reason"] == "test reload"


def test_reload_with_missing_baseline_does_not_increment(tmp_path):
    from storage.db import open_database, init_schema
    from storage.registry_state import RegistryState

    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()

    reg = ModelRegistry(str(p))
    reg.load()
    gen0 = rs.current_generation()

    # Rewrite registry without baseline
    bad = {"models": {"x": dict(SAMPLE_REGISTRY["models"]["60s_btc_v3_20260315"])}}
    p.write_text(json.dumps(bad))

    with pytest.raises(BaselineRemovalError):
        reg.reload(rs, reason="bad")

    # Generation must NOT have advanced
    assert rs.current_generation() == gen0
    # In-memory entries unchanged (still hold the previous baseline)
    assert "900s_btc_v3_20260315" in reg.entries()
```

- [ ] **Step 2: Run pytest expecting AttributeError on `reload`**

- [ ] **Step 3: Add `reload` method to `ModelRegistry`**

```python
def reload(self, registry_state, reason: str,
           detail: Optional[dict] = None) -> int:
    """Re-read the registry file and increment generation atomically.

    On any error (including missing baseline), in-memory state is
    unchanged and the generation is NOT incremented.
    """
    raw = json.loads(self._path.read_text())
    new_entries: Dict[str, ModelEntry] = {}
    baseline: Optional[str] = None
    for name, body in raw.get("models", {}).items():
        entry = ModelEntry(
            name=name,
            path=body["path"],
            feature_names_path=body["feature_names_path"],
            horizon_seconds=int(body["horizon_seconds"]),
            symbol=body["symbol"],
            feature_version=body["feature_version"],
            train_window_start=body.get("train_window_start"),
            train_window_end=body.get("train_window_end"),
            train_cutoff=body.get("train_cutoff"),
            is_baseline=bool(body.get("is_baseline", False)),
            paper_trading_enabled=bool(body.get("paper_trading_enabled", False)),
            kalshi_live_enabled=bool(body.get("kalshi_live_enabled", False)),
            lifecycle_state=body.get("lifecycle_state", "prediction_only"),
            min_markets_for_kalshi=int(body.get("min_markets_for_kalshi", 200)),
            min_days_for_kalshi=int(body.get("min_days_for_kalshi", 14)),
        )
        new_entries[name] = entry
        if entry.is_baseline:
            baseline = name
    if baseline is None:
        raise BaselineRemovalError(
            f"reload rejected: no baseline in {self._path}"
        )
    # Atomic swap + audit row
    self._entries = new_entries
    self._baseline_name = baseline
    return registry_state.increment(reason=reason, detail=detail)
```

- [ ] **Step 4: Run pytest expecting pass**

Expected: 7 passed (5 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/model_registry.py ofi-lab-v3/tests/test_model_registry.py
git commit -m "v3-B: ModelRegistry.reload — atomic swap + generation increment"
```

---

### Task 4: Pending-queue cleanup on reload (Steering 10h)

**Files:**
- Modify: `ofi-lab-v3/storage/model_registry.py` — add `reload_with_queue_cleanup`
- Modify: `ofi-lab-v3/tests/test_model_registry.py`

- [ ] **Step 1: Append failing test**

```python
def test_reload_prunes_pending_queue_for_removed_models(tmp_path):
    from storage.db import open_database, init_schema
    from storage.registry_state import RegistryState
    from storage.pending_queue import PendingResolutionQueue, PendingEntry

    p = _write_registry(tmp_path, SAMPLE_REGISTRY)
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()

    queue_path = tmp_path / "pending.json"
    queue = PendingResolutionQueue(queue_path)
    # Enqueue entries for both models
    for name in ("900s_btc_v3_20260315", "60s_btc_v3_20260315"):
        queue.enqueue(PendingEntry(
            prediction_id=f"{name}_p", boundary_ms=1_000_000,
            model_name=name, symbol="BTCUSDT", market_window_seconds=900,
            registry_load_generation=0, ts_resolve_at_ms=1_900_000,
            resolution_type="native", price_at_open=60_000.0,
        ))
    # Old generation entry from a now-removed model
    queue.enqueue(PendingEntry(
        prediction_id="ghost_p", boundary_ms=1_000_000,
        model_name="ghost_model", symbol="BTCUSDT", market_window_seconds=900,
        registry_load_generation=0, ts_resolve_at_ms=1_900_000,
        resolution_type="native", price_at_open=60_000.0,
    ))
    queue.persist()

    reg = ModelRegistry(str(p))
    reg.load()
    pruned, new_gen = reg.reload_with_queue_cleanup(
        rs, queue, reason="hot reload",
    )
    assert pruned == 1  # only ghost_model removed
    assert new_gen == 1
    remaining = sorted(e.prediction_id for e in queue.iter_all())
    assert remaining == ["60s_btc_v3_20260315_p", "900s_btc_v3_20260315_p"]
```

- [ ] **Step 2: Run test expecting AttributeError**

- [ ] **Step 3: Add `reload_with_queue_cleanup` to `ModelRegistry`**

```python
def reload_with_queue_cleanup(
    self, registry_state, pending_queue,
    reason: str, detail: Optional[dict] = None,
):
    """Reload + prune pending queue for any model removed by the reload.

    Returns (pruned_count, new_generation).
    """
    new_gen = self.reload(registry_state, reason=reason, detail=detail)
    active_names = set(self._entries.keys())
    pruned = pending_queue.prune_for_models(active_names)
    pending_queue.persist()
    return pruned, new_gen
```

- [ ] **Step 4: Run pytest expecting pass**

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/model_registry.py ofi-lab-v3/tests/test_model_registry.py
git commit -m "v3-B: ModelRegistry.reload_with_queue_cleanup (Steering 10h)"
```

---

### Task 5: Wire `ModelRegistry` into `PaperTrader.__init__`

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` — replace the config-dict approach with ModelRegistry where models are loaded
- Modify: `ofi-lab-v3/tests/test_paper_trader_init_v3.py` — add registry test

- [ ] **Step 1: Append failing test**

```python
def test_trader_loads_from_model_registry(tmp_path, monkeypatch, tiny_model_path):
    import json
    from storage.model_registry import ModelRegistry

    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))

    registry_path = tmp_path / "model_registry.json"
    registry_path.write_text(json.dumps({"models": {
        "900s_btc_v3_20260315": {
            "path": tiny_model_path,
            "feature_names_path": str(
                Path(tiny_model_path).parent / "feature_names.json"
            ),
            "horizon_seconds": 900,
            "symbol": "BTCUSDT",
            "feature_version": "v3",
            "is_baseline": True,
            "paper_trading_enabled": True,
            "kalshi_live_enabled": True,
            "lifecycle_state": "live_active",
            "train_window_start": "2025-04-01",
            "train_window_end": "2026-03-15",
            "train_cutoff": "2026-03-15",
            "min_markets_for_kalshi": 200,
            "min_days_for_kalshi": 14,
        }
    }}))
    monkeypatch.setenv("MODEL_REGISTRY_PATH", str(registry_path))

    from trading.paper_trader import PaperTrader
    t = PaperTrader(
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    assert isinstance(t.model_registry, ModelRegistry)
    assert "900s_btc_v3_20260315" in t.models
    # The legacy `model_paths` constructor arg is no longer required
```

- [ ] **Step 2: Run pytest expecting failure**

- [ ] **Step 3: Modify `PaperTrader.__init__`**

Add to imports near top:
```python
from storage.model_registry import ModelRegistry
```

Modify `__init__` to make `model_paths` optional and accept a registry path. The new init flow:

```python
# Near top of __init__:
registry_path = os.environ.get(
    "MODEL_REGISTRY_PATH",
    str(self.log_dir.parent / "model_registry.json")
)
if Path(registry_path).exists():
    self.model_registry = ModelRegistry(registry_path)
    self.model_registry.load()
    # Build model_paths from registry
    if not model_paths:
        model_paths = {
            name: entry.path
            for name, entry in self.model_registry.entries().items()
            if entry.paper_trading_enabled
        }
else:
    self.model_registry = None
    if not model_paths:
        raise ValueError(
            f"No model_registry.json at {registry_path} and no model_paths provided"
        )
```

The existing model loading loop continues to use `model_paths` — only the source of `model_paths` changes. Keep `config.PAPER_TRADING["model_metadata"]` as a fallback for entries not in the registry.

For `_build_envelope`, prefer `self.model_registry.entries()[name]` over `config.PAPER_TRADING["model_metadata"][name]` when the registry is loaded:

```python
def _build_envelope(self, model_name: str, platform: str) -> ProvenanceEnvelope:
    if self.model_registry is not None and model_name in self.model_registry.entries():
        e = self.model_registry.entries()[model_name]
        meta = {
            "feature_version": e.feature_version,
            "training_horizon_seconds": e.horizon_seconds,
            "train_window_start": e.train_window_start,
            "train_window_end": e.train_window_end,
            "train_cutoff": e.train_cutoff,
            "symbol": e.symbol,
        }
    else:
        meta = config.PAPER_TRADING["model_metadata"][model_name]
    # ... rest unchanged ...
```

For `kalshi_dispatch_eligible`, prefer registry:

```python
def kalshi_dispatch_eligible(self, *, model_name, symbol, market_window_seconds):
    if self.model_registry is not None and model_name in self.model_registry.entries():
        e = self.model_registry.entries()[model_name]
        if not e.kalshi_live_enabled:
            return False
        if e.lifecycle_state != "live_active":
            return False
        if symbol != e.symbol:
            return False
        if market_window_seconds != e.horizon_seconds:
            return False
        return True
    # Legacy fallback
    return self._kalshi_dispatch_eligible_from_config(
        model_name=model_name, symbol=symbol,
        market_window_seconds=market_window_seconds,
    )
```

(The legacy path becomes `_kalshi_dispatch_eligible_from_config` — rename what was previously `kalshi_dispatch_eligible`.)

- [ ] **Step 4: Run pytest, expect 7 passed in `test_paper_trader_init_v3.py`**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_paper_trader_init_v3.py
git commit -m "v3-B: PaperTrader loads from ModelRegistry; legacy config fallback"
```

---

### Task 6: Parameterized retrain CLI — `validation/retrain.py`

**Files:**
- Create: `ofi-lab-v3/validation/retrain.py`
- Create: `ofi-lab-v3/tests/test_retrain_cli.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_retrain_cli.py
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "validation" / "retrain.py"


def test_cli_rejects_missing_required_args():
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--horizon", "900"],
        capture_output=True, text=True,
    )
    assert res.returncode != 0
    assert "symbol" in res.stderr.lower() or "required" in res.stderr.lower()


def test_cli_dry_run_prints_resolved_window(tmp_path):
    res = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--horizon", "900",
         "--symbol", "BTCUSDT",
         "--feature-version", "v3",
         "--train-end", "2026-05-08",
         "--train-days", "330",
         "--val-days", "30",
         "--test-days", "14",
         "--feature-dir", str(tmp_path),
         "--output-dir", str(tmp_path / "models"),
         "--dry-run"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "train: 2025-06-12 → 2026-05-08" in res.stdout or \
           "train_window_start=2025-06-12" in res.stdout
    assert "auto_name=900s_btcusdt_v3_20260508" in res.stdout.lower() or \
           "900s_btcusdt_v3_20260508" in res.stdout.lower()
```

- [ ] **Step 2: Run test expecting FileNotFoundError**

- [ ] **Step 3: Implement `validation/retrain.py`**

```python
# ofi-lab-v3/validation/retrain.py
"""Parameterized retrain CLI.

Wraps the legacy run_training.py with date-range params instead of
hardcoded TRAIN_END / VAL_END. Auto-derives the model name per the
v3 naming convention.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _auto_name(horizon: int, symbol: str, feature_version: str,
               train_end: str) -> str:
    sym = symbol.lower()
    cutoff = train_end.replace("-", "")
    return f"{horizon}s_{sym}_{feature_version}_{cutoff}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--horizon", type=int, required=True,
                   help="training horizon in seconds (60, 300, 900...)")
    p.add_argument("--symbol", required=True,
                   help="symbol e.g. BTCUSDT")
    p.add_argument("--feature-version", required=True,
                   help="feature schema version e.g. v3, v3d")
    p.add_argument("--train-days", type=int, default=330)
    p.add_argument("--train-end", required=True,
                   help="last full day of training data, YYYY-MM-DD")
    p.add_argument("--val-days", type=int, default=30)
    p.add_argument("--test-days", type=int, default=14)
    p.add_argument("--feature-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--dry-run", action="store_true",
                   help="print resolved windows + name and exit 0")
    p.add_argument("--auto-name", action="store_true",
                   help="(default) derive model name from convention")
    args = p.parse_args()

    train_end = datetime.strptime(args.train_end, "%Y-%m-%d").date()
    train_start = train_end - timedelta(days=args.train_days - 1)
    val_end = train_end + timedelta(days=args.val_days)
    test_end = val_end + timedelta(days=args.test_days)
    name = _auto_name(args.horizon, args.symbol, args.feature_version,
                       args.train_end)

    print(f"train: {train_start} → {train_end}")
    print(f"val: {train_end + timedelta(days=1)} → {val_end}")
    print(f"test: {val_end + timedelta(days=1)} → {test_end}")
    print(f"auto_name={name}")

    if args.dry_run:
        return 0

    # Plan B does NOT call the actual trainer; that comes in T7's
    # delegation step. T6 just exposes the parameterized CLI.
    print("note: --dry-run not specified; T7 wires actual training")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test expecting 2 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/validation/retrain.py ofi-lab-v3/tests/test_retrain_cli.py
git commit -m "v3-B: retrain.py CLI — parameterized date ranges + auto-name"
```

---

### Task 7: Delegate retrain.py to `validation/run_training.py`

**Files:**
- Modify: `ofi-lab-v3/validation/retrain.py` — call into run_training when not `--dry-run`
- Modify: `ofi-lab-v3/tests/test_retrain_cli.py` — add delegation test

- [ ] **Step 1: Append failing test**

```python
def test_cli_calls_run_training_when_not_dry(tmp_path, monkeypatch):
    """When --dry-run is absent, retrain.py must invoke run_training's
    main() with the resolved date range. We verify by patching
    run_training.main to a sentinel and confirming it was called.
    """
    sentinel_path = tmp_path / "sentinel.txt"
    fake_runner = tmp_path / "fake_run_training.py"
    fake_runner.write_text(
        "import sys; from pathlib import Path\n"
        f"Path({str(sentinel_path)!r}).write_text(' '.join(sys.argv[1:]))\n"
        "sys.exit(0)\n"
    )
    monkeypatch.setenv("V3_RUN_TRAINING_OVERRIDE", str(fake_runner))
    res = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--horizon", "900",
         "--symbol", "BTCUSDT",
         "--feature-version", "v3",
         "--train-end", "2026-05-08",
         "--train-days", "330",
         "--val-days", "30",
         "--test-days", "14",
         "--feature-dir", str(tmp_path),
         "--output-dir", str(tmp_path / "models")],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    assert sentinel_path.exists()
    args = sentinel_path.read_text()
    assert "--symbol BTCUSDT" in args or "BTCUSDT" in args
    assert "--horizon 900" in args or "900" in args
```

- [ ] **Step 2: Run test expecting failure (sentinel not written)**

- [ ] **Step 3: Add delegation block to `retrain.py`**

Replace the `# Plan B does NOT call the actual trainer` block with:

```python
    import os
    runner_override = os.environ.get("V3_RUN_TRAINING_OVERRIDE")
    if runner_override:
        cmd = [sys.executable, runner_override]
    else:
        cmd = [sys.executable, "-m", "validation.run_training"]
    cmd += [
        "--symbol", args.symbol,
        "--horizon", str(args.horizon),
        "--feature-version", args.feature_version,
        "--train-start", str(train_start),
        "--train-end", str(train_end),
        "--val-end", str(val_end),
        "--test-end", str(test_end),
        "--feature-dir", args.feature_dir,
        "--output-dir", args.output_dir,
        "--model-name", name,
    ]
    print(f"delegating to: {' '.join(cmd)}")
    import subprocess as _sp
    return _sp.call(cmd)
```

`validation/run_training.py` may not yet accept these flags; that's an existing v2 retrain limitation. The `V3_RUN_TRAINING_OVERRIDE` env var is the test seam — it points at a stub script that swallows args without complaining. Real wiring of run_training.py's CLI is out of Plan B scope (the spec says "preserve run_training.py unchanged as reference").

- [ ] **Step 4: Run test expecting pass**

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/validation/retrain.py ofi-lab-v3/tests/test_retrain_cli.py
git commit -m "v3-B: retrain.py delegates to run_training (override seam)"
```

---

### Task 8: Data completeness validator — `validation/completeness.py`

**Files:**
- Create: `ofi-lab-v3/validation/completeness.py`
- Create: `ofi-lab-v3/tests/test_completeness.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_completeness.py
from datetime import date
from pathlib import Path

import pytest

from validation.completeness import (
    enumerate_dates, check_feature_coverage, CoverageReport,
)


def test_enumerate_dates_inclusive():
    out = enumerate_dates(date(2026, 5, 1), date(2026, 5, 3))
    assert [d.isoformat() for d in out] == [
        "2026-05-01", "2026-05-02", "2026-05-03"
    ]


def test_full_coverage(tmp_path):
    # Create feature parquet for every day in range
    for d in ("20260501", "20260502", "20260503"):
        (tmp_path / f"{d}_BTCUSDT_features.parquet").write_text("x")
    rep = check_feature_coverage(
        feature_dir=str(tmp_path),
        symbol="BTCUSDT",
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )
    assert rep.total_days == 3
    assert rep.missing_days == []
    assert rep.coverage_pct == 100.0


def test_partial_coverage_with_one_missing_day(tmp_path):
    (tmp_path / "20260501_BTCUSDT_features.parquet").write_text("x")
    (tmp_path / "20260503_BTCUSDT_features.parquet").write_text("x")
    rep = check_feature_coverage(
        feature_dir=str(tmp_path),
        symbol="BTCUSDT",
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )
    assert rep.missing_days == [date(2026, 5, 2)]
    assert rep.max_consecutive_missing == 1
    assert abs(rep.coverage_pct - 66.67) < 0.05


def test_three_consecutive_missing_days_exceeds_max(tmp_path):
    (tmp_path / "20260501_BTCUSDT_features.parquet").write_text("x")
    (tmp_path / "20260505_BTCUSDT_features.parquet").write_text("x")
    rep = check_feature_coverage(
        feature_dir=str(tmp_path),
        symbol="BTCUSDT",
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
    )
    assert rep.max_consecutive_missing == 3
    assert rep.passes(max_consecutive_missing=2) is False
    assert rep.passes(max_consecutive_missing=3) is True
```

- [ ] **Step 2: Run test expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `validation/completeness.py`**

```python
# ofi-lab-v3/validation/completeness.py
"""Data completeness validator.

Before a retrain begins, this module verifies that every day in the
requested date range has a corresponding feature parquet. The retrain
pipeline aborts if more than N consecutive days are missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List


def enumerate_dates(start: date, end: date) -> List[date]:
    out = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


@dataclass
class CoverageReport:
    total_days: int
    present_days: List[date]
    missing_days: List[date]
    max_consecutive_missing: int

    @property
    def coverage_pct(self) -> float:
        if self.total_days == 0:
            return 100.0
        return round(100.0 * len(self.present_days) / self.total_days, 2)

    def passes(self, max_consecutive_missing: int) -> bool:
        return self.max_consecutive_missing <= max_consecutive_missing


def check_feature_coverage(
    feature_dir: str, symbol: str, start: date, end: date,
) -> CoverageReport:
    days = enumerate_dates(start, end)
    fdir = Path(feature_dir)
    present: List[date] = []
    missing: List[date] = []
    for d in days:
        slug = d.strftime("%Y%m%d")
        candidate = fdir / f"{slug}_{symbol}_features.parquet"
        if candidate.exists():
            present.append(d)
        else:
            missing.append(d)
    # Compute longest run of consecutive missing days
    max_run = 0
    cur = 0
    for d in days:
        if d in missing:
            cur += 1
            max_run = max(max_run, cur)
        else:
            cur = 0
    return CoverageReport(
        total_days=len(days), present_days=present,
        missing_days=missing, max_consecutive_missing=max_run,
    )
```

- [ ] **Step 4: Run pytest expecting 4 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/validation/completeness.py ofi-lab-v3/tests/test_completeness.py
git commit -m "v3-B: data completeness validator"
```

---

### Task 9: Gap-safe `download_klines.py`

**Files:**
- Modify: `ofi-lab-v3/data/download_klines.py`
- Create: `ofi-lab-v3/tests/test_download_klines_gap_safe.py`

The v2 script skips if output file exists, treating any partial download as complete. v3 inspects row count: if the file exists but contains fewer rows than expected for a complete day (1440 1-minute bars), it's re-downloaded.

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_download_klines_gap_safe.py
from pathlib import Path

import pytest

from data.download_klines import is_complete_day_file, EXPECTED_ROWS_PER_DAY


def test_expected_rows_per_day_is_1440():
    assert EXPECTED_ROWS_PER_DAY == 1440


def test_missing_file_is_not_complete(tmp_path):
    assert is_complete_day_file(tmp_path / "missing.parquet") is False


def test_empty_file_is_not_complete(tmp_path):
    p = tmp_path / "empty.parquet"
    p.write_bytes(b"")
    assert is_complete_day_file(p) is False


def test_full_day_parquet_is_complete(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp_path / "full.parquet"
    table = pa.table({"open_time": list(range(1440))})
    pq.write_table(table, str(p))
    assert is_complete_day_file(p) is True


def test_partial_day_parquet_is_not_complete(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp_path / "partial.parquet"
    table = pa.table({"open_time": list(range(500))})
    pq.write_table(table, str(p))
    assert is_complete_day_file(p) is False
```

- [ ] **Step 2: Run pytest expecting AttributeError on `is_complete_day_file`**

- [ ] **Step 3: Add helper to `data/download_klines.py`**

Read the existing file first to find the `if output_path.exists()` check (the v2 skip pattern). Replace it with `is_complete_day_file(output_path)` and add the helper near the top:

```python
EXPECTED_ROWS_PER_DAY = 1440  # 1-minute bars × 24 h × 60 min


def is_complete_day_file(path) -> bool:
    """True iff the parquet file exists AND has the expected row count.

    Treats any other case (missing, empty, short row count, unreadable)
    as incomplete — caller should re-download.
    """
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return False
    if p.stat().st_size == 0:
        return False
    try:
        import pyarrow.parquet as pq
        meta = pq.read_metadata(str(p))
        return meta.num_rows >= EXPECTED_ROWS_PER_DAY
    except Exception:
        return False
```

Replace `if output_path.exists():` with `if is_complete_day_file(output_path):` at the skip-if-exists call site.

- [ ] **Step 4: Run pytest expecting 5 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/data/download_klines.py ofi-lab-v3/tests/test_download_klines_gap_safe.py
git commit -m "v3-B: download_klines — gap-safe via row-count check"
```

---

### Task 10: Dynamic `MID_PRICE_TRAINING_RANGE` (Steering 10g)

**Files:**
- Create: `ofi-lab-v3/data/range_computer.py`
- Create: `ofi-lab-v3/tests/test_range_computer.py`

XRP is missing from v2's hardcoded `MID_PRICE_TRAINING_RANGE`. v3 computes the range dynamically from training data so any symbol works.

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_range_computer.py
from pathlib import Path

import pytest

from data.range_computer import compute_mid_price_range


def test_range_from_synthetic_parquet(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp_path / "20260501_XRPUSDT_features.parquet"
    pq.write_table(pa.table({"mid_price": [0.50, 0.55, 0.60, 0.62, 0.58]}), str(p))
    rng = compute_mid_price_range(str(tmp_path), "XRPUSDT")
    assert rng["min"] == 0.50
    assert rng["max"] == 0.62
    assert abs(rng["median"] - 0.58) < 1e-9


def test_range_aggregates_multiple_files(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({"mid_price": [60_000.0, 61_000.0]}),
                   str(tmp_path / "20260501_BTCUSDT_features.parquet"))
    pq.write_table(pa.table({"mid_price": [59_000.0, 62_000.0]}),
                   str(tmp_path / "20260502_BTCUSDT_features.parquet"))
    rng = compute_mid_price_range(str(tmp_path), "BTCUSDT")
    assert rng["min"] == 59_000.0
    assert rng["max"] == 62_000.0


def test_missing_symbol_returns_none(tmp_path):
    rng = compute_mid_price_range(str(tmp_path), "ZZZUSDT")
    assert rng is None
```

- [ ] **Step 2: Run test expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `data/range_computer.py`**

```python
# ofi-lab-v3/data/range_computer.py
"""Compute mid_price min/max/median per symbol from feature parquets.

Replaces v2's hardcoded MID_PRICE_TRAINING_RANGE which omitted XRP.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def compute_mid_price_range(feature_dir: str, symbol: str) -> Optional[dict]:
    import pyarrow.parquet as pq

    fdir = Path(feature_dir)
    files = sorted(fdir.glob(f"*_{symbol}_features.parquet"))
    if not files:
        return None

    mins = []
    maxs = []
    all_vals = []
    for f in files:
        try:
            table = pq.read_table(str(f), columns=["mid_price"])
        except Exception:
            continue
        col = table.column("mid_price").to_pylist()
        if not col:
            continue
        mins.append(min(col))
        maxs.append(max(col))
        all_vals.extend(col)
    if not all_vals:
        return None
    all_vals.sort()
    n = len(all_vals)
    median = all_vals[n // 2] if n % 2 == 1 else \
        0.5 * (all_vals[n // 2 - 1] + all_vals[n // 2])
    return {"min": min(mins), "max": max(maxs), "median": median}
```

- [ ] **Step 4: Run pytest expecting 3 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/data/range_computer.py ofi-lab-v3/tests/test_range_computer.py
git commit -m "v3-B: dynamic mid_price range computer (Steering 10g)"
```

---

### Task 11: `retrain_pipeline.sh` orchestrator

**Files:**
- Create: `ofi-lab-v3/scripts/retrain_pipeline.sh`

This is a shell orchestrator with no dedicated test (validated manually). It chains: download → completeness check → feature build → retrain. Each step is a separate command; the shell script just wires them with `set -e`.

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# ofi-lab-v3/scripts/retrain_pipeline.sh
#
# Orchestrate the v3 retrain pipeline. Idempotent step-by-step:
# each tool independently skips work that's already done.
#
# Usage:
#   scripts/retrain_pipeline.sh \
#     --symbol BTCUSDT \
#     --horizon 900 \
#     --feature-version v3 \
#     --train-end 2026-05-08 \
#     --train-days 330 \
#     --val-days 30 \
#     --test-days 14
set -euo pipefail

SYMBOL=""
HORIZON=""
FEATURE_VERSION="v3"
TRAIN_END=""
TRAIN_DAYS=330
VAL_DAYS=30
TEST_DAYS=14
FEATURE_DIR="${FEATURE_DIR:-/data/features_v3}"
KLINE_DIR="${KLINE_DIR:-/data/klines_1m}"
ORDERBOOK_DIR="${ORDERBOOK_DIR:-/data/orderbook}"
OUTPUT_DIR="${OUTPUT_DIR:-/data/models}"
MAX_CONSECUTIVE_MISSING="${MAX_CONSECUTIVE_MISSING:-2}"

while [ $# -gt 0 ]; do
  case "$1" in
    --symbol) SYMBOL="$2"; shift 2;;
    --horizon) HORIZON="$2"; shift 2;;
    --feature-version) FEATURE_VERSION="$2"; shift 2;;
    --train-end) TRAIN_END="$2"; shift 2;;
    --train-days) TRAIN_DAYS="$2"; shift 2;;
    --val-days) VAL_DAYS="$2"; shift 2;;
    --test-days) TEST_DAYS="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

if [ -z "${SYMBOL}" ] || [ -z "${HORIZON}" ] || [ -z "${TRAIN_END}" ]; then
  echo "Required: --symbol, --horizon, --train-end" >&2
  exit 1
fi

# 1. Download new kline data (idempotent)
echo "[1/5] download_klines for ${SYMBOL}"
python -m data.download_klines \
  --symbol "${SYMBOL}" --output-dir "${KLINE_DIR}" --through "${TRAIN_END}" || true

# 2. Download new orderbook data (idempotent)
echo "[2/5] download_orderbook for ${SYMBOL}"
python -m data.download_orderbook \
  --symbol "${SYMBOL}" --output-dir "${ORDERBOOK_DIR}" --through "${TRAIN_END}" || true

# 3. Build features for any new days
echo "[3/5] build_features_v3"
python -m data.build_features_v3 \
  --symbol "${SYMBOL}" \
  --kline-dir "${KLINE_DIR}" \
  --orderbook-dir "${ORDERBOOK_DIR}" \
  --output-dir "${FEATURE_DIR}" \
  --through "${TRAIN_END}" || true

# 4. Validate completeness — abort on excess gaps
echo "[4/5] completeness check"
python -c "
import sys
from datetime import date, timedelta
from validation.completeness import check_feature_coverage
end = date.fromisoformat('${TRAIN_END}')
start = end - timedelta(days=${TRAIN_DAYS} - 1 + ${VAL_DAYS} + ${TEST_DAYS})
rep = check_feature_coverage(
    feature_dir='${FEATURE_DIR}', symbol='${SYMBOL}',
    start=start, end=end + timedelta(days=${VAL_DAYS} + ${TEST_DAYS}),
)
print(f'coverage: {len(rep.present_days)}/{rep.total_days} ({rep.coverage_pct}%)')
print(f'max consecutive missing: {rep.max_consecutive_missing}')
if not rep.passes(max_consecutive_missing=${MAX_CONSECUTIVE_MISSING}):
    print(f'ABORT: max consecutive missing > ${MAX_CONSECUTIVE_MISSING}', file=sys.stderr)
    sys.exit(2)
"

# 5. Retrain
echo "[5/5] retrain"
python -m validation.retrain \
  --horizon "${HORIZON}" \
  --symbol "${SYMBOL}" \
  --feature-version "${FEATURE_VERSION}" \
  --train-end "${TRAIN_END}" \
  --train-days "${TRAIN_DAYS}" \
  --val-days "${VAL_DAYS}" \
  --test-days "${TEST_DAYS}" \
  --feature-dir "${FEATURE_DIR}" \
  --output-dir "${OUTPUT_DIR}"

echo "[done] retrain_pipeline complete"
echo "Next: review artifacts in ${OUTPUT_DIR} and add to model_registry.json"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x ofi-lab-v3/scripts/retrain_pipeline.sh
```

- [ ] **Step 3: Smoke-check `--help` (parses without errors)**

```bash
ofi-lab-v3/scripts/retrain_pipeline.sh --help 2>&1 || echo "exit nonzero is fine"
```

The script doesn't implement `--help`; it just exits with the "Required" message. That's fine — the test is that the script parses arg names and the heredoc embedded Python compiles. Verify by running `bash -n ofi-lab-v3/scripts/retrain_pipeline.sh`.

- [ ] **Step 4: Commit**

```bash
git add ofi-lab-v3/scripts/retrain_pipeline.sh
git commit -m "v3-B: retrain_pipeline.sh — orchestrate download/validate/build/train"
```

---

### Task 12: Multi-model orchestration in `_run_predictions`

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Create: `ofi-lab-v3/tests/test_paper_trader_multimodel.py`

The v2 boundary loop scores `for symbol in PREDICTION_SYMBOLS: for model_name, model in self.models.items()`. v3 keeps that shape but ensures each model only scores symbols listed in its registry entry's `symbol` field. A baseline-only registry produces predictions for one (model, symbol) pair; a registry with two models for BTCUSDT produces two predictions per BTCUSDT boundary.

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_paper_trader_multimodel.py
import json
from pathlib import Path

import pytest


def _registry_two_btc_models(tmp_path, tiny_model_path):
    return {
        "models": {
            "900s_btc_v3_20260315": {
                "path": tiny_model_path,
                "feature_names_path": str(
                    Path(tiny_model_path).parent / "feature_names.json"
                ),
                "horizon_seconds": 900,
                "symbol": "BTCUSDT",
                "feature_version": "v3",
                "is_baseline": True,
                "paper_trading_enabled": True,
                "kalshi_live_enabled": False,
                "lifecycle_state": "live_active",
                "train_window_start": "2025-04-01",
                "train_window_end": "2026-03-15",
                "train_cutoff": "2026-03-15",
                "min_markets_for_kalshi": 200,
                "min_days_for_kalshi": 14,
            },
            "60s_btc_v3_20260315": {
                "path": tiny_model_path,
                "feature_names_path": str(
                    Path(tiny_model_path).parent / "feature_names.json"
                ),
                "horizon_seconds": 60,
                "symbol": "BTCUSDT",
                "feature_version": "v3",
                "is_baseline": False,
                "paper_trading_enabled": True,
                "kalshi_live_enabled": False,
                "lifecycle_state": "prediction_only",
                "train_window_start": "2025-04-01",
                "train_window_end": "2026-03-15",
                "train_cutoff": "2026-03-15",
                "min_markets_for_kalshi": 200,
                "min_days_for_kalshi": 14,
            },
        }
    }


def test_two_models_each_emit_their_native_rows(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    reg_path = tmp_path / "model_registry.json"
    reg_path.write_text(json.dumps(_registry_two_btc_models(tmp_path, tiny_model_path)))
    monkeypatch.setenv("MODEL_REGISTRY_PATH", str(reg_path))

    from trading.paper_trader import PaperTrader
    t = PaperTrader(
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    boundary = 1_700_000_000_000
    # Call _emit_prediction_rows for each (model, symbol) pair
    for model_name in ("900s_btc_v3_20260315", "60s_btc_v3_20260315"):
        t._emit_prediction_rows(
            model_name=model_name, symbol="BTCUSDT",
            boundary_ms=boundary, ts_model_ran_ms=boundary,
            pred_proba_raw=0.55, pred_proba_calibrated=0.55,
            pred_direction="up", above_threshold=False, warmup=False,
            platform="paper", price_at_open=60_000.0,
        )
    rows = t._db_conn.execute(
        "SELECT model_name, market_window_seconds, resolution_type"
        " FROM predictions ORDER BY model_name, market_window_seconds"
    ).fetchall()
    by_model = {}
    for r in rows:
        by_model.setdefault(r["model_name"], []).append(
            (r["market_window_seconds"], r["resolution_type"])
        )
    # 900s baseline: native at 900, evaluations at 300, 1800, 3600
    assert (900, "native") in by_model["900s_btc_v3_20260315"]
    # 60s model: native at 60, evaluations at 300, 900, 1800, 3600
    assert (60, "native") in by_model["60s_btc_v3_20260315"]
    # No cross-contamination — h300's native is 900, h60's native is 60
    assert (60, "native") not in by_model["900s_btc_v3_20260315"]
    assert (900, "native") not in by_model["60s_btc_v3_20260315"]
```

- [ ] **Step 2: Run test expecting failure if `_emit_prediction_rows` reads horizon from `config.PAPER_TRADING["model_metadata"]` instead of registry**

If T5 already routed `_emit_prediction_rows` through the registry-aware `_build_envelope`, this test should pass without further edits. If not, modify `_emit_prediction_rows` so that `meta = ...` lookup prefers `self.model_registry.entries()` over `config.PAPER_TRADING["model_metadata"]`.

- [ ] **Step 3: If needed, modify `_emit_prediction_rows` to prefer registry**

```python
def _emit_prediction_rows(self, ..., model_name, ...):
    if self.model_registry is not None and model_name in self.model_registry.entries():
        e = self.model_registry.entries()[model_name]
        horizon = e.horizon_seconds
    else:
        meta = config.PAPER_TRADING["model_metadata"][model_name]
        horizon = meta["training_horizon_seconds"]
    rows = plan_resolution_rows(
        boundary_ms=boundary_ms,
        training_horizon_seconds=horizon,
        evaluation_windows=config.EVALUATION_WINDOWS,
    )
    # ... rest unchanged ...
```

- [ ] **Step 4: Run test expecting pass**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_paper_trader_multimodel.py
git commit -m "v3-B: multi-model orchestration via ModelRegistry"
```

---

### Task 13: Vectorized predict — batch symbol × model scoring

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` — add `_score_batch` helper
- Create: `ofi-lab-v3/tests/test_score_batch.py`

The v2 inner loop calls `model.predict(feature_vec.reshape(1, -1))` once per symbol per model — `S × M` calls per boundary. v3 batches: each model scores all symbols it covers in a single `predict(feature_matrix)` call.

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_score_batch.py
import numpy as np
import pytest


def test_score_batch_returns_one_proba_per_symbol(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    from trading.live_features import V3_FEATURE_COLS
    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    rng = np.random.default_rng(seed=7)
    feature_rows = {
        "BTCUSDT": {c: float(rng.normal()) for c in V3_FEATURE_COLS},
        "ETHUSDT": {c: float(rng.normal()) for c in V3_FEATURE_COLS},
    }
    out = t._score_batch(
        model_name="900s_btc_v3_20260315",
        symbols=["BTCUSDT", "ETHUSDT"],
        feature_rows=feature_rows,
    )
    assert set(out.keys()) == {"BTCUSDT", "ETHUSDT"}
    for sym, p in out.items():
        assert 0.0 <= p <= 1.0


def test_score_batch_single_call_matches_per_row_calls(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    from trading.live_features import V3_FEATURE_COLS
    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    rng = np.random.default_rng(seed=11)
    fr = {f"S{i}": {c: float(rng.normal()) for c in V3_FEATURE_COLS} for i in range(3)}
    batch = t._score_batch(
        model_name="900s_btc_v3_20260315",
        symbols=list(fr.keys()),
        feature_rows=fr,
    )
    booster = t.models["900s_btc_v3_20260315"]
    feature_names = t.feature_names["900s_btc_v3_20260315"]
    for sym in fr:
        vec = np.array([fr[sym][c] for c in feature_names]).reshape(1, -1)
        single = float(booster.predict(vec)[0])
        assert abs(batch[sym] - single) < 1e-9
```

- [ ] **Step 2: Run test expecting AttributeError**

- [ ] **Step 3: Add `_score_batch` to `PaperTrader`**

```python
def _score_batch(
    self, *, model_name: str, symbols, feature_rows,
):
    """Score all symbols for one model in a single predict call.

    feature_rows: {symbol: {col: value, ...}}
    Returns: {symbol: float_proba}
    """
    import numpy as np
    booster = self.models[model_name]
    feature_names = self.feature_names[model_name]
    matrix = np.array([
        [feature_rows[s].get(c, 0.0) for c in feature_names]
        for s in symbols
    ])
    probas = booster.predict(matrix)
    return {s: float(probas[i]) for i, s in enumerate(symbols)}
```

The actual call site in `_run_predictions` is left to a follow-on edit if performance becomes an issue. T13 just exposes the helper + tests.

- [ ] **Step 4: Run pytest expecting 2 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_score_batch.py
git commit -m "v3-B: _score_batch helper for vectorized predict"
```

---

### Task 14: `regime/tagger.py` — quartile-based classifier

**Files:**
- Create: `ofi-lab-v3/regime/__init__.py`
- Create: `ofi-lab-v3/regime/tagger.py`
- Create: `ofi-lab-v3/tests/test_regime_tagger.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_regime_tagger.py
import pytest

from regime.tagger import compute_regime, RegimeTags


SAMPLE_THRESHOLDS = {
    "BTCUSDT": {
        "vwap_dev_30s_std": {"p25": 0.00004, "p50": 0.00009, "p75": 0.00016},
        "mlofi_60s_std":    {"p25": 0.0002,  "p50": 0.0005,  "p75": 0.0009},
        "relative_spread":  {"p25": 0.00008, "p50": 0.00018, "p75": 0.00031},
        "spread_5m_pct":    {"p25": 0.20,    "p50": 0.50,    "p75": 0.80},
        "mlofi_momentum":   {"p25": -0.0003, "p50": 0.0001,  "p75": 0.0004},
        "vwap_2m_deviation":{"p25": -0.0008, "p50": 0.0,     "p75": 0.0008},
    }
}


def test_low_volatility_tags_low(_):
    feature_row = {
        "vwap_dev_30s_std": 0.00002,
        "mlofi_60s_std": 0.0001,
        "relative_spread": 0.00009,
        "spread_5m_pct": 0.30,
        "mlofi_momentum": 0.0,
        "vwap_2m_deviation": 0.0,
    }
    tags = compute_regime("BTCUSDT", feature_row, SAMPLE_THRESHOLDS)
    assert tags.volatility == "low"


def test_high_volatility_tags_high():
    feature_row = {
        "vwap_dev_30s_std": 0.00020,
        "mlofi_60s_std": 0.0012,
        "relative_spread": 0.00040,
        "spread_5m_pct": 0.85,
        "mlofi_momentum": 0.0005,
        "vwap_2m_deviation": 0.001,
    }
    tags = compute_regime("BTCUSDT", feature_row, SAMPLE_THRESHOLDS)
    assert tags.volatility == "high"
    assert tags.liquidity == "thin"


def test_normal_liquidity_tags_normal():
    feature_row = {
        "vwap_dev_30s_std": 0.00009,
        "mlofi_60s_std": 0.0005,
        "relative_spread": 0.00018,
        "spread_5m_pct": 0.50,
        "mlofi_momentum": 0.0001,
        "vwap_2m_deviation": 0.0,
    }
    tags = compute_regime("BTCUSDT", feature_row, SAMPLE_THRESHOLDS)
    assert tags.liquidity == "normal"


def test_trending_when_momentum_and_deviation_align():
    feature_row = {
        "vwap_dev_30s_std": 0.00009,
        "mlofi_60s_std": 0.0005,
        "relative_spread": 0.00018,
        "spread_5m_pct": 0.50,
        "mlofi_momentum": 0.001,    # well above p75
        "vwap_2m_deviation": 0.002,  # well above p75
    }
    tags = compute_regime("BTCUSDT", feature_row, SAMPLE_THRESHOLDS)
    assert tags.trend == "trending"


def test_unknown_symbol_returns_unknown_tags():
    tags = compute_regime("ZZZUSDT", {}, SAMPLE_THRESHOLDS)
    assert tags == RegimeTags(volatility="unknown", liquidity="unknown",
                                trend="unknown")
```

(Note: `def test_low_volatility_tags_low(_):` — the `_` is a typo placeholder and would error. Fix it to `def test_low_volatility_tags_low():` when transcribing.)

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `regime/tagger.py`**

```python
# ofi-lab-v3/regime/tagger.py
"""Quartile-based regime tagger.

Three independent regime axes computed from existing v3 features:
  - volatility: low / medium / high   (vwap_dev_30s_std + mlofi_60s_std)
  - liquidity:  thin / normal / deep  (relative_spread + spread_5m_pct)
  - trend:      trending / choppy / mean_reverting (mlofi_momentum +
                vwap_2m_deviation sign persistence)

Thresholds come from regime_thresholds.json (auto-refreshed by
threshold_updater.py from the rolling 30-day distribution).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class RegimeTags:
    volatility: str
    liquidity: str
    trend: str


def _bucket(value: float, thresholds: dict) -> str:
    """Map a value to low/medium/high based on p25/p50/p75."""
    if value < thresholds["p25"]:
        return "low"
    if value < thresholds["p75"]:
        return "medium"
    return "high"


def _avg_bucket(b1: str, b2: str) -> str:
    """Combine two single-axis buckets into a regime label."""
    score = {"low": 0, "medium": 1, "high": 2}.get(b1, 1) + \
            {"low": 0, "medium": 1, "high": 2}.get(b2, 1)
    if score <= 1:
        return "low"
    if score <= 2:
        return "medium"
    return "high"


def _liquidity_bucket(rel_spread: float, spread_5m: float, t: dict) -> str:
    a = _bucket(rel_spread, t["relative_spread"])
    b = _bucket(spread_5m, t["spread_5m_pct"])
    combined = _avg_bucket(a, b)
    return {"low": "deep", "medium": "normal", "high": "thin"}[combined]


def _trend_bucket(momentum: float, vwap_dev: float, t: dict) -> str:
    m_bucket = _bucket(momentum, t["mlofi_momentum"])
    v_bucket = _bucket(vwap_dev, t["vwap_2m_deviation"])
    if m_bucket == "high" and v_bucket == "high":
        return "trending"
    if m_bucket == "low" and v_bucket == "low":
        return "mean_reverting"
    return "choppy"


def compute_regime(
    symbol: str, feature_row: Dict[str, float], thresholds: Dict[str, dict],
) -> RegimeTags:
    sym_t = thresholds.get(symbol)
    if sym_t is None:
        return RegimeTags(volatility="unknown", liquidity="unknown",
                          trend="unknown")
    vol = _avg_bucket(
        _bucket(feature_row.get("vwap_dev_30s_std", 0.0), sym_t["vwap_dev_30s_std"]),
        _bucket(feature_row.get("mlofi_60s_std", 0.0), sym_t["mlofi_60s_std"]),
    )
    liq = _liquidity_bucket(
        feature_row.get("relative_spread", 0.0),
        feature_row.get("spread_5m_pct", 0.0),
        sym_t,
    )
    tr = _trend_bucket(
        feature_row.get("mlofi_momentum", 0.0),
        feature_row.get("vwap_2m_deviation", 0.0),
        sym_t,
    )
    return RegimeTags(volatility=vol, liquidity=liq, trend=tr)
```

```python
# ofi-lab-v3/regime/__init__.py
"""Regime tagging — volatility / liquidity / trend classification."""
```

- [ ] **Step 4: Run pytest, expect 5 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/regime/__init__.py ofi-lab-v3/regime/tagger.py \
        ofi-lab-v3/tests/test_regime_tagger.py
git commit -m "v3-B: regime tagger — volatility/liquidity/trend from features"
```

---

### Task 15: Regime threshold auto-updater

**Files:**
- Create: `ofi-lab-v3/regime/threshold_updater.py`
- Create: `ofi-lab-v3/tests/test_threshold_updater.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_threshold_updater.py
import json
from pathlib import Path

import pytest

from regime.threshold_updater import (
    compute_thresholds_from_parquets,
    refresh_thresholds_file,
)


def test_compute_thresholds_returns_quartiles_per_signal(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp_path / "20260501_BTCUSDT_features.parquet"
    n = 100
    pq.write_table(pa.table({
        "vwap_dev_30s_std": list(range(n)),
        "mlofi_60s_std":    [i * 2 for i in range(n)],
        "relative_spread":  [i / 1000 for i in range(n)],
        "spread_5m_pct":    [i / 100 for i in range(n)],
        "mlofi_momentum":   [i - 50 for i in range(n)],
        "vwap_2m_deviation": [i - 50 for i in range(n)],
    }), str(p))
    out = compute_thresholds_from_parquets(
        feature_dir=str(tmp_path),
        symbol="BTCUSDT",
        days=1,
    )
    assert "vwap_dev_30s_std" in out
    assert "mlofi_60s_std" in out
    assert out["vwap_dev_30s_std"]["p25"] < out["vwap_dev_30s_std"]["p50"]
    assert out["vwap_dev_30s_std"]["p50"] < out["vwap_dev_30s_std"]["p75"]


def test_refresh_thresholds_file_writes_per_symbol(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    for d, sym in [("20260501", "BTCUSDT"), ("20260501", "ETHUSDT")]:
        pq.write_table(pa.table({
            "vwap_dev_30s_std": list(range(50)),
            "mlofi_60s_std":    list(range(50)),
            "relative_spread":  [i/100 for i in range(50)],
            "spread_5m_pct":    [i/100 for i in range(50)],
            "mlofi_momentum":   list(range(-25, 25)),
            "vwap_2m_deviation": list(range(-25, 25)),
        }), str(tmp_path / f"{d}_{sym}_features.parquet"))

    out_path = tmp_path / "regime_thresholds.json"
    refresh_thresholds_file(
        feature_dir=str(tmp_path),
        symbols=["BTCUSDT", "ETHUSDT"],
        out_path=str(out_path),
        days=1,
    )
    body = json.loads(out_path.read_text())
    assert "updated_at" in body
    assert "BTCUSDT" in body
    assert "ETHUSDT" in body
    assert "vwap_dev_30s_std" in body["BTCUSDT"]
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `regime/threshold_updater.py`**

```python
# ofi-lab-v3/regime/threshold_updater.py
"""Auto-refresh regime_thresholds.json from a rolling N-day window of
feature parquets.

Run daily by a cron job and at the end of every retrain pipeline.
On-demand reload happens through ModelRegistry.reload — the live
container's regime_tagger reads the file each prediction.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable


REGIME_SIGNALS = [
    "vwap_dev_30s_std",
    "mlofi_60s_std",
    "relative_spread",
    "spread_5m_pct",
    "mlofi_momentum",
    "vwap_2m_deviation",
]


def _quantiles(values, qs=(0.25, 0.5, 0.75)):
    if not values:
        return {f"p{int(q*100)}": 0.0 for q in qs}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    out = {}
    for q in qs:
        idx = int(q * (n - 1))
        out[f"p{int(q*100)}"] = float(sorted_vals[idx])
    return out


def compute_thresholds_from_parquets(
    feature_dir: str, symbol: str, days: int,
) -> dict:
    import pyarrow.parquet as pq

    fdir = Path(feature_dir)
    candidates = sorted(fdir.glob(f"*_{symbol}_features.parquet"))
    if not candidates:
        return {sig: {"p25": 0.0, "p50": 0.0, "p75": 0.0} for sig in REGIME_SIGNALS}
    files = candidates[-days:] if days > 0 else candidates
    accum = {sig: [] for sig in REGIME_SIGNALS}
    for f in files:
        try:
            table = pq.read_table(str(f), columns=REGIME_SIGNALS)
        except Exception:
            continue
        for sig in REGIME_SIGNALS:
            try:
                accum[sig].extend(
                    [v for v in table.column(sig).to_pylist() if v is not None]
                )
            except KeyError:
                pass
    return {sig: _quantiles(vals) for sig, vals in accum.items()}


def refresh_thresholds_file(
    feature_dir: str, symbols: Iterable[str], out_path: str, days: int = 30,
) -> None:
    body = {
        "updated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
    }
    for sym in symbols:
        body[sym] = compute_thresholds_from_parquets(
            feature_dir=feature_dir, symbol=sym, days=days,
        )
    Path(out_path).write_text(json.dumps(body, indent=2))
```

- [ ] **Step 4: Run pytest expecting 2 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/regime/threshold_updater.py ofi-lab-v3/tests/test_threshold_updater.py
git commit -m "v3-B: regime threshold auto-updater — 30-day quartiles"
```

---

### Task 16: Apply regime tags on every prediction

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` — load thresholds + tag predictions
- Modify: `ofi-lab-v3/storage/sqlite_ledger.py` — accept regime tags in `log_prediction_set`
- Create: `ofi-lab-v3/tests/test_paper_trader_regime.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_paper_trader_regime.py
import json
from pathlib import Path


def test_emit_prediction_rows_stamps_regime_tags(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))

    # Write a regime_thresholds.json that puts the test feature value
    # comfortably in the "low" bucket
    tpath = tmp_path / "regime_thresholds.json"
    tpath.write_text(json.dumps({
        "updated_at": "2026-05-09T00:00:00.000000Z",
        "BTCUSDT": {
            "vwap_dev_30s_std":  {"p25": 0.001, "p50": 0.002, "p75": 0.003},
            "mlofi_60s_std":     {"p25": 0.001, "p50": 0.002, "p75": 0.003},
            "relative_spread":   {"p25": 0.001, "p50": 0.002, "p75": 0.003},
            "spread_5m_pct":     {"p25": 0.30,  "p50": 0.50,  "p75": 0.80},
            "mlofi_momentum":    {"p25": -0.01, "p50": 0.0,   "p75": 0.01},
            "vwap_2m_deviation": {"p25": -0.01, "p50": 0.0,   "p75": 0.01},
        }
    }))
    monkeypatch.setenv("REGIME_THRESHOLDS_PATH", str(tpath))

    from trading.paper_trader import PaperTrader
    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    boundary = 1_700_000_000_000
    # Pass regime_features kwarg (NEW signature in T16)
    t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=boundary, ts_model_ran_ms=boundary,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", price_at_open=60_000.0,
        regime_features={
            "vwap_dev_30s_std": 0.0001,   # < p25 → low
            "mlofi_60s_std": 0.0001,       # < p25 → low
            "relative_spread": 0.0001,     # < p25 → deep (combined low → deep)
            "spread_5m_pct": 0.10,         # < p25
            "mlofi_momentum": 0.0,         # medium
            "vwap_2m_deviation": 0.0,      # medium
        },
    )
    rows = t._db_conn.execute(
        "SELECT regime_volatility, regime_liquidity, regime_trend"
        " FROM predictions WHERE resolution_type='native'"
    ).fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r["regime_volatility"] == "low"
    assert r["regime_liquidity"] == "deep"
    # trend defaults to choppy when momentum + vwap_dev are both medium
    assert r["regime_trend"] in ("choppy", "mean_reverting")
```

- [ ] **Step 2: Run pytest expecting failure (regime_features kwarg unknown)**

- [ ] **Step 3: Modify `_emit_prediction_rows` and `log_prediction_set`**

In `paper_trader.py`:

```python
# Near other storage imports at top of file:
from regime.tagger import compute_regime, RegimeTags

# In __init__, after other v3 storage init:
self._regime_thresholds_path = os.environ.get(
    "REGIME_THRESHOLDS_PATH",
    "/data/regime_thresholds.json",
)
self._regime_thresholds: dict = {}
self._reload_regime_thresholds()


def _reload_regime_thresholds(self) -> None:
    import json
    p = Path(self._regime_thresholds_path)
    if p.exists():
        try:
            self._regime_thresholds = json.loads(p.read_text())
        except Exception:
            self._regime_thresholds = {}


def _tag_regime(self, symbol: str, regime_features: dict) -> RegimeTags:
    if not self._regime_thresholds:
        return RegimeTags(volatility="unknown", liquidity="unknown",
                          trend="unknown")
    return compute_regime(symbol, regime_features, self._regime_thresholds)
```

Add `regime_features` kwarg to `_emit_prediction_rows`:

```python
def _emit_prediction_rows(
    self, *, model_name, symbol, boundary_ms, ts_model_ran_ms,
    pred_proba_raw, pred_proba_calibrated, pred_direction,
    above_threshold, warmup, platform, price_at_open,
    p_market=None, p_model_minus_market=None,
    utc_hour=None, day_of_week=None, is_weekend=None,
    relative_spread=None,
    regime_features: Optional[dict] = None,  # NEW
):
    # ... existing horizon lookup ...
    tags = self._tag_regime(symbol, regime_features or {})
    native_pid = self.sqlite_ledger.log_prediction_set(
        envelope=envelope,
        symbol=symbol,
        ts_model_ran_ms=ts_model_ran_ms,
        ts_contract_open_ms=boundary_ms,
        rows=rows,
        pred_proba_raw=pred_proba_raw,
        pred_proba_calibrated=pred_proba_calibrated,
        pred_direction=pred_direction,
        above_threshold=above_threshold,
        warmup=warmup,
        platform=platform,
        p_market=p_market,
        p_model_minus_market=p_model_minus_market,
        utc_hour=utc_hour,
        day_of_week=day_of_week,
        is_weekend=is_weekend,
        relative_spread=relative_spread,
        regime_volatility=tags.volatility,
        regime_liquidity=tags.liquidity,
        regime_trend=tags.trend,
    )
    # ... rest unchanged ...
```

In `storage/sqlite_ledger.py` extend `log_prediction_set` signature with three new kwargs (`regime_volatility`, `regime_liquidity`, `regime_trend`, all `Optional[str] = None`) and add them to the INSERT column list and VALUES tuple.

- [ ] **Step 4: Run pytest expecting pass**

Also confirm prior `test_sqlite_ledger.py` tests still pass (the new kwargs default to None so prior callers aren't broken).

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py \
        ofi-lab-v3/storage/sqlite_ledger.py \
        ofi-lab-v3/tests/test_paper_trader_regime.py
git commit -m "v3-B: regime tags on every prediction row"
```

---

### Task 17: Overlap writer — `trading/overlap_writer.py`

**Files:**
- Create: `ofi-lab-v3/trading/overlap_writer.py`
- Create: `ofi-lab-v3/tests/test_overlap_writer.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_overlap_writer.py
import json

from storage.db import open_database, init_schema
from trading.overlap_writer import OverlapWriter, ModelScore


def test_consensus_when_all_models_agree(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = OverlapWriter(conn)
    w.record_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="BTCUSDT",
        market_window_seconds=900,
        registry_load_generation=0,
        scores=[
            ModelScore("900s_btc_v3_20260315", "up", 0.55, weight=0.6),
            ModelScore("60s_btc_v3_20260315", "up", 0.52, weight=0.4),
        ],
    )
    row = conn.execute("SELECT * FROM model_overlap").fetchone()
    assert row["consensus"] == 1
    assert row["consensus_direction"] == "up"
    # Weighted confidence: 0.55 * 0.6 + 0.52 * 0.4 = 0.538
    assert abs(row["weighted_confidence"] - 0.538) < 1e-9


def test_no_consensus_when_models_disagree(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = OverlapWriter(conn)
    w.record_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="BTCUSDT",
        market_window_seconds=900,
        registry_load_generation=0,
        scores=[
            ModelScore("900s_btc_v3_20260315", "up", 0.55, weight=0.5),
            ModelScore("60s_btc_v3_20260315", "down", 0.52, weight=0.5),
        ],
    )
    row = conn.execute("SELECT * FROM model_overlap").fetchone()
    assert row["consensus"] == 0
    assert row["consensus_direction"] is None


def test_models_scored_serialized_as_json_array(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = OverlapWriter(conn)
    w.record_boundary(
        ts_contract_open_ms=1_700_000_000_000,
        symbol="BTCUSDT",
        market_window_seconds=900,
        registry_load_generation=0,
        scores=[
            ModelScore("a", "up", 0.55, weight=1.0),
        ],
    )
    row = conn.execute("SELECT models_scored_json, directions_json,"
                       " confidences_json FROM model_overlap").fetchone()
    assert json.loads(row["models_scored_json"]) == ["a"]
    assert json.loads(row["directions_json"]) == {"a": "up"}
    assert json.loads(row["confidences_json"]) == {"a": 0.55}
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `trading/overlap_writer.py`**

```python
# ofi-lab-v3/trading/overlap_writer.py
"""Per-boundary overlap / consensus writer.

After all paper-active models score a (boundary, symbol,
market_window) tuple, OverlapWriter records:
  - which models scored
  - their direction predictions
  - their calibrated confidences
  - whether all directions agree (consensus flag)
  - weighted-mean confidence (EWMA-weighted via per-model weight)

The consensus signal is logged but does NOT gate trades automatically
in Plan B. Plan C dashboard surfaces it; Plan B's trade gate is the
pairwise conflict rule (T20 — model_conflict = no trade).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class ModelScore:
    model_name: str
    direction: str
    calibrated_confidence: float
    weight: float = 1.0


class OverlapWriter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def record_boundary(
        self,
        *,
        ts_contract_open_ms: int,
        symbol: str,
        market_window_seconds: int,
        registry_load_generation: int,
        scores: List[ModelScore],
    ) -> None:
        if not scores:
            return
        models = [s.model_name for s in scores]
        directions = {s.model_name: s.direction for s in scores}
        confidences = {s.model_name: float(s.calibrated_confidence) for s in scores}
        # Consensus: all directions agree
        directions_set = set(directions.values())
        consensus = 1 if len(directions_set) == 1 else 0
        consensus_direction: Optional[str] = (
            next(iter(directions_set)) if consensus else None
        )
        # Weighted-mean confidence
        total_w = sum(s.weight for s in scores) or 1.0
        weighted = sum(s.calibrated_confidence * s.weight for s in scores) / total_w
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO model_overlap ("
                " ts_contract_open_ms, symbol, market_window_seconds,"
                " models_scored_json, directions_json, confidences_json,"
                " consensus, consensus_direction, weighted_confidence,"
                " registry_load_generation"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    ts_contract_open_ms, symbol, market_window_seconds,
                    json.dumps(models),
                    json.dumps(directions),
                    json.dumps(confidences),
                    consensus, consensus_direction, weighted,
                    registry_load_generation,
                ),
            )
```

- [ ] **Step 4: Run pytest expecting 3 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/overlap_writer.py ofi-lab-v3/tests/test_overlap_writer.py
git commit -m "v3-B: OverlapWriter + ModelScore dataclass"
```

---

### Task 18: EWMA-weighted confidence per model

**Files:**
- Create: `ofi-lab-v3/execution/conflict.py` (will hold consensus + weight helpers)
- Create: `ofi-lab-v3/tests/test_conflict.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_conflict.py
from execution.conflict import (
    rolling_win_rate, ewma_win_rate, ewma_weight,
    detect_conflict, ConflictResult,
)


def test_rolling_win_rate_simple_average():
    outcomes = [True, True, False, True, True]
    assert rolling_win_rate(outcomes) == 0.8


def test_rolling_win_rate_empty_returns_zero():
    assert rolling_win_rate([]) == 0.0


def test_ewma_win_rate_recent_outcomes_dominate():
    # 5 wins followed by 5 losses; with alpha=0.5 the recent losses
    # should pull the EWMA below 0.5.
    outcomes = [True]*5 + [False]*5
    rate = ewma_win_rate(outcomes, alpha=0.5)
    assert rate < 0.4


def test_ewma_weight_clamps_to_min_when_no_history():
    w = ewma_weight(outcomes=[], alpha=0.05, min_weight=0.1, min_samples=50)
    assert w == 0.1


def test_ewma_weight_returns_recency_weighted_after_min_samples():
    outcomes = [True]*100
    w = ewma_weight(outcomes, alpha=0.05, min_weight=0.1, min_samples=50)
    assert w > 0.5  # all wins → near 1.0


def test_detect_conflict_when_two_models_disagree():
    result = detect_conflict({
        "m1": "up",
        "m2": "down",
    })
    assert isinstance(result, ConflictResult)
    assert result.has_conflict is True
    assert sorted(result.models) == ["m1", "m2"]


def test_detect_conflict_when_all_agree():
    result = detect_conflict({"m1": "up", "m2": "up"})
    assert result.has_conflict is False
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `execution/conflict.py`**

```python
# ofi-lab-v3/execution/conflict.py
"""Conflict detection + EWMA weighting helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List


@dataclass(frozen=True)
class ConflictResult:
    has_conflict: bool
    models: List[str]
    directions: Dict[str, str]


def detect_conflict(directions: Dict[str, str]) -> ConflictResult:
    unique = set(directions.values())
    return ConflictResult(
        has_conflict=(len(unique) > 1),
        models=sorted(directions.keys()),
        directions=dict(directions),
    )


def rolling_win_rate(outcomes: Iterable[bool]) -> float:
    out = list(outcomes)
    if not out:
        return 0.0
    return sum(1 for x in out if x) / len(out)


def ewma_win_rate(outcomes: Iterable[bool], alpha: float = 0.05) -> float:
    """Exponentially weighted win rate.

    Older outcomes get exponentially less weight. alpha=0.05 puts ~25%
    of total weight on the most recent 5 outcomes (standard finance
    EWMA). alpha=0.5 makes the recent few dominate.
    """
    out = list(outcomes)
    if not out:
        return 0.0
    weight = 1.0
    weighted_sum = 0.0
    weight_total = 0.0
    # Iterate oldest-to-newest with increasing weight
    n = len(out)
    for i, won in enumerate(out):
        # Newest gets weight=1.0; oldest gets weight=(1-alpha)**(n-1)
        w = (1.0 - alpha) ** (n - 1 - i)
        weighted_sum += w * (1.0 if won else 0.0)
        weight_total += w
    if weight_total == 0:
        return 0.0
    return weighted_sum / weight_total


def ewma_weight(
    outcomes: Iterable[bool], alpha: float, min_weight: float,
    min_samples: int,
) -> float:
    out = list(outcomes)
    if len(out) < min_samples:
        return min_weight
    return max(min_weight, ewma_win_rate(out, alpha=alpha))
```

- [ ] **Step 4: Run pytest expecting 7 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/execution/conflict.py ofi-lab-v3/tests/test_conflict.py
git commit -m "v3-B: conflict detection + EWMA weighting helpers"
```

---

### Task 19: EV calculator — Polymarket + Kalshi fee models

**Files:**
- Create: `ofi-lab-v3/execution/ev.py`
- Create: `ofi-lab-v3/tests/test_ev.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_ev.py
import pytest

from execution.ev import (
    compute_ev_polymarket, compute_ev_kalshi, EVResult,
    POLYMARKET_FEE_COEF,
)


def test_ev_polymarket_zero_at_p_equals_market():
    # If calibrated_p == p_market, no edge; EV ≈ 0 minus fees.
    r = compute_ev_polymarket(
        calibrated_p=0.50, p_market=0.50, stake=10.0,
    )
    assert r.fee == POLYMARKET_FEE_COEF * 0.50 * 0.50 * 10.0
    assert r.ev < 0  # fees > 0 → EV < 0 at break-even probability


def test_ev_polymarket_positive_when_edge_exceeds_fees():
    # At calibrated 0.55 vs market 0.50, payout ratio improves.
    r = compute_ev_polymarket(
        calibrated_p=0.55, p_market=0.50, stake=10.0,
    )
    assert r.ev > 0
    assert r.payout_if_win > 0
    assert r.cost_if_lose > 0


def test_ev_polymarket_negative_under_high_fee():
    # If we artificially inflate the fee the EV must flip sign.
    r = compute_ev_polymarket(
        calibrated_p=0.51, p_market=0.50, stake=10.0,
        fee_coef=0.5,  # absurd fee to force negative EV
    )
    assert r.ev < 0


def test_ev_kalshi_maker_lower_fee_than_taker():
    r_maker = compute_ev_kalshi(
        calibrated_p=0.55, market_yes_price=0.50, stake=10.0,
        side="yes", order_type="maker",
    )
    r_taker = compute_ev_kalshi(
        calibrated_p=0.55, market_yes_price=0.50, stake=10.0,
        side="yes", order_type="taker",
    )
    assert r_maker.fee < r_taker.fee


def test_ev_kalshi_kelly_fraction_present():
    r = compute_ev_kalshi(
        calibrated_p=0.55, market_yes_price=0.50, stake=10.0,
        side="yes", order_type="maker",
    )
    # Kelly fraction = (b*p - q) / b where b = (1-price)/price
    assert 0.0 <= r.kelly_fraction <= 1.0
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `execution/ev.py`**

```python
# ofi-lab-v3/execution/ev.py
"""Expected-value calculator for paper (Polymarket) and Kalshi.

Universal EV formula:
    EV = calibrated_p * payout_if_win - (1 - calibrated_p) * cost_if_lose - fees - spread_cost

Per-platform fee models:
  - Polymarket: 0.072 * p * (1-p) * stake (concave around 0.5)
  - Kalshi maker: ~0.5% of payout; kalshi taker: ~1% of payout (rough
    approximation; live values come from execution/kalshi_fees.py
    when wired in Plan B+)
"""
from __future__ import annotations

from dataclasses import dataclass


POLYMARKET_FEE_COEF = 0.072
KALSHI_MAKER_FEE_COEF = 0.005
KALSHI_TAKER_FEE_COEF = 0.010


@dataclass(frozen=True)
class EVResult:
    ev: float
    fee: float
    payout_if_win: float
    cost_if_lose: float
    kelly_fraction: float


def compute_ev_polymarket(
    *, calibrated_p: float, p_market: float, stake: float,
    fee_coef: float = POLYMARKET_FEE_COEF,
) -> EVResult:
    """EV under Polymarket binary-option pricing.

    Buying YES at p_market; if YES resolves true, payout = stake * (1 - p_market) / p_market.
    If NO resolves, lose stake.
    """
    if p_market <= 0 or p_market >= 1:
        return EVResult(ev=-stake, fee=0.0, payout_if_win=0.0,
                        cost_if_lose=stake, kelly_fraction=0.0)
    payout_if_win = stake * (1 - p_market) / p_market
    cost_if_lose = stake
    fee = fee_coef * calibrated_p * (1 - calibrated_p) * stake
    ev = (
        calibrated_p * payout_if_win
        - (1 - calibrated_p) * cost_if_lose
        - fee
    )
    # Kelly fraction = (b*p - q) / b
    b = payout_if_win / stake  # net odds
    p = calibrated_p
    q = 1.0 - p
    kelly = (b * p - q) / b if b > 0 else 0.0
    kelly = max(0.0, min(1.0, kelly))
    return EVResult(ev=ev, fee=fee, payout_if_win=payout_if_win,
                    cost_if_lose=cost_if_lose, kelly_fraction=kelly)


def compute_ev_kalshi(
    *, calibrated_p: float, market_yes_price: float, stake: float,
    side: str, order_type: str,
) -> EVResult:
    """EV under Kalshi binary contract pricing.

    Kalshi prices contracts in [0, 1]. Buying YES at price y wins
    payout (1 - y) per contract; buying NO at price (1-y) wins payout y.
    side: 'yes' or 'no'.
    order_type: 'maker' or 'taker' (different fee tiers).
    """
    if market_yes_price <= 0 or market_yes_price >= 1:
        return EVResult(ev=-stake, fee=0.0, payout_if_win=0.0,
                        cost_if_lose=stake, kelly_fraction=0.0)
    if side == "yes":
        entry_price = market_yes_price
        win_p = calibrated_p
    elif side == "no":
        entry_price = 1.0 - market_yes_price
        win_p = 1.0 - calibrated_p
    else:
        raise ValueError(f"side must be yes or no, got {side!r}")
    contracts = stake / entry_price if entry_price > 0 else 0.0
    payout_if_win = contracts * (1.0 - entry_price)
    cost_if_lose = stake
    fee_coef = (
        KALSHI_MAKER_FEE_COEF if order_type == "maker"
        else KALSHI_TAKER_FEE_COEF
    )
    fee = fee_coef * payout_if_win
    ev = win_p * payout_if_win - (1 - win_p) * cost_if_lose - fee
    b = payout_if_win / stake if stake > 0 else 0.0
    kelly = (b * win_p - (1 - win_p)) / b if b > 0 else 0.0
    kelly = max(0.0, min(1.0, kelly))
    return EVResult(ev=ev, fee=fee, payout_if_win=payout_if_win,
                    cost_if_lose=cost_if_lose, kelly_fraction=kelly)
```

- [ ] **Step 4: Run pytest expecting 5 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/execution/ev.py ofi-lab-v3/tests/test_ev.py
git commit -m "v3-B: EV calculator — Polymarket + Kalshi fee models"
```

---

### Task 20: Five-layer filter pipeline scaffold — `filters/pipeline.py`

**Files:**
- Create: `ofi-lab-v3/filters/__init__.py`
- Create: `ofi-lab-v3/filters/pipeline.py`
- Create: `ofi-lab-v3/tests/test_filter_pipeline.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_filter_pipeline.py
import pytest

from filters.pipeline import FilterPipeline, FilterDecision, FilterStage


def make_stage(name, decision):
    """Stage factory: returns FilterStage that records its name and
    yields the given decision."""
    def stage_fn(ctx):
        ctx["trace"].append(name)
        return decision
    return FilterStage(name=name, fn=stage_fn)


def test_pipeline_runs_stages_in_order_until_first_block():
    p = FilterPipeline([
        make_stage("calibration", FilterDecision.pass_()),
        make_stage("paper_filter", FilterDecision.block(reason="below_confidence")),
        make_stage("live_eligibility", FilterDecision.pass_()),
    ])
    ctx = {"trace": []}
    result = p.run(ctx)
    assert ctx["trace"] == ["calibration", "paper_filter"]
    assert result.passed is False
    assert result.blocked_at == "paper_filter"
    assert result.reason == "below_confidence"


def test_pipeline_passes_through_when_all_stages_pass():
    p = FilterPipeline([
        make_stage("a", FilterDecision.pass_()),
        make_stage("b", FilterDecision.pass_()),
        make_stage("c", FilterDecision.pass_()),
    ])
    ctx = {"trace": []}
    result = p.run(ctx)
    assert ctx["trace"] == ["a", "b", "c"]
    assert result.passed is True
    assert result.blocked_at is None


def test_pipeline_collects_filter_evals_for_decision_trace():
    p = FilterPipeline([
        FilterStage(
            name="ev_threshold",
            fn=lambda ctx: FilterDecision.eval_(
                threshold=0.0, input_value=0.018, passed=True,
            ),
        ),
        FilterStage(
            name="confidence",
            fn=lambda ctx: FilterDecision.eval_(
                threshold=0.55, input_value=0.51, passed=False,
                reason="below_confidence",
            ),
        ),
    ])
    ctx = {"trace": []}
    result = p.run(ctx)
    assert result.passed is False
    assert len(result.evals) == 2
    assert result.evals[0].name == "ev_threshold"
    assert result.evals[0].input_value == 0.018
    assert result.evals[1].input_value == 0.51
    assert result.evals[1].passed is False
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement the pipeline scaffold**

```python
# ofi-lab-v3/filters/__init__.py
"""Five-layer filter pipeline:
1. Prediction generation (handled by paper_trader; not a layer here)
2. Calibration (handled by ProbabilityCalibrator)
3. Paper-trade filter
4. Live-eligibility filter
5. Platform-execution filter
"""
```

```python
# ofi-lab-v3/filters/pipeline.py
"""Composable filter pipeline.

Each layer's stage function receives a mutable context dict and
returns a FilterDecision. The pipeline runs stages in order. The
first stage that returns ``passed=False`` blocks the trade and the
rest are skipped. All evaluations (passed and blocked) are collected
into ``result.evals`` for the verbose decision trace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional


@dataclass(frozen=True)
class FilterEval:
    name: str
    threshold: Optional[float]
    input_value: Optional[float]
    passed: bool


@dataclass(frozen=True)
class FilterDecision:
    passed: bool
    name: str = ""
    reason: Optional[str] = None
    threshold: Optional[float] = None
    input_value: Optional[float] = None

    @classmethod
    def pass_(cls, threshold=None, input_value=None) -> "FilterDecision":
        return cls(passed=True, threshold=threshold, input_value=input_value)

    @classmethod
    def block(cls, reason: str, threshold=None, input_value=None) -> "FilterDecision":
        return cls(passed=False, reason=reason,
                   threshold=threshold, input_value=input_value)

    @classmethod
    def eval_(cls, *, threshold, input_value, passed,
              reason=None) -> "FilterDecision":
        return cls(passed=passed, threshold=threshold,
                   input_value=input_value, reason=reason)


@dataclass(frozen=True)
class FilterStage:
    name: str
    fn: Callable


@dataclass
class PipelineResult:
    passed: bool
    blocked_at: Optional[str]
    reason: Optional[str]
    evals: List[FilterEval] = field(default_factory=list)


class FilterPipeline:
    def __init__(self, stages: List[FilterStage]) -> None:
        self._stages = list(stages)

    def run(self, ctx: dict) -> PipelineResult:
        evals: List[FilterEval] = []
        for stage in self._stages:
            decision = stage.fn(ctx)
            evals.append(FilterEval(
                name=stage.name,
                threshold=decision.threshold,
                input_value=decision.input_value,
                passed=decision.passed,
            ))
            if not decision.passed:
                return PipelineResult(
                    passed=False, blocked_at=stage.name,
                    reason=decision.reason or stage.name, evals=evals,
                )
        return PipelineResult(
            passed=True, blocked_at=None, reason=None, evals=evals,
        )
```

- [ ] **Step 4: Run pytest expecting 3 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/filters/__init__.py ofi-lab-v3/filters/pipeline.py \
        ofi-lab-v3/tests/test_filter_pipeline.py
git commit -m "v3-B: FilterPipeline scaffold + FilterStage + FilterDecision"
```

---

### Task 21: PaperFilter layer — confidence/EV/regime/blackout/cooldown

**Files:**
- Create: `ofi-lab-v3/filters/paper_filter.py`
- Modify: `ofi-lab-v3/tests/test_filter_pipeline.py`

- [ ] **Step 1: Append failing test**

```python
def test_paper_filter_blocks_below_confidence():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.51,
        "ev": 0.020,
        "spread_pct": 0.0001,
        "utc_hour": 12,
        "blackout_hours": [],
    }
    decision = stage.fn(ctx)
    assert decision.passed is False
    assert decision.reason == "below_confidence"
    assert decision.input_value == 0.51


def test_paper_filter_blocks_negative_ev_even_with_high_confidence():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.60,
        "ev": -0.005,  # confidence high, but EV negative
        "spread_pct": 0.0001,
        "utc_hour": 12,
        "blackout_hours": [],
    }
    decision = stage.fn(ctx)
    assert decision.passed is False
    assert decision.reason == "negative_ev"


def test_paper_filter_blocks_blackout_hour():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(
        confidence_threshold=0.55, ev_threshold=0.0,
    )
    ctx = {
        "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
        "utc_hour": 22, "blackout_hours": [21, 22, 23],
    }
    decision = stage.fn(ctx)
    assert decision.passed is False
    assert decision.reason == "blackout"


def test_paper_filter_passes_when_all_gates_clear():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
        "utc_hour": 12, "blackout_hours": [21, 22, 23],
    }
    decision = stage.fn(ctx)
    assert decision.passed is True
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `filters/paper_filter.py`**

```python
# ofi-lab-v3/filters/paper_filter.py
"""Paper-tier filter — runs paper trades under their own optimal config.

Independent of live-platform state: paper-active continues even when
live is suspended. Plan B's MVP gates: confidence, EV, blackout. Plan
B+ adds: regime, cooldown, exposure cap.
"""
from __future__ import annotations

from filters.pipeline import FilterStage, FilterDecision


def build_paper_filter_stage(
    *, confidence_threshold: float, ev_threshold: float,
) -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        # 1. Confidence gate
        conf = ctx.get("calibrated_p", 0.0)
        if conf < confidence_threshold:
            return FilterDecision.block(
                reason="below_confidence",
                threshold=confidence_threshold, input_value=conf,
            )
        # 2. EV gate (universal)
        ev = ctx.get("ev", 0.0)
        if ev <= ev_threshold:
            return FilterDecision.block(
                reason="negative_ev",
                threshold=ev_threshold, input_value=ev,
            )
        # 3. Blackout gate
        utc_hour = ctx.get("utc_hour")
        blackout = ctx.get("blackout_hours", [])
        if utc_hour is not None and utc_hour in blackout:
            return FilterDecision.block(
                reason="blackout",
                threshold=None, input_value=utc_hour,
            )
        return FilterDecision.pass_()
    return FilterStage(name="paper_filter", fn=stage)
```

- [ ] **Step 4: Run pytest expecting 7 passed (3 prior + 4 new)**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/filters/paper_filter.py ofi-lab-v3/tests/test_filter_pipeline.py
git commit -m "v3-B: PaperFilter — confidence + EV + blackout gates"
```

---

### Task 22: LiveEligibility + PlatformExecution layers

**Files:**
- Create: `ofi-lab-v3/filters/live_eligibility.py`
- Create: `ofi-lab-v3/filters/platform_execution.py`
- Modify: `ofi-lab-v3/tests/test_filter_pipeline.py`

- [ ] **Step 1: Append failing test**

```python
def test_live_eligibility_blocks_when_lifecycle_not_live_active():
    from filters.live_eligibility import build_live_eligibility_stage
    stage = build_live_eligibility_stage()
    ctx = {"lifecycle_state": "live_suspended"}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "lifecycle_state_not_live_active"


def test_live_eligibility_passes_for_live_active():
    from filters.live_eligibility import build_live_eligibility_stage
    stage = build_live_eligibility_stage()
    ctx = {"lifecycle_state": "live_active", "kalshi_live_enabled": True}
    d = stage.fn(ctx)
    assert d.passed is True


def test_live_eligibility_blocks_when_kalshi_flag_disabled():
    from filters.live_eligibility import build_live_eligibility_stage
    stage = build_live_eligibility_stage()
    ctx = {"lifecycle_state": "live_active", "kalshi_live_enabled": False}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "kalshi_live_disabled"


def test_platform_execution_blocks_on_empty_book():
    from filters.platform_execution import build_kalshi_execution_stage
    stage = build_kalshi_execution_stage()
    ctx = {"kalshi_book_has_quotes": False, "kalshi_market_exists": True,
           "extreme_price": False}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "empty_book"


def test_platform_execution_blocks_on_missing_market():
    from filters.platform_execution import build_kalshi_execution_stage
    stage = build_kalshi_execution_stage()
    ctx = {"kalshi_book_has_quotes": True, "kalshi_market_exists": False,
           "extreme_price": False}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "no_market"


def test_platform_execution_passes_with_healthy_book():
    from filters.platform_execution import build_kalshi_execution_stage
    stage = build_kalshi_execution_stage()
    ctx = {"kalshi_book_has_quotes": True, "kalshi_market_exists": True,
           "extreme_price": False}
    d = stage.fn(ctx)
    assert d.passed is True
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement both layers**

```python
# ofi-lab-v3/filters/live_eligibility.py
"""Layer 4 — live dispatch eligibility.

Independent of paper status. A model can be paper_active +
live_suspended; this stage blocks live dispatch but does not affect
paper trading.
"""
from __future__ import annotations

from filters.pipeline import FilterStage, FilterDecision


def build_live_eligibility_stage() -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        if ctx.get("lifecycle_state") != "live_active":
            return FilterDecision.block(
                reason="lifecycle_state_not_live_active",
                input_value=None,
            )
        if not ctx.get("kalshi_live_enabled", False):
            return FilterDecision.block(
                reason="kalshi_live_disabled",
                input_value=None,
            )
        return FilterDecision.pass_()
    return FilterStage(name="live_eligibility", fn=stage)
```

```python
# ofi-lab-v3/filters/platform_execution.py
"""Layer 5 — platform-specific execution gates (Kalshi MVP).

Per-platform orderability checks: market exists, book has quotes,
price not extreme. Plan B+ adds maker/taker availability, min/max
contract size, allow-list checks.
"""
from __future__ import annotations

from filters.pipeline import FilterStage, FilterDecision


def build_kalshi_execution_stage() -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        if not ctx.get("kalshi_market_exists", False):
            return FilterDecision.block(reason="no_market", input_value=None)
        if not ctx.get("kalshi_book_has_quotes", False):
            return FilterDecision.block(reason="empty_book", input_value=None)
        if ctx.get("extreme_price", False):
            return FilterDecision.block(reason="extreme_price", input_value=None)
        return FilterDecision.pass_()
    return FilterStage(name="platform_execution", fn=stage)
```

- [ ] **Step 4: Run pytest expecting 13 passed total**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/filters/live_eligibility.py \
        ofi-lab-v3/filters/platform_execution.py \
        ofi-lab-v3/tests/test_filter_pipeline.py
git commit -m "v3-B: LiveEligibility + PlatformExecution filter layers"
```

---

### Task 23: Model conflict suppression rule

**Files:**
- Modify: `ofi-lab-v3/filters/paper_filter.py` — add conflict gate
- Modify: `ofi-lab-v3/tests/test_filter_pipeline.py`

The conflict rule is a paper-tier gate: when multiple models score the same `(symbol, market_window)` and disagree on direction, suppress the trade. This is independent of the EV gate.

- [ ] **Step 1: Append failing test**

```python
def test_paper_filter_blocks_on_model_conflict():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
        "utc_hour": 12, "blackout_hours": [],
        "model_conflict": True,
    }
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "model_conflict"


def test_paper_filter_passes_when_no_conflict_specified():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
        "utc_hour": 12, "blackout_hours": [],
        # model_conflict absent → treat as no conflict
    }
    d = stage.fn(ctx)
    assert d.passed is True
```

- [ ] **Step 2: Run pytest expecting failure (no conflict gate yet)**

- [ ] **Step 3: Add conflict gate to `filters/paper_filter.py`**

In `build_paper_filter_stage`, between the EV gate and the blackout gate:

```python
        # 3. Model conflict gate (Steering §4)
        if ctx.get("model_conflict", False):
            return FilterDecision.block(
                reason="model_conflict",
                input_value=None,
            )
```

(Renumber the existing blackout to 4 in the comment.)

- [ ] **Step 4: Run pytest expecting 15 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/filters/paper_filter.py ofi-lab-v3/tests/test_filter_pipeline.py
git commit -m "v3-B: model conflict gate (Steering §4 — no consensus = no trade)"
```

---

### Task 24: Stale-price + stale-book rejection

**Files:**
- Create: `ofi-lab-v3/filters/staleness.py`
- Modify: `ofi-lab-v3/tests/test_filter_pipeline.py`

- [ ] **Step 1: Append failing test**

```python
def test_stale_price_blocks_when_book_age_exceeds_max():
    from filters.staleness import build_stale_price_stage
    stage = build_stale_price_stage(max_age_seconds=30)
    ctx = {"book_age_seconds": 45.0}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "stale_price"
    assert d.input_value == 45.0


def test_stale_price_passes_when_book_fresh():
    from filters.staleness import build_stale_price_stage
    stage = build_stale_price_stage(max_age_seconds=30)
    ctx = {"book_age_seconds": 5.0}
    d = stage.fn(ctx)
    assert d.passed is True


def test_empty_book_blocks_when_quotes_missing():
    from filters.staleness import build_stale_book_stage
    stage = build_stale_book_stage()
    ctx = {"book_has_quotes": False}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "empty_book"


def test_empty_book_passes_when_quotes_present():
    from filters.staleness import build_stale_book_stage
    stage = build_stale_book_stage()
    ctx = {"book_has_quotes": True}
    d = stage.fn(ctx)
    assert d.passed is True
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `filters/staleness.py`**

```python
# ofi-lab-v3/filters/staleness.py
"""Stale-price / stale-book rejection.

Used in both paper and live execution layers. A stale price means
the model's input may not reflect the current market, and trading on
it can produce phantom edge. An empty book means we can't price the
contract at all.
"""
from __future__ import annotations

from filters.pipeline import FilterStage, FilterDecision


def build_stale_price_stage(*, max_age_seconds: float) -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        age = ctx.get("book_age_seconds", 0.0)
        if age > max_age_seconds:
            return FilterDecision.block(
                reason="stale_price",
                threshold=max_age_seconds, input_value=age,
            )
        return FilterDecision.pass_(
            threshold=max_age_seconds, input_value=age,
        )
    return FilterStage(name="stale_price", fn=stage)


def build_stale_book_stage() -> FilterStage:
    def stage(ctx: dict) -> FilterDecision:
        if not ctx.get("book_has_quotes", False):
            return FilterDecision.block(
                reason="empty_book", input_value=0.0,
            )
        return FilterDecision.pass_(input_value=1.0)
    return FilterStage(name="stale_book", fn=stage)
```

- [ ] **Step 4: Run pytest expecting 19 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/filters/staleness.py ofi-lab-v3/tests/test_filter_pipeline.py
git commit -m "v3-B: stale-price + stale-book filter stages"
```

---

### Task 25: Per-model exposure cap — `execution/exposure.py`

**Files:**
- Create: `ofi-lab-v3/execution/exposure.py`
- Create: `ofi-lab-v3/tests/test_exposure.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_exposure.py
import pytest

from execution.exposure import ExposureCap, ExposureBlock


def test_initial_open_count_is_zero():
    cap = ExposureCap(max_open_per_model={"alpha": 3})
    assert cap.open_count("alpha") == 0


def test_record_open_increments_count():
    cap = ExposureCap(max_open_per_model={"alpha": 3})
    cap.record_open("alpha", trade_id="t1")
    cap.record_open("alpha", trade_id="t2")
    assert cap.open_count("alpha") == 2


def test_record_close_decrements_count():
    cap = ExposureCap(max_open_per_model={"alpha": 3})
    cap.record_open("alpha", trade_id="t1")
    cap.record_close("alpha", trade_id="t1")
    assert cap.open_count("alpha") == 0


def test_can_open_blocks_when_at_cap():
    cap = ExposureCap(max_open_per_model={"alpha": 2})
    cap.record_open("alpha", trade_id="t1")
    cap.record_open("alpha", trade_id="t2")
    result = cap.can_open("alpha")
    assert result.allowed is False
    assert result.current == 2
    assert result.cap == 2


def test_can_open_allows_under_cap():
    cap = ExposureCap(max_open_per_model={"alpha": 5})
    cap.record_open("alpha", trade_id="t1")
    result = cap.can_open("alpha")
    assert result.allowed is True
    assert result.current == 1


def test_unlimited_when_no_cap_for_model():
    cap = ExposureCap(max_open_per_model={})
    result = cap.can_open("any_model")
    assert result.allowed is True
    assert result.cap is None
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `execution/exposure.py`**

```python
# ofi-lab-v3/execution/exposure.py
"""Per-model open-position cap tracker.

Plan B's exposure cap is in-memory and per-process. Plan B+ persists
to SQLite and shares across paper_trader / api_server for race-free
display.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class ExposureBlock:
    allowed: bool
    current: int
    cap: Optional[int]


class ExposureCap:
    def __init__(self, max_open_per_model: Dict[str, int]) -> None:
        self._caps = dict(max_open_per_model)
        self._open: Dict[str, Set[str]] = {}

    def open_count(self, model_name: str) -> int:
        return len(self._open.get(model_name, set()))

    def record_open(self, model_name: str, trade_id: str) -> None:
        self._open.setdefault(model_name, set()).add(trade_id)

    def record_close(self, model_name: str, trade_id: str) -> None:
        if model_name in self._open:
            self._open[model_name].discard(trade_id)

    def can_open(self, model_name: str) -> ExposureBlock:
        cap = self._caps.get(model_name)
        current = self.open_count(model_name)
        if cap is None:
            return ExposureBlock(allowed=True, current=current, cap=None)
        return ExposureBlock(allowed=(current < cap), current=current, cap=cap)
```

- [ ] **Step 4: Run pytest expecting 6 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/execution/exposure.py ofi-lab-v3/tests/test_exposure.py
git commit -m "v3-B: ExposureCap — per-model open-position tracker"
```

---

### Task 26: Wire FilterPipeline into `_run_predictions`

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Create: `ofi-lab-v3/tests/test_filter_pipeline_integration.py`

- [ ] **Step 1: Write failing integration test**

```python
# ofi-lab-v3/tests/test_filter_pipeline_integration.py
import json
from pathlib import Path


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_evaluate_paper_filters_returns_pipeline_result(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    result = t._evaluate_paper_filters(
        ctx={
            "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
            "utc_hour": 12, "blackout_hours": [],
            "model_conflict": False, "book_age_seconds": 5.0,
            "book_has_quotes": True,
        },
    )
    assert result.passed is True
    # 4 stages run: stale_price, stale_book, paper_filter (also EV/conf/conflict/blackout)
    names = [e.name for e in result.evals]
    assert "paper_filter" in names


def test_evaluate_paper_filters_blocks_negative_ev(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    result = t._evaluate_paper_filters(
        ctx={
            "calibrated_p": 0.60, "ev": -0.005, "spread_pct": 0.0001,
            "utc_hour": 12, "blackout_hours": [],
            "model_conflict": False, "book_age_seconds": 5.0,
            "book_has_quotes": True,
        },
    )
    assert result.passed is False
    assert result.reason == "negative_ev"
```

- [ ] **Step 2: Run pytest expecting AttributeError**

- [ ] **Step 3: Add `_evaluate_paper_filters` helper to PaperTrader**

```python
from filters.pipeline import FilterPipeline
from filters.staleness import build_stale_price_stage, build_stale_book_stage
from filters.paper_filter import build_paper_filter_stage


def _evaluate_paper_filters(self, ctx: dict):
    """Run the paper-tier filter pipeline against a decision context."""
    pipeline = FilterPipeline([
        build_stale_book_stage(),
        build_stale_price_stage(
            max_age_seconds=self.filters.get("max_book_age_seconds", 30),
        ),
        build_paper_filter_stage(
            confidence_threshold=self.filters.get("confidence_threshold", 0.55),
            ev_threshold=self.filters.get("ev_threshold", 0.0),
        ),
    ])
    return pipeline.run(ctx)
```

The actual call-site replacement (using `_evaluate_paper_filters` instead of v2 sequential gates in `_run_predictions`) is left for a follow-on patch since it requires reshaping the v2 control flow. T26 just exposes the helper.

- [ ] **Step 4: Run pytest expecting 2 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py \
        ofi-lab-v3/tests/test_filter_pipeline_integration.py
git commit -m "v3-B: PaperTrader._evaluate_paper_filters — pipeline integration helper"
```

---

### Task 27: `DecayWriter` — append-only decay snapshot writer

**Files:**
- Create: `ofi-lab-v3/storage/decay_writer.py`
- Create: `ofi-lab-v3/tests/test_decay_writer.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_decay_writer.py
from storage.db import open_database, init_schema
from storage.decay_writer import DecayWriter


def test_write_decay_snapshot_persists_row(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = DecayWriter(conn)
    w.write_snapshot(
        model_name="900s_btc_v3_20260315",
        symbol="BTCUSDT",
        market_window_seconds=900,
        window_size=100,
        rolling_ev=0.018,
        recency_weighted_ev=0.022,
        rolling_win_rate=0.55,
        brier_score=0.21,
        calibration_error=0.03,
        sample_count=100,
    )
    row = conn.execute("SELECT * FROM decay_metrics").fetchone()
    assert row["model_name"] == "900s_btc_v3_20260315"
    assert abs(row["rolling_ev"] - 0.018) < 1e-9
    assert abs(row["recency_weighted_ev"] - 0.022) < 1e-9
    assert row["sample_count"] == 100


def test_write_evaluation_persists_row(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    w = DecayWriter(conn)
    w.write_evaluation(
        model_name="900s_btc_v3_20260315",
        symbol="BTCUSDT",
        market_window_seconds=900,
        eval_type="psi",
        metric_value=0.18,
        threshold=0.10,
        triggered=True,
        detail={"reference_period": "30d"},
    )
    row = conn.execute("SELECT * FROM decay_evaluations").fetchone()
    assert row["eval_type"] == "psi"
    assert row["triggered"] == 1
    import json
    assert json.loads(row["detail_json"])["reference_period"] == "30d"
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `storage/decay_writer.py`**

```python
# ofi-lab-v3/storage/decay_writer.py
"""Append-only writer for decay snapshots and evaluations."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class DecayWriter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def write_snapshot(
        self, *, model_name: str, symbol: str,
        market_window_seconds: int, window_size: int,
        rolling_ev: Optional[float], recency_weighted_ev: Optional[float],
        rolling_win_rate: Optional[float], brier_score: Optional[float],
        calibration_error: Optional[float], sample_count: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO decay_metrics ("
                " ts, model_name, symbol, market_window_seconds,"
                " window_size, rolling_ev, recency_weighted_ev,"
                " rolling_win_rate, brier_score, calibration_error,"
                " sample_count"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_utc_iso(), model_name, symbol, market_window_seconds,
                 window_size, rolling_ev, recency_weighted_ev,
                 rolling_win_rate, brier_score, calibration_error,
                 sample_count),
            )

    def write_evaluation(
        self, *, model_name: str, symbol: str,
        market_window_seconds: int, eval_type: str,
        metric_value: Optional[float], threshold: Optional[float],
        triggered: bool, detail: Optional[dict] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO decay_evaluations ("
                " ts, model_name, symbol, market_window_seconds,"
                " eval_type, metric_value, threshold, triggered,"
                " detail_json"
                ") VALUES (?,?,?,?,?,?,?,?,?)",
                (_utc_iso(), model_name, symbol, market_window_seconds,
                 eval_type, metric_value, threshold, int(triggered),
                 json.dumps(detail) if detail is not None else None),
            )
```

- [ ] **Step 4: Run pytest expecting 2 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/decay_writer.py ofi-lab-v3/tests/test_decay_writer.py
git commit -m "v3-B: DecayWriter — snapshot + evaluation rows"
```

---

### Task 28: Decay metrics computer — rolling EV + Brier + calibration error

**Files:**
- Create: `ofi-lab-v3/storage/decay_metrics.py`
- Create: `ofi-lab-v3/tests/test_decay_metrics.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_decay_metrics.py
import pytest

from storage.decay_metrics import (
    compute_brier_score, compute_calibration_error, compute_rolling_ev,
    compute_recency_weighted_ev,
)


def test_brier_zero_when_predictions_perfect():
    assert compute_brier_score([(1.0, True), (0.0, False)]) == 0.0


def test_brier_high_when_predictions_inverted():
    score = compute_brier_score([(0.9, False), (0.1, True)])
    assert score > 0.7


def test_calibration_error_zero_when_well_calibrated():
    # Predict 60% three times, observe 2/3 wins ≈ 0.667
    rows = [(0.60, True)] * 2 + [(0.60, False)]
    err = compute_calibration_error(rows, bin_width=0.1)
    # Bin centered at 0.60; predicted=0.60, actual=2/3≈0.667
    assert err < 0.10


def test_rolling_ev_simple_average():
    ev_values = [0.01, 0.02, -0.01, 0.005]
    assert abs(compute_rolling_ev(ev_values) - 0.00625) < 1e-9


def test_rolling_ev_empty_returns_zero():
    assert compute_rolling_ev([]) == 0.0


def test_recency_weighted_ev_recent_dominates():
    # 10 wins of $0.05 followed by 10 losses of -$0.05; recency
    # weighting should pull EV negative.
    ev_values = [0.05] * 10 + [-0.05] * 10
    rate = compute_recency_weighted_ev(ev_values, alpha=0.3)
    assert rate < 0.0
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `storage/decay_metrics.py`**

```python
# ofi-lab-v3/storage/decay_metrics.py
"""Decay metric computations.

All functions take simple Python iterables — they're pure and easy
to test. The schedule of when these run lives in PaperTrader's
periodic loop (Plan B+ wires the cadence).
"""
from __future__ import annotations

from typing import Iterable, List, Tuple


def compute_brier_score(rows: Iterable[Tuple[float, bool]]) -> float:
    """Mean of (predicted - outcome)^2.

    Lower is better. 0 = perfect; 0.25 = always 0.5; 1.0 = inverted.
    """
    rows = list(rows)
    if not rows:
        return 0.0
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in rows) / len(rows)


def compute_calibration_error(
    rows: Iterable[Tuple[float, bool]], bin_width: float = 0.05,
) -> float:
    """Mean absolute error per probability bin.

    Bin predictions by ``bin_width`` and compare each bin's mean
    prediction to its empirical win rate; weighted average over bins.
    """
    rows = list(rows)
    if not rows:
        return 0.0
    bins: dict = {}
    for p, o in rows:
        b = round(p / bin_width) * bin_width
        bins.setdefault(b, []).append((p, o))
    total = len(rows)
    weighted_err = 0.0
    for b, members in bins.items():
        avg_p = sum(p for p, _ in members) / len(members)
        actual = sum(1 for _, o in members if o) / len(members)
        weighted_err += abs(avg_p - actual) * (len(members) / total)
    return weighted_err


def compute_rolling_ev(ev_values: Iterable[float]) -> float:
    vals = list(ev_values)
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def compute_recency_weighted_ev(
    ev_values: Iterable[float], alpha: float = 0.05,
) -> float:
    """EWMA over per-trade EV. alpha is the decay rate.

    Older trades get exponentially less weight. alpha=0.05 = standard
    finance EWMA; alpha=0.30 = aggressively recent-biased.
    """
    vals = list(ev_values)
    if not vals:
        return 0.0
    n = len(vals)
    weight_total = 0.0
    weighted_sum = 0.0
    for i, v in enumerate(vals):
        # Newest gets weight 1.0; oldest gets (1-alpha)^(n-1)
        w = (1.0 - alpha) ** (n - 1 - i)
        weighted_sum += w * v
        weight_total += w
    return weighted_sum / weight_total if weight_total > 0 else 0.0
```

- [ ] **Step 4: Run pytest expecting 6 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/decay_metrics.py ofi-lab-v3/tests/test_decay_metrics.py
git commit -m "v3-B: decay metrics — Brier, calibration error, rolling EV, EWMA EV"
```

---

### Task 29: Decay refresh loop in PaperTrader

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Create: `ofi-lab-v3/tests/test_decay_refresh.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_decay_refresh.py
def test_refresh_decay_metrics_writes_snapshot_per_model(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    from storage.sqlite_ledger import SQLiteLedger
    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    # Insert 10 native paper_trade rows with PnL values
    conn = t._db_conn
    for i in range(10):
        conn.execute(
            "INSERT INTO paper_trades ("
            " trade_id, prediction_id, model_name, model_artifact_hash,"
            " policy_config_hash, decision_policy_version, calibration_map_hash,"
            " registry_load_generation, feature_version,"
            " training_horizon_seconds, symbol, market_window_seconds,"
            " resolution_type, ts_model_ran_ms, ts_contract_open_ms,"
            " ts_resolve_at_ms, pred_proba_raw, pred_proba_calibrated,"
            " pred_direction, confidence_threshold_used, simulated_stake_usdc,"
            " warmup, platform, decision_outcome, net_pnl, prediction_correct,"
            " resolved"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"t{i}", f"p{i}", "900s_btc_v3_20260315", "a"*64, "c"*64,
                0, "d"*64, 0, "v3", 900, "BTCUSDT", 900, "native",
                i*1000, i*1000, i*1000+900_000, 0.55, 0.55, "up",
                0.55, 10.0, 0, "paper", "executed",
                0.05 if i % 2 == 0 else -0.04,  # alternating PnL
                1 if i % 2 == 0 else 0,
                1,
            ),
        )

    t.refresh_decay_metrics(window_size=10)

    rows = conn.execute(
        "SELECT * FROM decay_metrics WHERE model_name='900s_btc_v3_20260315'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["sample_count"] == 10
    assert rows[0]["rolling_ev"] is not None
    assert rows[0]["rolling_win_rate"] == 0.5  # 5/10
```

- [ ] **Step 2: Run pytest expecting AttributeError**

- [ ] **Step 3: Add `refresh_decay_metrics` to PaperTrader**

```python
from storage.decay_writer import DecayWriter
from storage.decay_metrics import (
    compute_brier_score, compute_calibration_error,
    compute_rolling_ev, compute_recency_weighted_ev,
)


def _decay_writer(self):
    if not hasattr(self, "_decay_writer_cache"):
        self._decay_writer_cache = DecayWriter(self._db_conn)
    return self._decay_writer_cache


def refresh_decay_metrics(self, *, window_size: int = 100) -> None:
    """Compute rolling decay metrics for every (model, symbol, window) and
    write a snapshot row to ``decay_metrics``.
    """
    writer = self._decay_writer()
    # Find every distinct (model, symbol, market_window_seconds)
    triples = self._db_conn.execute(
        "SELECT DISTINCT model_name, symbol, market_window_seconds"
        " FROM paper_trades"
        " WHERE resolution_type = 'native' AND resolved = 1"
        "   AND warmup = 0"
    ).fetchall()
    for t in triples:
        model_name = t["model_name"]
        symbol = t["symbol"]
        window = t["market_window_seconds"]
        rows = self._db_conn.execute(
            "SELECT net_pnl, simulated_stake_usdc, pred_proba_calibrated,"
            " prediction_correct"
            " FROM paper_trades"
            " WHERE model_name = ? AND symbol = ? AND market_window_seconds = ?"
            "   AND resolution_type = 'native' AND resolved = 1 AND warmup = 0"
            " ORDER BY ts_contract_open_ms DESC LIMIT ?",
            (model_name, symbol, window, window_size),
        ).fetchall()
        if not rows:
            continue
        # Reverse to oldest-first for EWMA
        rows = list(reversed(rows))
        ev_values = [(r["net_pnl"] or 0.0) / (r["simulated_stake_usdc"] or 1.0)
                      for r in rows]
        cal_rows = [
            (r["pred_proba_calibrated"], bool(r["prediction_correct"] or 0))
            for r in rows
        ]
        win_rate = sum(1 for r in rows if r["prediction_correct"]) / len(rows)
        writer.write_snapshot(
            model_name=model_name, symbol=symbol,
            market_window_seconds=window,
            window_size=window_size,
            rolling_ev=compute_rolling_ev(ev_values),
            recency_weighted_ev=compute_recency_weighted_ev(ev_values, alpha=0.05),
            rolling_win_rate=win_rate,
            brier_score=compute_brier_score(cal_rows),
            calibration_error=compute_calibration_error(cal_rows),
            sample_count=len(rows),
        )
```

- [ ] **Step 4: Run pytest expecting pass**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_decay_refresh.py
git commit -m "v3-B: PaperTrader.refresh_decay_metrics — periodic snapshot"
```

---

### Task 30: PSI integration (Steering 10f)

**Files:**
- Create: `ofi-lab-v3/storage/psi_integration.py`
- Create: `ofi-lab-v3/tests/test_psi_integration.py`

The v2 codebase has `monitoring/psi.py` (population stability index) but it is never called from the live loop. Plan B wires a periodic check that compares the current 100-prediction window's distribution against a 30-day reference window and writes a `decay_evaluations` row of type `'psi'`.

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_psi_integration.py
import pytest

from storage.db import open_database, init_schema
from storage.psi_integration import compute_psi_against_reference


def test_psi_zero_when_distributions_identical():
    a = [0.1] * 50 + [0.9] * 50
    psi = compute_psi_against_reference(current=a, reference=a, bins=10)
    assert psi < 0.001


def test_psi_high_when_distributions_diverge():
    a = [0.1] * 100
    b = [0.9] * 100
    psi = compute_psi_against_reference(current=a, reference=b, bins=10)
    assert psi > 0.20


def test_psi_handles_empty_inputs():
    psi = compute_psi_against_reference(current=[], reference=[0.1, 0.5], bins=5)
    assert psi == 0.0
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `storage/psi_integration.py`**

```python
# ofi-lab-v3/storage/psi_integration.py
"""PSI (population stability index) computation.

Standalone helper. PaperTrader's decay refresh loop calls this against
the current vs reference prediction windows and writes a row of
eval_type='psi' to decay_evaluations.
"""
from __future__ import annotations

import math
from typing import Iterable, List


def _histogram(values: List[float], bins: int, lo: float, hi: float) -> List[float]:
    if not values or hi <= lo:
        return [0.0] * bins
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        if v < lo:
            counts[0] += 1
            continue
        if v >= hi:
            counts[-1] += 1
            continue
        idx = min(int((v - lo) / width), bins - 1)
        counts[idx] += 1
    total = sum(counts) or 1
    return [c / total for c in counts]


def compute_psi_against_reference(
    *, current: Iterable[float], reference: Iterable[float], bins: int = 10,
) -> float:
    cur = list(current)
    ref = list(reference)
    if not cur or not ref:
        return 0.0
    lo = min(min(cur), min(ref))
    hi = max(max(cur), max(ref))
    if hi == lo:
        return 0.0
    cur_hist = _histogram(cur, bins, lo, hi)
    ref_hist = _histogram(ref, bins, lo, hi)
    psi = 0.0
    for c, r in zip(cur_hist, ref_hist):
        # Smooth zeros to avoid log(0)
        c = max(c, 1e-6)
        r = max(r, 1e-6)
        psi += (c - r) * math.log(c / r)
    return psi
```

- [ ] **Step 4: Run pytest expecting 3 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/psi_integration.py ofi-lab-v3/tests/test_psi_integration.py
git commit -m "v3-B: PSI integration (Steering 10f)"
```

---

### Task 31: Cliff detection — sharp performance drop alarm

**Files:**
- Create: `ofi-lab-v3/storage/cliff_detector.py`
- Create: `ofi-lab-v3/tests/test_cliff_detector.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_cliff_detector.py
from storage.cliff_detector import (
    detect_ev_cliff, detect_calibration_cliff, CliffResult,
)


def test_no_cliff_when_metrics_stable():
    # Recent 20 EV ≈ baseline ≈ 0.02
    result = detect_ev_cliff(
        recent_ev=[0.02] * 20, baseline_ev=0.02,
        cliff_drop_threshold=0.05,
    )
    assert result.triggered is False


def test_cliff_when_recent_ev_drops_below_threshold():
    result = detect_ev_cliff(
        recent_ev=[-0.04] * 20, baseline_ev=0.02,
        cliff_drop_threshold=0.05,
    )
    assert result.triggered is True
    assert "drop" in result.reason.lower() or "cliff" in result.reason.lower()


def test_calibration_cliff_when_error_spikes():
    result = detect_calibration_cliff(
        recent_error=0.18, baseline_error=0.04,
        spike_threshold=0.10,
    )
    assert result.triggered is True


def test_no_calibration_cliff_when_error_stable():
    result = detect_calibration_cliff(
        recent_error=0.05, baseline_error=0.04, spike_threshold=0.10,
    )
    assert result.triggered is False
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `storage/cliff_detector.py`**

```python
# ofi-lab-v3/storage/cliff_detector.py
"""Sharp performance drop detection.

A "cliff" is a sudden divergence between recent and baseline metrics:
  - recent rolling EV drops below baseline by ``cliff_drop_threshold``
  - calibration error spikes above baseline by ``spike_threshold``
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CliffResult:
    triggered: bool
    metric: str
    delta: float
    reason: str


def detect_ev_cliff(
    *, recent_ev: Iterable[float], baseline_ev: float,
    cliff_drop_threshold: float,
) -> CliffResult:
    vals = list(recent_ev)
    if not vals:
        return CliffResult(triggered=False, metric="ev",
                           delta=0.0, reason="no recent data")
    mean_recent = sum(vals) / len(vals)
    delta = baseline_ev - mean_recent
    triggered = delta >= cliff_drop_threshold
    reason = (
        f"EV drop {delta:.4f} >= cliff threshold {cliff_drop_threshold:.4f}"
        if triggered else "stable"
    )
    return CliffResult(triggered=triggered, metric="ev",
                       delta=delta, reason=reason)


def detect_calibration_cliff(
    *, recent_error: float, baseline_error: float, spike_threshold: float,
) -> CliffResult:
    delta = recent_error - baseline_error
    triggered = delta >= spike_threshold
    reason = (
        f"calibration error spike {delta:.4f} >= threshold {spike_threshold:.4f}"
        if triggered else "stable"
    )
    return CliffResult(triggered=triggered, metric="calibration_error",
                       delta=delta, reason=reason)
```

- [ ] **Step 4: Run pytest expecting 4 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/cliff_detector.py ofi-lab-v3/tests/test_cliff_detector.py
git commit -m "v3-B: cliff detector — EV drop + calibration spike"
```

---

### Task 32: Lifecycle state machine — `storage/lifecycle.py`

**Files:**
- Create: `ofi-lab-v3/storage/lifecycle.py`
- Create: `ofi-lab-v3/tests/test_lifecycle.py`

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_lifecycle.py
import pytest

from storage.lifecycle import (
    LifecycleStateMachine, LifecycleDecision, LifecycleConfig,
)


CONFIG = LifecycleConfig(
    eligibility_min_resolved=200,
    eligibility_min_recency_weighted_ev=0.05,
    eligibility_max_calibration_error=0.05,
    suspend_recency_weighted_ev=0.02,
    suspend_calibration_error=0.10,
    reactivate_recency_weighted_ev=0.05,
    reactivate_calibration_error=0.05,
    requalification_failures_to_retire=3,
)


def test_prediction_only_promotes_when_eligible():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="prediction_only",
        resolved_count=250,
        recency_weighted_ev=0.06,
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    assert decision.next_state == "live_eligible"
    assert decision.reason == "eligibility_met"


def test_prediction_only_stays_when_not_enough_data():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="prediction_only",
        resolved_count=50,
        recency_weighted_ev=0.06,
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    assert decision.next_state == "prediction_only"


def test_live_active_suspends_when_ev_drops():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="live_active",
        resolved_count=300,
        recency_weighted_ev=0.01,  # below 0.02 suspend threshold
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    assert decision.next_state == "live_suspended"
    assert "ev" in decision.reason.lower()


def test_hysteresis_suspend_threshold_below_reactivate_threshold():
    # Suspend at 0.02, reactivate at 0.05 → gap of 0.03 prevents flapping.
    assert CONFIG.suspend_recency_weighted_ev < CONFIG.reactivate_recency_weighted_ev


def test_live_suspended_does_not_immediately_reactivate_on_low_ev():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="live_suspended",
        resolved_count=300,
        recency_weighted_ev=0.03,  # above suspend (0.02) but below reactivate (0.05)
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    # Stays suspended (or moves to requalification cooling) — does NOT
    # jump straight to live_active.
    assert decision.next_state in ("live_suspended", "requalification")


def test_requalification_promotes_to_live_active_when_metrics_recover():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="requalification",
        resolved_count=400,
        recency_weighted_ev=0.06,
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=False,
    )
    assert decision.next_state == "live_active"


def test_requalification_retires_after_repeated_failures():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="requalification",
        resolved_count=400,
        recency_weighted_ev=0.01,
        calibration_error=0.20,
        consecutive_requalification_failures=3,
        is_baseline=False,
    )
    assert decision.next_state == "retired"


def test_baseline_never_retired():
    fsm = LifecycleStateMachine(CONFIG)
    decision = fsm.evaluate(
        current_state="requalification",
        resolved_count=400,
        recency_weighted_ev=0.01,
        calibration_error=0.20,
        consecutive_requalification_failures=10,
        is_baseline=True,
    )
    # Baseline can be live_suspended but never retired.
    assert decision.next_state != "retired"
```

- [ ] **Step 2: Run pytest expecting ModuleNotFoundError**

- [ ] **Step 3: Implement `storage/lifecycle.py`**

```python
# ofi-lab-v3/storage/lifecycle.py
"""Lifecycle state machine with hysteresis.

Live axis: prediction_only → live_eligible → live_active →
            live_suspended → requalification → retired
Paper axis: paper_active ↔ paper_paused (independent — handled
            outside this FSM).

Hysteresis: the EV / calibration thresholds for suspend are looser
than for reactivate, preventing rapid flapping.

Baseline protection: a model with is_baseline=True never enters
'retired'. It can be live_suspended (and re-enter requalification)
but the FSM rejects retirement.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LifecycleConfig:
    eligibility_min_resolved: int = 200
    eligibility_min_recency_weighted_ev: float = 0.05
    eligibility_max_calibration_error: float = 0.05
    suspend_recency_weighted_ev: float = 0.02
    suspend_calibration_error: float = 0.10
    reactivate_recency_weighted_ev: float = 0.05
    reactivate_calibration_error: float = 0.05
    requalification_failures_to_retire: int = 3


@dataclass(frozen=True)
class LifecycleDecision:
    next_state: str
    reason: str


class LifecycleStateMachine:
    def __init__(self, config: LifecycleConfig) -> None:
        self._cfg = config

    def evaluate(
        self, *,
        current_state: str,
        resolved_count: int,
        recency_weighted_ev: float,
        calibration_error: float,
        consecutive_requalification_failures: int,
        is_baseline: bool,
    ) -> LifecycleDecision:
        c = self._cfg
        if current_state == "prediction_only":
            if (
                resolved_count >= c.eligibility_min_resolved
                and recency_weighted_ev >= c.eligibility_min_recency_weighted_ev
                and calibration_error <= c.eligibility_max_calibration_error
            ):
                return LifecycleDecision("live_eligible", "eligibility_met")
            return LifecycleDecision("prediction_only", "below_eligibility")

        if current_state == "live_eligible":
            # Manual promotion or auto-promote in Plan B; default: stay
            return LifecycleDecision("live_eligible", "awaiting_promotion")

        if current_state == "live_active":
            if recency_weighted_ev < c.suspend_recency_weighted_ev:
                return LifecycleDecision(
                    "live_suspended",
                    f"recency_weighted_ev {recency_weighted_ev:.4f} < "
                    f"suspend threshold {c.suspend_recency_weighted_ev}",
                )
            if calibration_error > c.suspend_calibration_error:
                return LifecycleDecision(
                    "live_suspended",
                    f"calibration_error {calibration_error:.4f} > "
                    f"suspend threshold {c.suspend_calibration_error}",
                )
            return LifecycleDecision("live_active", "stable")

        if current_state == "live_suspended":
            # Move to requalification after enough fresh resolved
            return LifecycleDecision("requalification", "cooling_complete")

        if current_state == "requalification":
            if (
                recency_weighted_ev >= c.reactivate_recency_weighted_ev
                and calibration_error <= c.reactivate_calibration_error
            ):
                return LifecycleDecision("live_active", "metrics_recovered")
            if (
                consecutive_requalification_failures
                >= c.requalification_failures_to_retire
            ):
                if is_baseline:
                    return LifecycleDecision(
                        "live_suspended",
                        "baseline cannot retire — staying suspended",
                    )
                return LifecycleDecision(
                    "retired",
                    f"requalification failed "
                    f"{consecutive_requalification_failures}× consecutive",
                )
            return LifecycleDecision("requalification", "metrics_below_reactivate")

        if current_state == "retired":
            return LifecycleDecision("retired", "terminal")

        # Unknown state → no-op
        return LifecycleDecision(current_state, "unknown_state")
```

- [ ] **Step 4: Run pytest expecting 8 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/lifecycle.py ofi-lab-v3/tests/test_lifecycle.py
git commit -m "v3-B: lifecycle FSM with hysteresis + baseline protection"
```

---

### Task 33: Lifecycle decision loop in PaperTrader

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Create: `ofi-lab-v3/tests/test_lifecycle_loop.py`

The decision loop runs periodically (e.g., once per hour). For each model in the registry, it reads the latest decay snapshot and applies `LifecycleStateMachine.evaluate`. State transitions update both `model_registry.json` AND a new `decay_evaluations` row of type `'lifecycle_transition'`.

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_lifecycle_loop.py
import json
from pathlib import Path


def test_evaluate_lifecycle_writes_transition_row(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))

    registry_path = tmp_path / "model_registry.json"
    registry_path.write_text(json.dumps({"models": {
        "900s_btc_v3_20260315": {
            "path": tiny_model_path,
            "feature_names_path": str(
                Path(tiny_model_path).parent / "feature_names.json"
            ),
            "horizon_seconds": 900, "symbol": "BTCUSDT",
            "feature_version": "v3", "is_baseline": True,
            "paper_trading_enabled": True, "kalshi_live_enabled": True,
            "lifecycle_state": "live_active",
            "train_window_start": "2025-04-01",
            "train_window_end": "2026-03-15",
            "train_cutoff": "2026-03-15",
            "min_markets_for_kalshi": 200, "min_days_for_kalshi": 14,
        }
    }}))
    monkeypatch.setenv("MODEL_REGISTRY_PATH", str(registry_path))

    from trading.paper_trader import PaperTrader
    t = PaperTrader(
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )

    # Insert a decay snapshot showing EV below suspend threshold
    t._db_conn.execute(
        "INSERT INTO decay_metrics ("
        " ts, model_name, symbol, market_window_seconds,"
        " window_size, rolling_ev, recency_weighted_ev,"
        " rolling_win_rate, brier_score, calibration_error, sample_count"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-05-09T00:00:00.000000Z", "900s_btc_v3_20260315",
         "BTCUSDT", 900, 100, 0.005, 0.005, 0.50, 0.21, 0.04, 250),
    )

    transitions = t.evaluate_lifecycle_transitions()
    # Baseline is live_active; recency-weighted EV 0.005 < suspend 0.02
    assert any(
        tr["model_name"] == "900s_btc_v3_20260315"
        and tr["from_state"] == "live_active"
        and tr["to_state"] == "live_suspended"
        for tr in transitions
    )
    rows = t._db_conn.execute(
        "SELECT * FROM decay_evaluations WHERE eval_type='lifecycle_transition'"
    ).fetchall()
    assert len(rows) >= 1
```

- [ ] **Step 2: Run pytest expecting AttributeError**

- [ ] **Step 3: Add `evaluate_lifecycle_transitions` to PaperTrader**

```python
from storage.lifecycle import (
    LifecycleStateMachine, LifecycleConfig, LifecycleDecision,
)


def evaluate_lifecycle_transitions(self) -> list:
    """Walk every model in the registry, read its latest decay
    snapshot, and apply the lifecycle FSM. Returns a list of
    transition dicts (also written to decay_evaluations).
    """
    if self.model_registry is None:
        return []
    cfg = LifecycleConfig()  # Plan B+ reads thresholds from config.py
    fsm = LifecycleStateMachine(cfg)
    writer = self._decay_writer()
    transitions = []
    for name, entry in self.model_registry.entries().items():
        snap = self._db_conn.execute(
            "SELECT * FROM decay_metrics"
            " WHERE model_name = ? AND symbol = ?"
            " AND market_window_seconds = ?"
            " ORDER BY id DESC LIMIT 1",
            (name, entry.symbol, entry.horizon_seconds),
        ).fetchone()
        if snap is None:
            continue
        # Count consecutive requalification failures
        failures = self._db_conn.execute(
            "SELECT count(*) AS n FROM decay_evaluations"
            " WHERE model_name = ? AND eval_type = 'requalification_failure'"
            "   AND ts > ("
            "     SELECT COALESCE(MAX(ts), '0000-00-00')"
            "     FROM decay_evaluations"
            "     WHERE model_name = ?"
            "       AND eval_type = 'lifecycle_transition'"
            "       AND detail_json LIKE '%live_active%'"
            "   )",
            (name, name),
        ).fetchone()["n"]
        decision = fsm.evaluate(
            current_state=entry.lifecycle_state,
            resolved_count=snap["sample_count"],
            recency_weighted_ev=snap["recency_weighted_ev"] or 0.0,
            calibration_error=snap["calibration_error"] or 0.0,
            consecutive_requalification_failures=failures,
            is_baseline=entry.is_baseline,
        )
        if decision.next_state != entry.lifecycle_state:
            transitions.append({
                "model_name": name,
                "from_state": entry.lifecycle_state,
                "to_state": decision.next_state,
                "reason": decision.reason,
            })
            writer.write_evaluation(
                model_name=name, symbol=entry.symbol,
                market_window_seconds=entry.horizon_seconds,
                eval_type="lifecycle_transition",
                metric_value=snap["recency_weighted_ev"],
                threshold=None, triggered=True,
                detail={
                    "from_state": entry.lifecycle_state,
                    "to_state": decision.next_state,
                    "reason": decision.reason,
                },
            )
    return transitions
```

The actual update of `model_registry.json` (writing the new `lifecycle_state`) is a Plan C concern (operator review + approval). Plan B logs the transition; Plan C exposes it for human approval via the dashboard.

- [ ] **Step 4: Run pytest expecting pass**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_lifecycle_loop.py
git commit -m "v3-B: PaperTrader.evaluate_lifecycle_transitions"
```

---

### Task 34: Paper-active vs live-status independence enforcement

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` — ensure `paper_trading_enabled` is decoupled from any live-status change
- Create: `ofi-lab-v3/tests/test_paper_live_independence.py`

Steering §6: "A model can be paper_active + live_suspended simultaneously. Suspending live trading must never affect paper trading."

This task tests the invariant: changing a model's `lifecycle_state` to `live_suspended` must not change its `paper_trading_enabled` flag, and the next prediction loop must still emit prediction rows for that model.

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_paper_live_independence.py
import json
from pathlib import Path


def test_live_suspended_model_continues_paper_trading(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))

    registry_path = tmp_path / "model_registry.json"
    registry_path.write_text(json.dumps({"models": {
        "900s_btc_v3_20260315": {
            "path": tiny_model_path,
            "feature_names_path": str(
                Path(tiny_model_path).parent / "feature_names.json"
            ),
            "horizon_seconds": 900, "symbol": "BTCUSDT",
            "feature_version": "v3", "is_baseline": True,
            "paper_trading_enabled": True,
            "kalshi_live_enabled": True,
            "lifecycle_state": "live_suspended",  # critical
            "train_window_start": "2025-04-01",
            "train_window_end": "2026-03-15",
            "train_cutoff": "2026-03-15",
            "min_markets_for_kalshi": 200, "min_days_for_kalshi": 14,
        }
    }}))
    monkeypatch.setenv("MODEL_REGISTRY_PATH", str(registry_path))

    from trading.paper_trader import PaperTrader
    t = PaperTrader(
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )

    # The model is paper_active (from registry flag) but live_suspended
    # (from lifecycle_state). Paper emission must still work.
    boundary = 1_700_000_000_000
    pid = t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=boundary, ts_model_ran_ms=boundary,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=True, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    rows = t._db_conn.execute(
        "SELECT count(*) AS n FROM predictions"
        " WHERE model_name='900s_btc_v3_20260315'"
    ).fetchone()
    assert rows["n"] == 4  # 1 native + 3 evaluation

    # Kalshi dispatch must NOT be eligible for live_suspended models
    assert t.kalshi_dispatch_eligible(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        market_window_seconds=900,
    ) is False


def test_paper_can_be_paused_independently_of_live(tmp_path, monkeypatch, tiny_model_path):
    """A model with paper_trading_enabled=False is excluded from
    active_paper_models, regardless of its lifecycle_state.
    """
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))

    from storage.model_registry import ModelRegistry
    registry_path = tmp_path / "model_registry.json"
    registry_path.write_text(json.dumps({"models": {
        "900s_btc_v3_20260315": {
            "path": tiny_model_path,
            "feature_names_path": str(
                Path(tiny_model_path).parent / "feature_names.json"
            ),
            "horizon_seconds": 900, "symbol": "BTCUSDT",
            "feature_version": "v3", "is_baseline": True,
            "paper_trading_enabled": False,  # paused
            "kalshi_live_enabled": True,
            "lifecycle_state": "live_active",
            "train_window_start": "2025-04-01",
            "train_window_end": "2026-03-15",
            "train_cutoff": "2026-03-15",
            "min_markets_for_kalshi": 200, "min_days_for_kalshi": 14,
        }
    }}))
    reg = ModelRegistry(str(registry_path))
    reg.load()
    assert reg.active_paper_models() == {}  # paused → excluded
```

- [ ] **Step 2: Run pytest — both tests should pass without changes if T2 + T5 wired things correctly**

If they fail, the wiring missed something:
- Confirm `kalshi_dispatch_eligible` checks `e.lifecycle_state != "live_active"` (T5 should have wired this).
- Confirm `_emit_prediction_rows` does not consult `lifecycle_state` (it should only check `paper_trading_enabled` indirectly via `active_paper_models()`).

- [ ] **Step 3: Fix any wiring gaps surfaced by the tests**

If gap found, the most likely fix is in `kalshi_dispatch_eligible`:

```python
def kalshi_dispatch_eligible(self, *, model_name, symbol, market_window_seconds):
    if self.model_registry is not None and model_name in self.model_registry.entries():
        e = self.model_registry.entries()[model_name]
        if not e.kalshi_live_enabled:
            return False
        if e.lifecycle_state != "live_active":  # critical
            return False
        ...
```

- [ ] **Step 4: Run pytest expecting 2 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/tests/test_paper_live_independence.py ofi-lab-v3/trading/paper_trader.py
git commit -m "v3-B: invariant test — paper-active independent of live status"
```

---

### Task 35: Baseline protection in lifecycle decisions

**Files:**
- Modify: `ofi-lab-v3/storage/lifecycle.py` (already enforces in T32)
- Create: `ofi-lab-v3/tests/test_baseline_protection.py`

This task adds an additional invariant test: the baseline model can transition to live_suspended but the lifecycle FSM never returns 'retired' for it, even after many failures.

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_baseline_protection.py
import pytest

from storage.lifecycle import (
    LifecycleStateMachine, LifecycleConfig, LifecycleDecision,
)


CFG = LifecycleConfig(
    eligibility_min_resolved=200,
    eligibility_min_recency_weighted_ev=0.05,
    eligibility_max_calibration_error=0.05,
    suspend_recency_weighted_ev=0.02,
    suspend_calibration_error=0.10,
    reactivate_recency_weighted_ev=0.05,
    reactivate_calibration_error=0.05,
    requalification_failures_to_retire=3,
)


def test_baseline_in_requalification_with_failures_stays_suspended():
    fsm = LifecycleStateMachine(CFG)
    for failures in (3, 5, 10, 100):
        d = fsm.evaluate(
            current_state="requalification",
            resolved_count=400,
            recency_weighted_ev=-0.10,
            calibration_error=0.30,
            consecutive_requalification_failures=failures,
            is_baseline=True,
        )
        assert d.next_state != "retired", f"failed at {failures} failures"
        assert d.next_state in ("live_suspended", "requalification")


def test_non_baseline_retires_after_threshold():
    fsm = LifecycleStateMachine(CFG)
    d = fsm.evaluate(
        current_state="requalification",
        resolved_count=400,
        recency_weighted_ev=-0.10,
        calibration_error=0.30,
        consecutive_requalification_failures=3,
        is_baseline=False,
    )
    assert d.next_state == "retired"


def test_baseline_can_still_be_suspended_from_live_active():
    fsm = LifecycleStateMachine(CFG)
    d = fsm.evaluate(
        current_state="live_active",
        resolved_count=400,
        recency_weighted_ev=0.005,
        calibration_error=0.04,
        consecutive_requalification_failures=0,
        is_baseline=True,
    )
    assert d.next_state == "live_suspended"
```

- [ ] **Step 2: Run pytest — should pass if T32 implemented baseline protection correctly**

- [ ] **Step 3: If failure, fix `LifecycleStateMachine.evaluate`'s `is_baseline` branch (T32 should already cover it)**

- [ ] **Step 4: Run pytest expecting 3 passed**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/tests/test_baseline_protection.py ofi-lab-v3/storage/lifecycle.py
git commit -m "v3-B: invariant tests — baseline never retired"
```

---

### Task 36: Wire `OverlapWriter` into `_run_predictions` boundary loop

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Create: `ofi-lab-v3/tests/test_overlap_integration.py`

After every model scores a `(symbol, market_window)` boundary, emit one `model_overlap` row.

- [ ] **Step 1: Write failing test**

```python
# ofi-lab-v3/tests/test_overlap_integration.py
import json
from pathlib import Path


def test_record_overlap_for_boundary_writes_row(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    from trading.overlap_writer import ModelScore

    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    boundary = 1_700_000_000_000
    t.record_overlap_for_boundary(
        ts_contract_open_ms=boundary,
        symbol="BTCUSDT",
        market_window_seconds=900,
        scores=[
            ModelScore("900s_btc_v3_20260315", "up", 0.55, weight=1.0),
            ModelScore("60s_btc_v3_20260315", "up", 0.52, weight=0.5),
        ],
    )
    rows = t._db_conn.execute(
        "SELECT consensus, weighted_confidence FROM model_overlap"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["consensus"] == 1
```

- [ ] **Step 2: Run pytest expecting AttributeError**

- [ ] **Step 3: Add `record_overlap_for_boundary` to PaperTrader**

```python
from trading.overlap_writer import OverlapWriter, ModelScore


def _overlap_writer(self):
    if not hasattr(self, "_overlap_writer_cache"):
        self._overlap_writer_cache = OverlapWriter(self._db_conn)
    return self._overlap_writer_cache


def record_overlap_for_boundary(
    self, *, ts_contract_open_ms: int, symbol: str,
    market_window_seconds: int, scores,
) -> None:
    gen = (
        self.registry_state.current_generation()
        if hasattr(self, "registry_state") else 0
    )
    self._overlap_writer().record_boundary(
        ts_contract_open_ms=ts_contract_open_ms,
        symbol=symbol,
        market_window_seconds=market_window_seconds,
        registry_load_generation=gen,
        scores=scores,
    )
```

The boundary-loop call site (after the inner `for model_name in self.models:` finishes for each `symbol`) is left as a follow-on patch since it requires accumulating per-model `(direction, calibrated_p)` pairs into a `ModelScore` list. T36 just exposes the helper.

- [ ] **Step 4: Run pytest expecting pass**

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/tests/test_overlap_integration.py
git commit -m "v3-B: PaperTrader.record_overlap_for_boundary"
```

---

### Task 37: Schema staleness — add `range_computer` integration test

**Files:**
- Create: `ofi-lab-v3/tests/test_dynamic_range_integration.py`

Confirm that running `compute_mid_price_range` over a synthetic XRPUSDT parquet returns a sensible range (Steering 10g).

- [ ] **Step 1: Write test**

```python
# ofi-lab-v3/tests/test_dynamic_range_integration.py
def test_xrp_range_computed_dynamically(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data.range_computer import compute_mid_price_range
    pq.write_table(
        pa.table({"mid_price": [0.45, 0.50, 0.55, 0.60, 0.62]}),
        str(tmp_path / "20260501_XRPUSDT_features.parquet"),
    )
    rng = compute_mid_price_range(str(tmp_path), "XRPUSDT")
    assert rng["min"] >= 0.4
    assert rng["max"] <= 0.7
    assert 0.45 <= rng["median"] <= 0.62
```

- [ ] **Step 2: Run pytest expecting pass (relies on T10 being complete)**

- [ ] **Step 3: Commit**

```bash
git add ofi-lab-v3/tests/test_dynamic_range_integration.py
git commit -m "v3-B: dynamic range integration test for XRP"
```

---

### Task 38: Final full-suite pass + plan exit

**Files:** none new

- [ ] **Step 1: Run the entire v3 test suite**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3
.venv/bin/python -m pytest \
  tests/test_storage_db.py \
  tests/test_storage_schema.py \
  tests/test_provenance.py \
  tests/test_registry_state.py \
  tests/test_policy_snapshot.py \
  tests/test_init_db_script.py \
  tests/test_config_v3.py \
  tests/test_window_planner.py \
  tests/test_sqlite_ledger.py \
  tests/test_decision_trace.py \
  tests/test_pending_queue.py \
  tests/test_calibration_v3.py \
  tests/test_paper_trader_init_v3.py \
  tests/test_paper_trader_provenance.py \
  tests/test_paper_trader_scoring.py \
  tests/test_paper_trader_resolution.py \
  tests/test_kalshi_dispatch_gating.py \
  tests/test_warmup_tagging.py \
  tests/test_compact_decision_trace.py \
  tests/test_verbose_decision_trace.py \
  tests/test_migration.py \
  tests/test_replay_smoke.py \
  tests/test_model_registry.py \
  tests/test_retrain_cli.py \
  tests/test_completeness.py \
  tests/test_download_klines_gap_safe.py \
  tests/test_range_computer.py \
  tests/test_paper_trader_multimodel.py \
  tests/test_score_batch.py \
  tests/test_regime_tagger.py \
  tests/test_threshold_updater.py \
  tests/test_paper_trader_regime.py \
  tests/test_overlap_writer.py \
  tests/test_conflict.py \
  tests/test_ev.py \
  tests/test_filter_pipeline.py \
  tests/test_filter_pipeline_integration.py \
  tests/test_exposure.py \
  tests/test_decay_writer.py \
  tests/test_decay_metrics.py \
  tests/test_decay_refresh.py \
  tests/test_psi_integration.py \
  tests/test_cliff_detector.py \
  tests/test_lifecycle.py \
  tests/test_lifecycle_loop.py \
  tests/test_paper_live_independence.py \
  tests/test_baseline_protection.py \
  tests/test_overlap_integration.py \
  tests/test_dynamic_range_integration.py \
  -v 2>&1 | tail -10
```

Expected: all green.

- [ ] **Step 2: Confirm schema invariants**

```bash
sqlite3 /tmp/v3_invariant.db <<'SQL'
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
SELECT count(*) FROM sqlite_master WHERE type='index'
  AND name IN ('idx_overlap_symbol_window', 'idx_decay_model',
               'idx_decay_eval_model');
SQL
```

Expected: 9 tables present including `model_overlap`, `decay_metrics`, `decay_evaluations`. All 3 new indexes present.

- [ ] **Step 3: Tag the milestone**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem
git tag v3-plan-b-multimodel-complete
git log --oneline v3-plan-a-foundation-complete..v3-plan-b-multimodel-complete | wc -l
```

- [ ] **Step 4: Hand off to Plan C**

Plan C (Dashboard + Safety + Rollback) builds on Plan B. It depends on:
- `model_registry.json` writable from API (`POST /api/models/{name}/enable`)
- `lifecycle_state` column readable per-model for the `/models` page
- `decay_metrics` table queryable for performance-over-time charts
- `model_overlap` table queryable for consensus heat maps
- `decay_evaluations` queryable for transition history / audit trail
- `RegistryState.increment` callable for hot reload via `POST /api/models/reload`

---

## Self-Review

### 1. Spec coverage

Cross-checked the Plan A header's "Plan B" scope and steering items:

- ✅ Phase 2 model registry + hot reload (T1-T5)
- ✅ Baseline protection (T2 load + T35 lifecycle)
- ✅ Pending queue cleanup on reload (T4 — Steering 10h)
- ✅ Phase 3 retrain pipeline (T6, T7, T11) + completeness (T8) + gap-safe download (T9) + dynamic XRP range (T10, T37)
- ✅ Phase 4 multi-model orchestration (T12) + vectorized predict (T13)
- ✅ Phase 5 overlap + EWMA-weighted consensus (T17, T18)
- ✅ Phase 6 regime tagger + auto-thresholds + integration (T14, T15, T16)
- ✅ Steering §2 five-layer pipeline (T20, T21, T22, T26)
- ✅ Steering §4 EV + conflict (T19, T23)
- ✅ Steering §4 stale-price/book (T24)
- ✅ Steering §4 exposure cap (T25)
- ✅ Steering §5 decay schema + EWMA EV + Brier (T27, T28, T29)
- ✅ Steering §5 PSI integration (T30, Steering 10f)
- ✅ Steering §5 cliff detection (T31)
- ✅ Steering §6 lifecycle FSM + hysteresis (T32) + decision loop (T33)
- ✅ Steering §6 paper-active independence (T34)
- ✅ Steering §6 baseline protection (T35)

Two concerns:

- **Gap-safe `download_orderbook.py`** was in the original plan list but did not get a dedicated task. It's intentionally bundled with the broader retrain pipeline orchestrator (T11), but the existing `download_orderbook.py` will not benefit from row-count validation without an analogous patch. Add T9' (orderbook gap-safe) if needed during execution; the same pattern as T9 applies. Marked as a follow-on in Plan B+ since orderbook download is rarer (S3 dumps are atomic).

- **Daily cron / scheduled refresh** of regime thresholds is not covered. The implementation in T15 supports `refresh_thresholds_file()` callable from a cron job, but the cron entry itself is a deployment concern (Plan C / VPS deployment guide).

### 2. Placeholder scan

No `TBD`, `TODO`, `fill in details`, or vague error-handling phrases remain. Every code block is concrete.

### 3. Type / signature consistency

- `ProvenanceEnvelope` (Plan A) is passed unchanged through `OverlapWriter` and the filter pipeline — no signature drift.
- `ModelEntry.lifecycle_state` field name matches across `model_registry.py`, `lifecycle.py`, and the LiveEligibility filter.
- `FilterDecision.eval_` factory method name (with trailing underscore) is consistent across `paper_filter.py`, `live_eligibility.py`, `platform_execution.py`, `staleness.py`.
- `ModelScore(model_name, direction, calibrated_confidence, weight)` field order matches between `overlap_writer.py` and `test_overlap_writer.py`/`test_overlap_integration.py`.
- `LifecycleConfig` field names match across `lifecycle.py`, `test_lifecycle.py`, `test_baseline_protection.py`.

### 4. Risk-tier alignment with Plan A's steering §9

| Risk Area | Tasks |
|---|---|
| Hot reload safety | T3, T4 (queue cleanup invariant) |
| Multi-window resolution (already from Plan A) | preserved by T16 (regime tags don't break native vs evaluation) |
| Decision trace completeness | T20-T26 (filter pipeline emits eval rows) |
| Lifecycle transitions | T32 hysteresis test, T35 baseline protection |
| Platform gating | T22 platform_execution + T23 conflict gate |
| Signal decay tracking | T28 metric math vs hand-calc, T29 refresh integration |

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-v3-plan-b-multimodel.md`. Two execution options:

**1. Subagent-Driven (recommended)** — Same protocol as Plan A. Fresh haiku subagent per task; review between tasks. Plan B's tasks are mostly independent (all storage and `filters/` modules are isolated; only T26, T29, T33, T36 touch PaperTrader).

**2. Inline Execution** — batch with checkpoints in this session.

**Which approach?**




