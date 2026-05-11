# Model A v3 — Plan A: Foundation, Provenance & Bug-Fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a new `ofi-lab-v3/` container that runs the existing h300 BTC 900s baseline unchanged in behavior, but with SQLite-backed storage, a full provenance envelope on every record, two-tier decision tracing, native-vs-evaluation resolution semantics, per-model calibration, warmup tagging, and the four hardcoded-window/symbol bugs fixed.

**Architecture:**
- New directory `ofi-lab-v3/` cloned from `ofi-lab/`. Deploy path on VPS becomes `/home/johnny/ofi-lab-v3/` and a new container `model-a-v3`. v2-clone keeps running until v3 is observed stable.
- A single SQLite database at `/data/v3.db` holds `predictions`, `paper_trades`, `decision_traces`, `calibration_outcomes`, and `registry_audit` tables. JSONL ledgers are retired in v3 (one-time migration imports historical v2 data).
- Every prediction and trade carries a `ProvenanceEnvelope` (model artifact hash, feature names hash, policy config hash, calibration map hash, decision policy version counter, registry load generation counter, training horizon, market window, train dates, feature version, platform, warmup state).
- Every prediction generates one `resolution_type='native'` row at `boundary_ms + (training_horizon_seconds × 1000)` plus N `resolution_type='evaluation'` rows at the other live windows {300, 900, 1800, 3600} − {training_horizon_seconds}. Lifecycle and decay queries filter on `resolution_type='native'`.
- Compact decision trace fields (outcome, reason, EV, Kelly, size, order type) are stored inline on each prediction/trade row. Verbose decision traces (per-filter inputs, Kelly math breakdown, fee math, platform gate details) are appended to a separate `decision_traces` table keyed by `prediction_id`.

**Tech Stack:**
- Python 3.12, SQLite 3 (stdlib `sqlite3`), aiohttp, LightGBM, pytest. No new runtime dependencies vs `ofi-lab/requirements.txt`.

**No bit-for-bit parity requirement.** IO and timing drift make exact replay unrealistic. Plan A's correctness is validated by: schema integrity, migration round-trip, multi-window resolution math, provenance hash determinism, and end-to-end paper run that produces well-formed predictions and resolutions for the h300 BTC 900s baseline path.

---

## Spec Coverage

| Spec / Steering Item | Tasks |
|---|---|
| Phase 1.1 SQLite schema | T2, T3, T4, T5 |
| Phase 1.2 Multi-window resolution | T15, T16 |
| Phase 1.3 Feature parquet unchanged | (no task — confirmed at T1) |
| Phase 1.4 Migration script | T20 |
| Phase 7.1 `MIN_SAMPLES_PER_BIN = 20` | T18 |
| Phase 7.2 Per-model calibrators | T19 |
| Phase 7.3 Calibration staleness field | T19 |
| Steering §1 Provenance envelope | T6, T7, T8, T9 |
| Steering §3 Two-tier decision trace | T4 (schema), T11 (compact write path), T12 (verbose write path) |
| Steering §7 Hot-reload safety primitives (generation counter, baseline protection ground-rules) | T8, T9 (counters + idempotency index — full hot reload arrives in Plan B) |
| Steering §10a Hardcoded 900s pred resolution | T15 |
| Steering §10b Native vs evaluation distinction | T3 (schema column), T15, T16 |
| Steering §10c Hardcoded `name == "h300" and symbol == "BTCUSDT" and duration == 900` Kalshi guard | T17 |
| Steering §10d Hardcoded Kalshi ticker resolution `+ 900` | T17 |
| Steering §10e `contract_duration_seconds=900` unconditional | T16 |
| Steering §10h Pending resolution model cleanup primitive | T14 (queue keyed on model_name + generation) |
| Steering §10j O(n) merge → indexed lookups (SQLite indexes replace JSONL merge entirely) | T3 |
| Warmup tagging on every record | T13 |

**Out of scope (explicitly Plan B or Plan C):**
- Model registry hot reload, baseline-removal rejection, registry audit trail (Plan B / Plan C)
- Regime tagging compute (Plan B) — schema columns are reserved here, populated NULL in Plan A
- Overlap / consensus logging (Plan B)
- 5-layer filter pipeline / EV gate / lifecycle (Plan B)
- Signal decay tracking, PSI integration (Plan B)
- `/models` dashboard and toggles, audit trail UI, rollback procedures (Plan C)
- XRP `MID_PRICE_TRAINING_RANGE` (Plan B / training pipeline)
- Polymarket execution (out of v3)

---

## File Structure

### Directory clone

`ofi-lab-v3/` is a full copy of `ofi-lab/` at the start of T1. Files modified or added by Plan A:

| File | Action | Responsibility |
|---|---|---|
| `ofi-lab-v3/storage/__init__.py` | Create | Package marker |
| `ofi-lab-v3/storage/db.py` | Create | Connection factory, schema bootstrap, migrations registry |
| `ofi-lab-v3/storage/schema.sql` | Create | Canonical DDL for all tables and indexes |
| `ofi-lab-v3/storage/sqlite_ledger.py` | Create | `SQLiteLedger` — drop-in replacement for `trading/ledger.py` with provenance + multi-window |
| `ofi-lab-v3/storage/decision_trace.py` | Create | `DecisionTraceWriter` — verbose trace append-only writer |
| `ofi-lab-v3/storage/provenance.py` | Create | `ProvenanceEnvelope` dataclass + hash helpers |
| `ofi-lab-v3/storage/policy_snapshot.py` | Create | `PolicySnapshot` — captures filter/threshold/Kelly/EV/per-symbol overrides into a hashable canonical form, owns `decision_policy_version` counter |
| `ofi-lab-v3/storage/registry_state.py` | Create | Owns `registry_load_generation` counter (atomic increment, persisted) |
| `ofi-lab-v3/trading/paper_trader.py` | Modify | Wire provenance, native+evaluation rows, fix hardcoded 900s, MIN_SAMPLES change uses new calibrator path |
| `ofi-lab-v3/execution/calibration.py` | Modify | `MIN_SAMPLES_PER_BIN = 20`, per-model instance, persist `last_refit_at`, hash exposure |
| `ofi-lab-v3/execution/kalshi_live_trader.py` | Modify (minimal) | Accept registry-driven dispatch flag instead of hardcoded `model == "h300"` check (call site moves to paper_trader) |
| `ofi-lab-v3/config.py` | Modify | Add `STORAGE_DB_PATH`, `EVALUATION_WINDOWS`, baseline registry stub |
| `ofi-lab-v3/Dockerfile.v3` | Create | New container build (`golden-goose-v3:foundation` image) |
| `ofi-lab-v3/scripts/migrate_jsonl_to_sqlite.py` | Create | One-time importer of v2 JSONL data |
| `ofi-lab-v3/scripts/init_db.py` | Create | Bootstrap an empty `/data/v3.db` from `schema.sql` |
| `ofi-lab-v3/tests/test_provenance.py` | Create | Hash determinism + envelope round-trip tests |
| `ofi-lab-v3/tests/test_sqlite_ledger.py` | Create | Schema, idempotency, native+evaluation row generation, indexes |
| `ofi-lab-v3/tests/test_decision_trace.py` | Create | Verbose trace writer tests |
| `ofi-lab-v3/tests/test_calibration_v3.py` | Create | Per-model calibrators, MIN_SAMPLES=20, staleness |
| `ofi-lab-v3/tests/test_multi_window_resolution.py` | Create | Native vs evaluation resolution scheduling math |
| `ofi-lab-v3/tests/test_migration.py` | Create | JSONL → SQLite round-trip with synthetic and real fixtures |
| `ofi-lab-v3/tests/test_paper_trader_provenance.py` | Create | Integration: scoring path stamps provenance correctly |

### Files explicitly UNCHANGED in Plan A

Per the Builder Prompt's "Things That Must Not Change" list:
- `trading/live_features.py`
- `execution/apfs/`
- `api/kalshi.py`
- `validation/run_training.py`
- `V3_FEATURE_COLS`
- The 0.53 AUC-ROC gate
- The maker-first Kalshi order strategy
- All feature engineering scripts under `data/`

### Files PRESERVED for reference

- `trading/ledger.py` — kept in `ofi-lab-v3/` byte-identical to `ofi-lab/`. The new `SQLiteLedger` is a peer, not a replacement-by-overwrite. The paper trader is rewired to use `SQLiteLedger`. Old `Ledger` is retained so the migration script can read v2 JSONL using the same parsers and so anything else in the repo that imports from `trading.ledger` still works during transition.

---

## Database Schema (canonical, T3 reference)

```sql
-- Tables created from this schema in Task 3.
-- Stored verbatim at ofi-lab-v3/storage/schema.sql.

-- =========================================================================
-- predictions: every model score, both native and evaluation rows.
-- =========================================================================
CREATE TABLE predictions (
    -- Identity
    prediction_id          TEXT PRIMARY KEY,

    -- Provenance: model identity
    model_name             TEXT NOT NULL,
    model_artifact_hash    TEXT NOT NULL,
    feature_names_hash     TEXT NOT NULL,
    feature_version        TEXT NOT NULL,
    training_horizon_seconds INTEGER NOT NULL,
    train_window_start     TEXT,
    train_window_end       TEXT,
    train_cutoff           TEXT,
    registry_load_generation INTEGER NOT NULL,

    -- Provenance: policy + calibration identity
    policy_config_hash     TEXT NOT NULL,
    decision_policy_version INTEGER NOT NULL,
    calibration_map_hash   TEXT NOT NULL,

    -- Symbol & evaluation window
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    resolution_type        TEXT NOT NULL CHECK (resolution_type IN ('native','evaluation')),

    -- Timing
    ts_model_ran_ms        INTEGER NOT NULL,
    ts_contract_open_ms    INTEGER NOT NULL,
    ts_resolve_at_ms       INTEGER NOT NULL,

    -- Prediction
    pred_proba_raw         REAL NOT NULL,
    pred_proba_calibrated  REAL NOT NULL,
    pred_direction         TEXT NOT NULL CHECK (pred_direction IN ('up','down')),
    above_threshold        INTEGER NOT NULL,

    -- State flags
    warmup                 INTEGER NOT NULL DEFAULT 0,
    trade_eligible         INTEGER NOT NULL DEFAULT 1,
    platform               TEXT NOT NULL CHECK (platform IN ('paper','kalshi','polymarket')),

    -- Market context
    p_market               REAL,
    p_model_minus_market   REAL,

    -- Time context
    utc_hour               INTEGER,
    day_of_week            INTEGER,
    is_weekend             INTEGER,

    -- Regime tags (populated in Plan B; reserved here so schema is stable)
    regime_volatility      TEXT,
    regime_liquidity       TEXT,
    regime_trend           TEXT,
    relative_spread        REAL,

    -- Resolution
    price_at_open          REAL,
    price_at_close         REAL,
    contract_result        TEXT,
    prediction_correct     INTEGER,
    resolved               INTEGER NOT NULL DEFAULT 0,
    ts_resolved_ms         INTEGER,

    -- Compact decision trace (inline)
    decision_outcome       TEXT,
    decision_reason        TEXT,
    ev_estimate            REAL,
    kelly_fraction_capped  REAL,
    final_size_usdc        REAL,
    order_type             TEXT,

    -- Audit
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_pred_model_symbol      ON predictions(model_name, symbol);
CREATE INDEX idx_pred_ts                ON predictions(ts_contract_open_ms);
CREATE INDEX idx_pred_market_window     ON predictions(market_window_seconds);
CREATE INDEX idx_pred_unresolved        ON predictions(resolved) WHERE resolved = 0;
CREATE INDEX idx_pred_warmup            ON predictions(warmup);
CREATE INDEX idx_pred_resolution_type   ON predictions(resolution_type);
CREATE INDEX idx_pred_generation        ON predictions(model_name, registry_load_generation);
CREATE INDEX idx_pred_native_for_decay  ON predictions(model_name, symbol, resolution_type, ts_contract_open_ms)
    WHERE resolution_type = 'native' AND resolved = 1;

-- Idempotency: prevent duplicate scoring on hot reload re-entry.
CREATE UNIQUE INDEX idx_pred_idempotent ON predictions(
    model_name, symbol, ts_contract_open_ms, market_window_seconds, registry_load_generation
);

-- =========================================================================
-- paper_trades: predictions that pass paper-tier filters become rows here.
-- =========================================================================
CREATE TABLE paper_trades (
    trade_id               TEXT PRIMARY KEY,
    prediction_id          TEXT NOT NULL REFERENCES predictions(prediction_id),

    -- Provenance copy (denormalized for fast queries; matches prediction row)
    model_name             TEXT NOT NULL,
    model_artifact_hash    TEXT NOT NULL,
    policy_config_hash     TEXT NOT NULL,
    decision_policy_version INTEGER NOT NULL,
    calibration_map_hash   TEXT NOT NULL,
    registry_load_generation INTEGER NOT NULL,
    feature_version        TEXT NOT NULL,
    training_horizon_seconds INTEGER NOT NULL,

    -- Symbol & window
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    resolution_type        TEXT NOT NULL CHECK (resolution_type IN ('native','evaluation')),

    -- Timing
    ts_model_ran_ms        INTEGER NOT NULL,
    ts_contract_open_ms    INTEGER NOT NULL,
    ts_resolve_at_ms       INTEGER NOT NULL,

    -- Trade params
    pred_proba_raw         REAL NOT NULL,
    pred_proba_calibrated  REAL NOT NULL,
    pred_direction         TEXT NOT NULL,
    confidence_threshold_used REAL NOT NULL,
    simulated_stake_usdc   REAL,
    p_market               REAL,
    suppressed_reason      TEXT,
    filter_mode            TEXT,
    warmup                 INTEGER NOT NULL DEFAULT 0,
    platform               TEXT NOT NULL,

    -- Compact decision trace (inline)
    decision_outcome       TEXT NOT NULL,
    decision_reason        TEXT,
    ev_estimate            REAL,
    kelly_fraction_capped  REAL,
    final_size_usdc        REAL,
    order_type             TEXT,

    -- Resolution
    price_at_open          REAL,
    price_at_close         REAL,
    contract_result        TEXT,
    prediction_correct     INTEGER,
    gross_pnl              REAL,
    fee_paid               REAL,
    net_pnl                REAL,
    trade_result           TEXT,
    pnl_method             TEXT,
    resolved               INTEGER NOT NULL DEFAULT 0,
    ts_resolved_ms         INTEGER,

    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_trade_model            ON paper_trades(model_name, symbol);
CREATE INDEX idx_trade_unresolved       ON paper_trades(resolved) WHERE resolved = 0;
CREATE INDEX idx_trade_resolution_type  ON paper_trades(resolution_type);
CREATE INDEX idx_trade_native_for_decay ON paper_trades(model_name, symbol, resolution_type, ts_contract_open_ms)
    WHERE resolution_type = 'native' AND resolved = 1;

-- =========================================================================
-- decision_traces: verbose forensic trace, append-only, linked by prediction_id.
-- =========================================================================
CREATE TABLE decision_traces (
    trace_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id          TEXT NOT NULL,
    ts                     TEXT NOT NULL,

    -- Filter chain serialized as JSON array
    --   [{"name": "...", "threshold": ..., "input_value": ..., "passed": 0|1}, ...]
    filters_json           TEXT NOT NULL,

    -- Kelly math breakdown
    kelly_raw              REAL,
    kelly_capped           REAL,
    bankroll_used          REAL,
    per_trade_cap_usdc     REAL,

    -- Fee model
    fee_model              TEXT NOT NULL,
    fee_amount             REAL,

    -- Platform-specific gating snapshot (JSON)
    platform_gate_json     TEXT,

    -- Context echoed for forensic completeness
    warmup                 INTEGER NOT NULL,
    consensus_data_json    TEXT,
    policy_config_hash     TEXT NOT NULL,
    calibration_map_hash   TEXT NOT NULL,
    registry_load_generation INTEGER NOT NULL
);

CREATE INDEX idx_trace_pid ON decision_traces(prediction_id);
CREATE INDEX idx_trace_ts  ON decision_traces(ts);

-- =========================================================================
-- calibration_outcomes: one row per native resolution, used for refit + decay.
-- =========================================================================
CREATE TABLE calibration_outcomes (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    prediction_id          TEXT NOT NULL,
    model_name             TEXT NOT NULL,
    symbol                 TEXT NOT NULL,
    market_window_seconds  INTEGER NOT NULL,
    resolution_type        TEXT NOT NULL,
    side_conf              REAL NOT NULL,
    won                    INTEGER NOT NULL,
    warmup                 INTEGER NOT NULL DEFAULT 0,
    regime_volatility      TEXT,
    regime_liquidity       TEXT
);

CREATE INDEX idx_cal_model ON calibration_outcomes(model_name, symbol, market_window_seconds);
CREATE INDEX idx_cal_native ON calibration_outcomes(model_name, resolution_type) WHERE resolution_type = 'native';

-- =========================================================================
-- registry_audit: append-only log of registry generation increments.
--                 Plan A only writes the bootstrap row; Plan B writes on reload.
-- =========================================================================
CREATE TABLE registry_audit (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    generation             INTEGER NOT NULL,
    reason                 TEXT NOT NULL,
    detail_json            TEXT
);

CREATE UNIQUE INDEX idx_audit_generation ON registry_audit(generation);

-- =========================================================================
-- policy_audit: append-only log of policy version increments.
-- =========================================================================
CREATE TABLE policy_audit (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                     TEXT NOT NULL,
    decision_policy_version INTEGER NOT NULL,
    policy_config_hash     TEXT NOT NULL,
    snapshot_json          TEXT NOT NULL,
    initiated_by           TEXT
);

CREATE UNIQUE INDEX idx_policy_version ON policy_audit(decision_policy_version);
```

Key invariants enforced by the schema:

1. **Idempotency on (model_name, symbol, ts_contract_open_ms, market_window_seconds, registry_load_generation)** — a hot reload mid-boundary cannot produce duplicate rows for the same model+window+boundary at the same generation. A new generation is allowed to re-score the same boundary (this is intentional: the new generation has a different artifact hash and represents a different decision).
2. **Native rows are unambiguous for lifecycle queries** via partial index `idx_pred_native_for_decay`. Every dashboard/decay/lifecycle query that drives a state transition must include `resolution_type = 'native'`.
3. **`resolution_type` column is `CHECK`-constrained.** No row can carry an arbitrary value.
4. **`platform` column is `CHECK`-constrained.** Plan A only writes `'paper'` and `'kalshi'`. `'polymarket'` is reserved.

---

## Tasks

### Task 1: Clone `ofi-lab/` to `ofi-lab-v3/` and verify baseline test suite

**Files:**
- Create: `/Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3/` (full directory copy)

- [ ] **Step 1: Copy the directory**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem
rsync -a --exclude '__pycache__' --exclude '.mypy_cache' --exclude '*.pyc' \
  ofi-lab/ ofi-lab-v3/
```

- [ ] **Step 2: Confirm structural parity**

```bash
diff -rq ofi-lab ofi-lab-v3 \
  | grep -v '__pycache__' \
  | grep -v '.mypy_cache' \
  | grep -v '.pyc'
```

Expected: empty output (every file present in both, byte-identical).

- [ ] **Step 3: Run the existing test suite against the clone**

```bash
cd ofi-lab-v3
python -m pytest tests/ -v 2>&1 | tail -40
```

Expected: same pass/fail profile as `ofi-lab/`. Any failure here is a copy bug, not a Plan A defect — fix before proceeding.

- [ ] **Step 4: Commit**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem
git add ofi-lab-v3/
git commit -m "v3: clone ofi-lab → ofi-lab-v3 as foundation baseline

Plan A Task 1. Byte-identical copy of v2-clone. Subsequent
tasks add provenance, SQLite, and bug fixes on top of this
baseline."
```

---

### Task 2: Create `storage/` package skeleton

**Files:**
- Create: `ofi-lab-v3/storage/__init__.py`
- Create: `ofi-lab-v3/storage/db.py`
- Test: `ofi-lab-v3/tests/test_storage_db.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_storage_db.py
import sqlite3
from pathlib import Path

import pytest

from storage.db import open_database, DEFAULT_DB_PATH


def test_open_database_creates_file_if_missing(tmp_path):
    db_path = tmp_path / "v3.db"
    conn = open_database(str(db_path))
    assert db_path.exists()
    assert isinstance(conn, sqlite3.Connection)
    conn.close()


def test_open_database_enables_wal_and_foreign_keys(tmp_path):
    db_path = tmp_path / "v3.db"
    conn = open_database(str(db_path))
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert journal == "wal"
    assert fk == 1
    conn.close()


def test_default_db_path_constant():
    assert DEFAULT_DB_PATH == "/data/v3.db"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3
python -m pytest tests/test_storage_db.py -v
```

Expected: `ModuleNotFoundError: No module named 'storage'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ofi-lab-v3/storage/__init__.py
"""SQLite-backed storage layer for v3."""
```

```python
# ofi-lab-v3/storage/db.py
"""Database connection factory.

All v3 storage flows through a single SQLite database. WAL mode is
enabled so the API server can read concurrently with the paper trader's
writes. Foreign keys are enforced for prediction → trade integrity.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = "/data/v3.db"


def open_database(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (and create if needed) the v3 SQLite database.

    Returns a connection with WAL journaling, foreign keys enforced,
    and a 5-second busy timeout to handle multi-process contention
    between the API server and the paper trader.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_storage_db.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/__init__.py ofi-lab-v3/storage/db.py \
        ofi-lab-v3/tests/test_storage_db.py
git commit -m "v3: storage.db connection factory with WAL + FK"
```

---

### Task 3: Define schema in `storage/schema.sql` and bootstrap loader

**Files:**
- Create: `ofi-lab-v3/storage/schema.sql` (canonical DDL — copy of the schema block in this plan's "Database Schema" section)
- Modify: `ofi-lab-v3/storage/db.py` — add `init_schema(conn)`
- Test: `ofi-lab-v3/tests/test_storage_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_storage_schema.py
from storage.db import open_database, init_schema


EXPECTED_TABLES = {
    "predictions",
    "paper_trades",
    "decision_traces",
    "calibration_outcomes",
    "registry_audit",
    "policy_audit",
}

EXPECTED_INDEXES_INCLUDE = {
    "idx_pred_idempotent",
    "idx_pred_native_for_decay",
    "idx_trace_pid",
    "idx_cal_native",
    "idx_audit_generation",
    "idx_policy_version",
}


def test_init_schema_creates_all_tables(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert EXPECTED_TABLES.issubset(names), f"missing: {EXPECTED_TABLES - names}"


def test_init_schema_creates_critical_indexes(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()
    names = {r["name"] for r in rows}
    missing = EXPECTED_INDEXES_INCLUDE - names
    assert not missing, f"missing indexes: {missing}"


def test_init_schema_is_idempotent(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    init_schema(conn)  # second call must not raise
    rows = conn.execute(
        "SELECT count(*) AS n FROM sqlite_master WHERE type='table'"
    ).fetchone()
    assert rows["n"] >= len(EXPECTED_TABLES)


def test_resolution_type_check_constraint_enforced(tmp_path):
    import sqlite3
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    with pytest_raises_integrity():
        conn.execute(
            "INSERT INTO predictions ("
            " prediction_id, model_name, model_artifact_hash, feature_names_hash,"
            " feature_version, training_horizon_seconds, registry_load_generation,"
            " policy_config_hash, decision_policy_version, calibration_map_hash,"
            " symbol, market_window_seconds, resolution_type,"
            " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
            " pred_proba_raw, pred_proba_calibrated, pred_direction, above_threshold,"
            " platform"
            ") VALUES ("
            " 'p1','m','h','f','v3',900,0,'ph',0,'ch',"
            " 'BTCUSDT',900,'BOGUS',1,2,3,0.5,0.5,'up',0,'paper'"
            ")"
        )


def pytest_raises_integrity():
    import sqlite3
    import pytest
    return pytest.raises(sqlite3.IntegrityError)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_storage_schema.py -v
```

Expected: `ImportError: cannot import name 'init_schema'`.

- [ ] **Step 3: Create `schema.sql`**

Copy the full DDL block from the "Database Schema" section of this plan into `ofi-lab-v3/storage/schema.sql` verbatim.

- [ ] **Step 4: Add `init_schema` to `db.py`**

```python
# Append to ofi-lab-v3/storage/db.py

from pathlib import Path as _Path

_SCHEMA_PATH = _Path(__file__).parent / "schema.sql"


def init_schema(conn) -> None:
    """Apply the canonical schema. Idempotent.

    Reads ``storage/schema.sql`` and executes its DDL. SQLite's
    ``CREATE TABLE`` and ``CREATE INDEX`` are not natively idempotent,
    so the SQL file uses ``CREATE TABLE IF NOT EXISTS`` and
    ``CREATE INDEX IF NOT EXISTS`` for safe re-application.
    """
    sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
```

Then update `schema.sql` to use `IF NOT EXISTS` on every `CREATE TABLE` and `CREATE INDEX`.

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m pytest tests/test_storage_schema.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add ofi-lab-v3/storage/schema.sql ofi-lab-v3/storage/db.py \
        ofi-lab-v3/tests/test_storage_schema.py
git commit -m "v3: schema.sql with full provenance envelope + idempotency index"
```

---

### Task 4: Hash utilities in `storage/provenance.py`

**Files:**
- Create: `ofi-lab-v3/storage/provenance.py`
- Test: `ofi-lab-v3/tests/test_provenance.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_provenance.py
import json
import tempfile
from pathlib import Path

import pytest

from storage.provenance import (
    ProvenanceEnvelope,
    sha256_file,
    sha256_canonical_json,
    feature_names_hash,
    policy_config_hash,
    calibration_map_hash,
)


def test_sha256_file_is_deterministic(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world")
    h1 = sha256_file(str(p))
    h2 = sha256_file(str(p))
    assert h1 == h2
    assert len(h1) == 64
    # Known SHA-256 of "hello world"
    assert h1 == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_sha256_canonical_json_independent_of_key_order():
    a = {"b": 1, "a": 2, "c": [3, 1, 2]}
    b = {"c": [3, 1, 2], "a": 2, "b": 1}
    assert sha256_canonical_json(a) == sha256_canonical_json(b)


def test_feature_names_hash_is_order_independent():
    h1 = feature_names_hash(["mlofi", "ofi", "spread"])
    h2 = feature_names_hash(["spread", "mlofi", "ofi"])
    assert h1 == h2


def test_feature_names_hash_changes_when_member_changes():
    h1 = feature_names_hash(["mlofi", "ofi"])
    h2 = feature_names_hash(["mlofi", "ofi_v2"])
    assert h1 != h2


def test_policy_config_hash_canonicalizes():
    a = {"confidence_threshold": 0.55, "kelly_fraction": 0.25,
         "per_symbol": {"BTCUSDT": 0.55, "SOLUSDT": 0.58}}
    b = {"per_symbol": {"SOLUSDT": 0.58, "BTCUSDT": 0.55},
         "kelly_fraction": 0.25, "confidence_threshold": 0.55}
    assert policy_config_hash(a) == policy_config_hash(b)


def test_calibration_map_hash_changes_with_bins():
    a = {"method": "binmap", "bins": [{"raw": 0.52, "calibrated": 0.50}]}
    b = {"method": "binmap", "bins": [{"raw": 0.52, "calibrated": 0.51}]}
    assert calibration_map_hash(a) != calibration_map_hash(b)


def test_envelope_roundtrip_to_dict():
    env = ProvenanceEnvelope(
        model_name="900s_btc_v3_20260315",
        model_artifact_hash="a" * 64,
        feature_names_hash="b" * 64,
        feature_version="v3",
        training_horizon_seconds=900,
        train_window_start="2025-04-01",
        train_window_end="2026-03-15",
        train_cutoff="2026-03-15",
        registry_load_generation=1,
        policy_config_hash="c" * 64,
        decision_policy_version=7,
        calibration_map_hash="d" * 64,
        platform="paper",
    )
    d = env.to_dict()
    env2 = ProvenanceEnvelope.from_dict(d)
    assert env == env2


def test_envelope_rejects_invalid_platform():
    with pytest.raises(ValueError):
        ProvenanceEnvelope(
            model_name="m", model_artifact_hash="a"*64, feature_names_hash="b"*64,
            feature_version="v3", training_horizon_seconds=900,
            train_window_start=None, train_window_end=None, train_cutoff=None,
            registry_load_generation=0, policy_config_hash="c"*64,
            decision_policy_version=0, calibration_map_hash="d"*64,
            platform="bogus",
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_provenance.py -v
```

Expected: `ModuleNotFoundError: No module named 'storage.provenance'`.

- [ ] **Step 3: Implement `provenance.py`**

```python
# ofi-lab-v3/storage/provenance.py
"""Provenance envelope and hash utilities for v3.

Every prediction and trade carries a ProvenanceEnvelope so that any
record can be unambiguously tied back to the exact (model artifact,
feature schema, policy config, calibration map, registry generation)
that produced it. Hashes are computed deterministically from canonical
representations so that semantically equivalent inputs always produce
the same hash.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Optional

VALID_PLATFORMS = {"paper", "kalshi", "polymarket"}


def sha256_file(path: str) -> str:
    """SHA-256 hex digest of a file's full contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_canonical_json(obj: Any) -> str:
    """SHA-256 of obj serialized with sorted keys and no whitespace.

    Stable across dict insertion orders. NaN/Inf rejected — call sites
    must clean numeric values before hashing.
    """
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                         allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def feature_names_hash(names: Iterable[str]) -> str:
    """Hash a feature-name list. Order independent (sorted before hashing)."""
    return sha256_canonical_json(sorted(names))


def policy_config_hash(config: dict) -> str:
    """Hash the active policy config snapshot."""
    return sha256_canonical_json(config)


def calibration_map_hash(cal_map: dict) -> str:
    """Hash the active calibration bin map.

    Expects {"method": ..., "bins": [{"raw": ..., "calibrated": ...}, ...]}.
    """
    return sha256_canonical_json(cal_map)


@dataclass(frozen=True)
class ProvenanceEnvelope:
    """Full identity context for a single prediction or trade.

    All hash fields are 64-character lowercase hex SHA-256 digests.
    All counters are non-negative monotonic integers.
    """
    model_name: str
    model_artifact_hash: str
    feature_names_hash: str
    feature_version: str
    training_horizon_seconds: int
    train_window_start: Optional[str]
    train_window_end: Optional[str]
    train_cutoff: Optional[str]
    registry_load_generation: int
    policy_config_hash: str
    decision_policy_version: int
    calibration_map_hash: str
    platform: str

    def __post_init__(self) -> None:
        if self.platform not in VALID_PLATFORMS:
            raise ValueError(
                f"invalid platform {self.platform!r}, expected one of {VALID_PLATFORMS}"
            )
        for field, value in [
            ("model_artifact_hash", self.model_artifact_hash),
            ("feature_names_hash", self.feature_names_hash),
            ("policy_config_hash", self.policy_config_hash),
            ("calibration_map_hash", self.calibration_map_hash),
        ]:
            if not (isinstance(value, str) and len(value) == 64):
                raise ValueError(f"{field} must be 64-char hex, got {value!r}")
        if self.training_horizon_seconds <= 0:
            raise ValueError("training_horizon_seconds must be positive")
        if self.registry_load_generation < 0:
            raise ValueError("registry_load_generation must be >= 0")
        if self.decision_policy_version < 0:
            raise ValueError("decision_policy_version must be >= 0")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProvenanceEnvelope":
        return cls(**d)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_provenance.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/provenance.py ofi-lab-v3/tests/test_provenance.py
git commit -m "v3: ProvenanceEnvelope dataclass + sha256 hash utilities"
```

---

### Task 5: `RegistryState` — generation counter persisted in `registry_audit`

**Files:**
- Create: `ofi-lab-v3/storage/registry_state.py`
- Test: `ofi-lab-v3/tests/test_registry_state.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_registry_state.py
from storage.db import open_database, init_schema
from storage.registry_state import RegistryState


def test_first_bootstrap_writes_generation_zero(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty(reason="initial v3 boot")
    assert rs.current_generation() == 0
    rows = conn.execute("SELECT * FROM registry_audit").fetchall()
    assert len(rows) == 1
    assert rows[0]["generation"] == 0
    assert rows[0]["reason"] == "initial v3 boot"


def test_increment_persists_new_generation(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()
    rs.increment(reason="hot reload", detail={"models_added": ["x"]})
    assert rs.current_generation() == 1
    rs.increment(reason="hot reload")
    assert rs.current_generation() == 2
    rows = conn.execute(
        "SELECT generation, reason FROM registry_audit ORDER BY generation"
    ).fetchall()
    assert [r["generation"] for r in rows] == [0, 1, 2]


def test_reload_after_restart_recovers_current_generation(tmp_path):
    db = str(tmp_path / "v3.db")
    conn = open_database(db)
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty()
    rs.increment(reason="r1")
    rs.increment(reason="r2")
    conn.close()

    conn2 = open_database(db)
    rs2 = RegistryState(conn2)
    assert rs2.current_generation() == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_registry_state.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `registry_state.py`**

```python
# ofi-lab-v3/storage/registry_state.py
"""Registry generation counter.

Every time the model registry is reloaded (Plan B), the generation
counter is incremented and a new row is appended to ``registry_audit``.
Plan A only writes the bootstrap row (generation 0). Predictions stamp
the *current* generation, which lets pending resolutions and decision
traces remain unambiguous after a hot reload.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Optional


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class RegistryState:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def bootstrap_if_empty(self, reason: str = "bootstrap") -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT count(*) AS n FROM registry_audit"
            ).fetchone()
            if row["n"] == 0:
                self._conn.execute(
                    "INSERT INTO registry_audit (ts, generation, reason, detail_json)"
                    " VALUES (?, 0, ?, NULL)",
                    (_utc_iso(), reason),
                )

    def current_generation(self) -> int:
        row = self._conn.execute(
            "SELECT MAX(generation) AS g FROM registry_audit"
        ).fetchone()
        return int(row["g"]) if row["g"] is not None else 0

    def increment(self, reason: str, detail: Optional[dict] = None) -> int:
        with self._lock:
            current = self.current_generation()
            new = current + 1
            self._conn.execute(
                "INSERT INTO registry_audit (ts, generation, reason, detail_json)"
                " VALUES (?, ?, ?, ?)",
                (_utc_iso(), new, reason,
                 json.dumps(detail) if detail is not None else None),
            )
            return new
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_registry_state.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/registry_state.py \
        ofi-lab-v3/tests/test_registry_state.py
git commit -m "v3: RegistryState — persistent generation counter"
```

---

### Task 6: `PolicySnapshot` — version counter + canonical hash of filter/threshold/Kelly config

**Files:**
- Create: `ofi-lab-v3/storage/policy_snapshot.py`
- Test: `ofi-lab-v3/tests/test_policy_snapshot.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_policy_snapshot.py
from storage.db import open_database, init_schema
from storage.policy_snapshot import PolicySnapshot, policy_canonical_form


SAMPLE = {
    "confidence_threshold": 0.55,
    "kelly_fraction": 0.25,
    "ev_threshold": 0.001,
    "circuit_breaker_drawdown": 50.0,
    "clob_divergence_min_edge": 0.02,
    "per_symbol_confidence": {"BTCUSDT": 0.55, "SOLUSDT": 0.58},
    "blackout_hours_utc": [21, 22, 23, 0, 1, 2, 3],
}


def test_canonical_form_round_trips(tmp_path):
    canon = policy_canonical_form(SAMPLE)
    assert canon["per_symbol_confidence"] == {"BTCUSDT": 0.55, "SOLUSDT": 0.58}
    # Lists of times/hours preserved in source order
    assert canon["blackout_hours_utc"] == [21, 22, 23, 0, 1, 2, 3]


def test_first_capture_writes_version_zero(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    ps = PolicySnapshot(conn)
    version, hashv = ps.capture(SAMPLE, initiated_by="bootstrap")
    assert version == 0
    assert len(hashv) == 64
    rows = conn.execute("SELECT * FROM policy_audit").fetchall()
    assert len(rows) == 1
    assert rows[0]["decision_policy_version"] == 0


def test_repeated_identical_capture_does_not_increment(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    ps = PolicySnapshot(conn)
    v1, h1 = ps.capture(SAMPLE, initiated_by="boot")
    v2, h2 = ps.capture(SAMPLE, initiated_by="boot")
    assert v1 == v2 == 0
    assert h1 == h2


def test_changed_value_increments_version(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    ps = PolicySnapshot(conn)
    v1, h1 = ps.capture(SAMPLE, initiated_by="boot")
    altered = dict(SAMPLE, confidence_threshold=0.56)
    v2, h2 = ps.capture(altered, initiated_by="api")
    assert v2 == v1 + 1
    assert h2 != h1


def test_current_returns_latest(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    ps = PolicySnapshot(conn)
    ps.capture(SAMPLE)
    altered = dict(SAMPLE, kelly_fraction=0.5)
    v, h = ps.capture(altered)
    assert ps.current() == (v, h)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_policy_snapshot.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `policy_snapshot.py`**

```python
# ofi-lab-v3/storage/policy_snapshot.py
"""Policy config snapshot + version counter.

A "policy" is the full set of filter thresholds, Kelly parameters,
EV thresholds, per-symbol overrides, blackout hours, and any other
runtime-mutable knob that influences a trade decision. The snapshot is
hashed and versioned so every prediction can carry both the hash (what
the policy was) and the version (when it changed relative to other
changes). The version counter only increments when the canonical
serialization actually changes.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Tuple

from storage.provenance import policy_config_hash


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def policy_canonical_form(config: dict) -> dict:
    """Return the canonical (hash-stable) form of a policy config dict.

    Currently a pass-through (``json.dumps(sort_keys=True)`` already
    handles dict ordering). Reserved as the single normalization point
    so future changes (rounding, alias unification, etc.) live in one
    place.
    """
    return dict(config)


class PolicySnapshot:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def current(self) -> Tuple[int, str]:
        row = self._conn.execute(
            "SELECT decision_policy_version, policy_config_hash FROM policy_audit"
            " ORDER BY decision_policy_version DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return (-1, "")  # signals never captured
        return (int(row["decision_policy_version"]), row["policy_config_hash"])

    def capture(self, config: dict, initiated_by: str = "system") -> Tuple[int, str]:
        canon = policy_canonical_form(config)
        h = policy_config_hash(canon)
        with self._lock:
            cur_v, cur_h = self.current()
            if cur_h == h:
                return (cur_v, cur_h)
            new_v = 0 if cur_v < 0 else cur_v + 1
            self._conn.execute(
                "INSERT INTO policy_audit"
                " (ts, decision_policy_version, policy_config_hash,"
                "  snapshot_json, initiated_by)"
                " VALUES (?, ?, ?, ?, ?)",
                (_utc_iso(), new_v, h, json.dumps(canon, sort_keys=True),
                 initiated_by),
            )
            return (new_v, h)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_policy_snapshot.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/policy_snapshot.py \
        ofi-lab-v3/tests/test_policy_snapshot.py
git commit -m "v3: PolicySnapshot — versioned canonical policy hash"
```

---

### Task 7: `init_db.py` script — bootstrap empty `/data/v3.db`

**Files:**
- Create: `ofi-lab-v3/scripts/init_db.py`
- Test: `ofi-lab-v3/tests/test_init_db_script.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_init_db_script.py
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "init_db.py"


def test_init_db_creates_schema(tmp_path):
    db = tmp_path / "v3.db"
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--bootstrap-reason", "test"],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, res.stderr
    assert db.exists()
    import sqlite3
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    for t in ("predictions", "paper_trades", "decision_traces",
              "calibration_outcomes", "registry_audit", "policy_audit"):
        assert t in tables
    audit = conn.execute(
        "SELECT generation, reason FROM registry_audit"
    ).fetchall()
    assert audit == [(0, "test")]
    conn.close()


def test_init_db_idempotent(tmp_path):
    db = tmp_path / "v3.db"
    cmd = [sys.executable, str(SCRIPT), "--db", str(db)]
    subprocess.run(cmd, check=True, capture_output=True)
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_init_db_script.py -v
```

Expected: `FileNotFoundError` for the script.

- [ ] **Step 3: Implement `init_db.py`**

```python
# ofi-lab-v3/scripts/init_db.py
"""Bootstrap an empty v3 SQLite database.

Usage:
    python -m scripts.init_db --db /data/v3.db --bootstrap-reason "v3 launch"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage.db import open_database, init_schema, DEFAULT_DB_PATH
from storage.registry_state import RegistryState


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DEFAULT_DB_PATH)
    p.add_argument("--bootstrap-reason", default="initial v3 boot")
    args = p.parse_args()

    conn = open_database(args.db)
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty(reason=args.bootstrap_reason)
    print(f"v3.db initialized at {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_init_db_script.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/scripts/init_db.py \
        ofi-lab-v3/tests/test_init_db_script.py
git commit -m "v3: init_db.py bootstrap script"
```

---

### Task 8: Config additions — `STORAGE_DB_PATH`, `EVALUATION_WINDOWS`, baseline name

**Files:**
- Modify: `ofi-lab-v3/config.py`
- Test: `ofi-lab-v3/tests/test_config_v3.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_config_v3.py
import config


def test_storage_db_path_default():
    assert config.STORAGE_DB_PATH == "/data/v3.db"


def test_evaluation_windows_complete():
    # All four live market windows; lifecycle code subtracts the native
    # horizon at runtime.
    assert sorted(config.EVALUATION_WINDOWS) == [300, 900, 1800, 3600]


def test_baseline_model_name_constant():
    assert config.BASELINE_MODEL_NAME == "900s_btc_v3_20260315"


def test_baseline_protected_flag():
    assert config.BASELINE_PROTECTED is True


def test_warmup_seconds_default():
    assert config.WARMUP_SECONDS == 1800  # 30 minutes
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_config_v3.py -v
```

Expected: `AttributeError: module 'config' has no attribute 'STORAGE_DB_PATH'`.

- [ ] **Step 3: Add the constants to `config.py`**

Append after the existing `MAX_MODELS_TO_KEEP` line:

```python
# ---------- v3 storage / lifecycle / multi-window ----------

STORAGE_DB_PATH = "/data/v3.db"

# Live market windows that v3 tracks evaluation rows for. The native
# horizon for a given model is *excluded* from this set at scheduling
# time so a 900s model only generates evaluation rows at 300/1800/3600.
EVALUATION_WINDOWS = [300, 900, 1800, 3600]

# Golden baseline model. Plan B enforces baseline-removal protection in
# the registry; Plan A only references the name.
BASELINE_MODEL_NAME = "900s_btc_v3_20260315"
BASELINE_PROTECTED = True

# Warmup window applied at every container start. Predictions made
# before now_ms exceeds boot_ts_ms + WARMUP_SECONDS * 1000 are stamped
# warmup=1 and excluded from calibration / decay / Kalshi dispatch.
WARMUP_SECONDS = 1800
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_config_v3.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/config.py ofi-lab-v3/tests/test_config_v3.py
git commit -m "v3: config — STORAGE_DB_PATH, EVALUATION_WINDOWS, baseline, warmup"
```

---

### Task 9: Native + evaluation row planner — `storage/window_planner.py`

**Files:**
- Create: `ofi-lab-v3/storage/window_planner.py`
- Test: `ofi-lab-v3/tests/test_window_planner.py`

This module is small but critical: it is the single source of truth for "given a model whose native training horizon is X seconds, which `(market_window_seconds, resolution_type)` rows should be inserted at this boundary, and at what `ts_resolve_at_ms` does each one resolve?"

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_window_planner.py
import pytest

from storage.window_planner import plan_resolution_rows, ResolutionRow


def test_h300_baseline_emits_native_900_plus_three_evaluations():
    rows = plan_resolution_rows(
        boundary_ms=1_700_000_000_000,
        training_horizon_seconds=900,
        evaluation_windows=[300, 900, 1800, 3600],
    )
    # 1 native + 3 evaluation (900 excluded from evaluation set)
    assert len(rows) == 4
    native = [r for r in rows if r.resolution_type == "native"]
    evals = [r for r in rows if r.resolution_type == "evaluation"]
    assert len(native) == 1
    assert native[0].market_window_seconds == 900
    assert native[0].ts_resolve_at_ms == 1_700_000_000_000 + 900_000
    eval_windows = sorted(r.market_window_seconds for r in evals)
    assert eval_windows == [300, 1800, 3600]


def test_h60_emits_native_60_plus_four_evaluations():
    rows = plan_resolution_rows(
        boundary_ms=1_700_000_000_000,
        training_horizon_seconds=60,
        evaluation_windows=[300, 900, 1800, 3600],
    )
    assert len(rows) == 5
    native = next(r for r in rows if r.resolution_type == "native")
    assert native.market_window_seconds == 60
    assert native.ts_resolve_at_ms == 1_700_000_000_000 + 60_000


def test_native_horizon_already_in_evaluation_set_is_not_duplicated():
    rows = plan_resolution_rows(
        boundary_ms=1_700_000_000_000,
        training_horizon_seconds=300,
        evaluation_windows=[300, 900],
    )
    # Native at 300, evaluation at 900 only.
    assert len(rows) == 2
    assert sum(1 for r in rows if r.market_window_seconds == 300) == 1


def test_resolve_at_ms_is_per_window():
    rows = plan_resolution_rows(
        boundary_ms=2_000_000,
        training_horizon_seconds=900,
        evaluation_windows=[300, 1800],
    )
    by_w = {r.market_window_seconds: r.ts_resolve_at_ms for r in rows}
    assert by_w[900] == 2_000_000 + 900_000
    assert by_w[300] == 2_000_000 + 300_000
    assert by_w[1800] == 2_000_000 + 1_800_000


def test_invalid_horizon_rejected():
    with pytest.raises(ValueError):
        plan_resolution_rows(
            boundary_ms=0,
            training_horizon_seconds=0,
            evaluation_windows=[900],
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_window_planner.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `window_planner.py`**

```python
# ofi-lab-v3/storage/window_planner.py
"""Multi-window resolution row planner.

Every prediction emits one ``resolution_type='native'`` row at the
model's native training horizon and zero or more
``resolution_type='evaluation'`` rows at the other live market windows.
Lifecycle, decay, calibration, and rolling-EV queries filter on
``resolution_type='native'`` so evaluation rows never contaminate core
model statistics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class ResolutionRow:
    market_window_seconds: int
    resolution_type: str  # 'native' | 'evaluation'
    ts_resolve_at_ms: int


def plan_resolution_rows(
    boundary_ms: int,
    training_horizon_seconds: int,
    evaluation_windows: Iterable[int],
) -> List[ResolutionRow]:
    if training_horizon_seconds <= 0:
        raise ValueError("training_horizon_seconds must be positive")
    rows: List[ResolutionRow] = [
        ResolutionRow(
            market_window_seconds=training_horizon_seconds,
            resolution_type="native",
            ts_resolve_at_ms=boundary_ms + training_horizon_seconds * 1000,
        )
    ]
    for w in evaluation_windows:
        if w == training_horizon_seconds:
            continue  # native already emitted
        rows.append(
            ResolutionRow(
                market_window_seconds=w,
                resolution_type="evaluation",
                ts_resolve_at_ms=boundary_ms + w * 1000,
            )
        )
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_window_planner.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/window_planner.py \
        ofi-lab-v3/tests/test_window_planner.py
git commit -m "v3: window_planner — native + evaluation row scheduler"
```

---

### Task 10: `SQLiteLedger` write API — predictions

**Files:**
- Create: `ofi-lab-v3/storage/sqlite_ledger.py`
- Test: `ofi-lab-v3/tests/test_sqlite_ledger.py`

The `SQLiteLedger` is the SQLite-backed peer of `trading/ledger.py`. Its public methods are designed so the paper trader can be migrated by changing instantiation only — method signatures preserve `prediction_id` / `trade_id` return contracts.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_sqlite_ledger.py
import pytest

from storage.db import open_database, init_schema
from storage.policy_snapshot import PolicySnapshot
from storage.provenance import ProvenanceEnvelope
from storage.registry_state import RegistryState
from storage.sqlite_ledger import SQLiteLedger
from storage.window_planner import plan_resolution_rows


@pytest.fixture
def ledger(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    RegistryState(conn).bootstrap_if_empty()
    PolicySnapshot(conn).capture({"confidence_threshold": 0.55})
    return SQLiteLedger(conn), conn


def _envelope(**overrides):
    base = dict(
        model_name="900s_btc_v3_20260315",
        model_artifact_hash="a"*64,
        feature_names_hash="b"*64,
        feature_version="v3",
        training_horizon_seconds=900,
        train_window_start="2025-04-01",
        train_window_end="2026-03-15",
        train_cutoff="2026-03-15",
        registry_load_generation=0,
        policy_config_hash="c"*64,
        decision_policy_version=0,
        calibration_map_hash="d"*64,
        platform="paper",
    )
    base.update(overrides)
    return ProvenanceEnvelope(**base)


def test_log_prediction_inserts_one_native_plus_evaluations(ledger):
    led, conn = ledger
    boundary = 1_700_000_000_000
    rows = plan_resolution_rows(boundary, 900, [300, 900, 1800, 3600])
    pid = led.log_prediction_set(
        envelope=_envelope(),
        symbol="BTCUSDT",
        ts_model_ran_ms=boundary - 1000,
        ts_contract_open_ms=boundary,
        rows=rows,
        pred_proba_raw=0.54,
        pred_proba_calibrated=0.51,
        pred_direction="up",
        above_threshold=False,
        warmup=False,
        platform="paper",
        p_market=0.50,
        utc_hour=12, day_of_week=2, is_weekend=0,
        relative_spread=0.00012,
    )
    inserted = conn.execute("SELECT * FROM predictions").fetchall()
    assert len(inserted) == 4
    by_window = {r["market_window_seconds"]: r for r in inserted}
    assert by_window[900]["resolution_type"] == "native"
    for w in (300, 1800, 3600):
        assert by_window[w]["resolution_type"] == "evaluation"
    # All rows share the same group prediction_id prefix and base
    # provenance.
    base_ids = {r["prediction_id"].rsplit("_", 1)[0] for r in inserted}
    assert len(base_ids) == 1
    assert pid == sorted(r["prediction_id"] for r in inserted
                          if r["resolution_type"] == "native")[0]


def test_idempotent_index_blocks_double_score_at_same_generation(ledger):
    import sqlite3
    led, conn = ledger
    boundary = 1_700_000_000_000
    rows = plan_resolution_rows(boundary, 900, [900])
    led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=boundary, ts_contract_open_ms=boundary,
        rows=rows, pred_proba_raw=0.5, pred_proba_calibrated=0.5,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper",
    )
    with pytest.raises(sqlite3.IntegrityError):
        led.log_prediction_set(
            envelope=_envelope(), symbol="BTCUSDT",
            ts_model_ran_ms=boundary, ts_contract_open_ms=boundary,
            rows=rows, pred_proba_raw=0.6, pred_proba_calibrated=0.6,
            pred_direction="up", above_threshold=False, warmup=False,
            platform="paper",
        )


def test_new_generation_can_rescore_same_boundary(ledger):
    led, conn = ledger
    boundary = 1_700_000_000_000
    rows = plan_resolution_rows(boundary, 900, [900])
    led.log_prediction_set(
        envelope=_envelope(registry_load_generation=0),
        symbol="BTCUSDT",
        ts_model_ran_ms=boundary, ts_contract_open_ms=boundary,
        rows=rows, pred_proba_raw=0.5, pred_proba_calibrated=0.5,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper",
    )
    led.log_prediction_set(
        envelope=_envelope(registry_load_generation=1),
        symbol="BTCUSDT",
        ts_model_ran_ms=boundary, ts_contract_open_ms=boundary,
        rows=rows, pred_proba_raw=0.6, pred_proba_calibrated=0.6,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper",
    )
    n = conn.execute("SELECT count(*) AS n FROM predictions").fetchone()["n"]
    assert n == 2  # one row each generation


def test_warmup_flag_is_persisted(ledger):
    led, conn = ledger
    rows = plan_resolution_rows(1_000_000, 900, [900])
    led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=1_000_000, ts_contract_open_ms=1_000_000,
        rows=rows, pred_proba_raw=0.5, pred_proba_calibrated=0.5,
        pred_direction="up", above_threshold=False, warmup=True,
        platform="paper",
    )
    row = conn.execute("SELECT warmup FROM predictions").fetchone()
    assert row["warmup"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_sqlite_ledger.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `sqlite_ledger.py` (predictions only — trades + resolutions added in T11/T12)**

```python
# ofi-lab-v3/storage/sqlite_ledger.py
"""SQLite-backed ledger.

Drop-in peer of ``trading/ledger.py``. The paper trader is rewired to
call ``SQLiteLedger`` in T13. The legacy JSONL ledger is preserved
unchanged for migration use.

This module is intentionally narrow: it knows how to **persist** rows
that already carry a complete provenance envelope. It does not compute
hashes, schedule resolutions, or evaluate filters; those are the paper
trader's responsibility.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from typing import Iterable, Optional

from storage.provenance import ProvenanceEnvelope
from storage.window_planner import ResolutionRow


def _new_id_prefix() -> str:
    return uuid.uuid4().hex


class SQLiteLedger:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Predictions
    # ------------------------------------------------------------------
    def log_prediction_set(
        self,
        *,
        envelope: ProvenanceEnvelope,
        symbol: str,
        ts_model_ran_ms: int,
        ts_contract_open_ms: int,
        rows: Iterable[ResolutionRow],
        pred_proba_raw: float,
        pred_proba_calibrated: float,
        pred_direction: str,
        above_threshold: bool,
        warmup: bool,
        platform: str,
        p_market: Optional[float] = None,
        p_model_minus_market: Optional[float] = None,
        utc_hour: Optional[int] = None,
        day_of_week: Optional[int] = None,
        is_weekend: Optional[int] = None,
        relative_spread: Optional[float] = None,
        trade_eligible: bool = True,
    ) -> str:
        """Insert one native + N evaluation rows for a single boundary.

        Returns the **native** row's ``prediction_id``. Evaluation rows
        share the same prefix with a window suffix.
        """
        prefix = _new_id_prefix()
        native_id: Optional[str] = None
        with self._lock:
            for row in rows:
                pid = f"{prefix}_{row.market_window_seconds}_{row.resolution_type[0]}"
                if row.resolution_type == "native":
                    native_id = pid
                self._conn.execute(
                    "INSERT INTO predictions ("
                    " prediction_id, model_name, model_artifact_hash,"
                    " feature_names_hash, feature_version,"
                    " training_horizon_seconds, train_window_start,"
                    " train_window_end, train_cutoff,"
                    " registry_load_generation,"
                    " policy_config_hash, decision_policy_version,"
                    " calibration_map_hash,"
                    " symbol, market_window_seconds, resolution_type,"
                    " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
                    " pred_proba_raw, pred_proba_calibrated, pred_direction,"
                    " above_threshold, warmup, trade_eligible, platform,"
                    " p_market, p_model_minus_market,"
                    " utc_hour, day_of_week, is_weekend, relative_spread"
                    ") VALUES ("
                    " ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?"
                    ")",
                    (
                        pid,
                        envelope.model_name, envelope.model_artifact_hash,
                        envelope.feature_names_hash, envelope.feature_version,
                        envelope.training_horizon_seconds,
                        envelope.train_window_start, envelope.train_window_end,
                        envelope.train_cutoff, envelope.registry_load_generation,
                        envelope.policy_config_hash, envelope.decision_policy_version,
                        envelope.calibration_map_hash,
                        symbol, row.market_window_seconds, row.resolution_type,
                        ts_model_ran_ms, ts_contract_open_ms, row.ts_resolve_at_ms,
                        float(pred_proba_raw), float(pred_proba_calibrated),
                        pred_direction, int(above_threshold),
                        int(warmup), int(trade_eligible), platform,
                        p_market, p_model_minus_market,
                        utc_hour, day_of_week, is_weekend, relative_spread,
                    ),
                )
        assert native_id is not None, "rows must include exactly one native row"
        return native_id
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_sqlite_ledger.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/sqlite_ledger.py \
        ofi-lab-v3/tests/test_sqlite_ledger.py
git commit -m "v3: SQLiteLedger.log_prediction_set — native + evaluation rows + idempotency"
```

---

### Task 11: `SQLiteLedger.log_paper_trade` and `log_compact_decision`

**Files:**
- Modify: `ofi-lab-v3/storage/sqlite_ledger.py`
- Modify: `ofi-lab-v3/tests/test_sqlite_ledger.py`

- [ ] **Step 1: Extend the test**

Append to `tests/test_sqlite_ledger.py`:

```python
def test_log_paper_trade_links_to_prediction(ledger):
    led, conn = ledger
    rows = plan_resolution_rows(1_700_000_000_000, 900, [900])
    pid = led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=1_700_000_000_000,
        ts_contract_open_ms=1_700_000_000_000,
        rows=rows, pred_proba_raw=0.55, pred_proba_calibrated=0.53,
        pred_direction="up", above_threshold=True, warmup=False,
        platform="paper",
    )
    tid = led.log_paper_trade(
        prediction_id=pid,
        envelope=_envelope(),
        symbol="BTCUSDT",
        market_window_seconds=900,
        resolution_type="native",
        ts_model_ran_ms=1_700_000_000_000,
        ts_contract_open_ms=1_700_000_000_000,
        ts_resolve_at_ms=1_700_000_000_000 + 900_000,
        pred_proba_raw=0.55,
        pred_proba_calibrated=0.53,
        pred_direction="up",
        confidence_threshold_used=0.52,
        simulated_stake_usdc=10.0,
        decision_outcome="executed",
        decision_reason=None,
        ev_estimate=0.018,
        kelly_fraction_capped=0.25,
        final_size_usdc=10.0,
        order_type="maker",
        warmup=False,
        platform="paper",
    )
    row = conn.execute(
        "SELECT prediction_id, decision_outcome, ev_estimate"
        " FROM paper_trades WHERE trade_id = ?", (tid,)
    ).fetchone()
    assert row["prediction_id"] == pid
    assert row["decision_outcome"] == "executed"
    assert abs(row["ev_estimate"] - 0.018) < 1e-9


def test_log_compact_decision_writes_inline_fields(ledger):
    led, conn = ledger
    rows = plan_resolution_rows(1_000_000, 900, [900])
    pid = led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=1_000_000, ts_contract_open_ms=1_000_000,
        rows=rows, pred_proba_raw=0.55, pred_proba_calibrated=0.53,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper",
    )
    led.log_compact_decision(
        prediction_id=pid,
        decision_outcome="suppressed",
        decision_reason="below_confidence",
        ev_estimate=-0.001,
        kelly_fraction_capped=0.0,
        final_size_usdc=0.0,
        order_type="skipped",
    )
    row = conn.execute(
        "SELECT decision_outcome, decision_reason FROM predictions"
        " WHERE prediction_id = ?", (pid,)
    ).fetchone()
    assert row["decision_outcome"] == "suppressed"
    assert row["decision_reason"] == "below_confidence"
```

- [ ] **Step 2: Run tests to confirm new ones fail**

```bash
python -m pytest tests/test_sqlite_ledger.py -v
```

Expected: 2 failures (`AttributeError: log_paper_trade`, `log_compact_decision`).

- [ ] **Step 3: Implement both methods**

Append to `storage/sqlite_ledger.py`:

```python
    # ------------------------------------------------------------------
    # Paper trades
    # ------------------------------------------------------------------
    def log_paper_trade(
        self,
        *,
        prediction_id: str,
        envelope: ProvenanceEnvelope,
        symbol: str,
        market_window_seconds: int,
        resolution_type: str,
        ts_model_ran_ms: int,
        ts_contract_open_ms: int,
        ts_resolve_at_ms: int,
        pred_proba_raw: float,
        pred_proba_calibrated: float,
        pred_direction: str,
        confidence_threshold_used: float,
        simulated_stake_usdc: float,
        decision_outcome: str,
        decision_reason: Optional[str],
        ev_estimate: Optional[float],
        kelly_fraction_capped: Optional[float],
        final_size_usdc: Optional[float],
        order_type: Optional[str],
        warmup: bool,
        platform: str,
        p_market: Optional[float] = None,
        suppressed_reason: Optional[str] = None,
        filter_mode: Optional[str] = None,
    ) -> str:
        trade_id = _new_id_prefix() + "_t"
        with self._lock:
            self._conn.execute(
                "INSERT INTO paper_trades ("
                " trade_id, prediction_id,"
                " model_name, model_artifact_hash, policy_config_hash,"
                " decision_policy_version, calibration_map_hash,"
                " registry_load_generation, feature_version,"
                " training_horizon_seconds,"
                " symbol, market_window_seconds, resolution_type,"
                " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
                " pred_proba_raw, pred_proba_calibrated, pred_direction,"
                " confidence_threshold_used, simulated_stake_usdc,"
                " p_market, suppressed_reason, filter_mode,"
                " warmup, platform,"
                " decision_outcome, decision_reason,"
                " ev_estimate, kelly_fraction_capped,"
                " final_size_usdc, order_type"
                ") VALUES ("
                " ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?"
                ")",
                (
                    trade_id, prediction_id,
                    envelope.model_name, envelope.model_artifact_hash,
                    envelope.policy_config_hash, envelope.decision_policy_version,
                    envelope.calibration_map_hash,
                    envelope.registry_load_generation, envelope.feature_version,
                    envelope.training_horizon_seconds,
                    symbol, market_window_seconds, resolution_type,
                    ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,
                    float(pred_proba_raw), float(pred_proba_calibrated),
                    pred_direction, float(confidence_threshold_used),
                    simulated_stake_usdc, p_market, suppressed_reason, filter_mode,
                    int(warmup), platform,
                    decision_outcome, decision_reason,
                    ev_estimate, kelly_fraction_capped,
                    final_size_usdc, order_type,
                ),
            )
        return trade_id

    # ------------------------------------------------------------------
    # Compact decision (inline fields on the prediction row)
    # ------------------------------------------------------------------
    def log_compact_decision(
        self,
        *,
        prediction_id: str,
        decision_outcome: str,
        decision_reason: Optional[str],
        ev_estimate: Optional[float],
        kelly_fraction_capped: Optional[float],
        final_size_usdc: Optional[float],
        order_type: Optional[str],
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE predictions SET"
                "  decision_outcome = ?,"
                "  decision_reason = ?,"
                "  ev_estimate = ?,"
                "  kelly_fraction_capped = ?,"
                "  final_size_usdc = ?,"
                "  order_type = ?"
                " WHERE prediction_id = ?",
                (decision_outcome, decision_reason, ev_estimate,
                 kelly_fraction_capped, final_size_usdc, order_type,
                 prediction_id),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_sqlite_ledger.py -v
```

Expected: all 6 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/sqlite_ledger.py \
        ofi-lab-v3/tests/test_sqlite_ledger.py
git commit -m "v3: SQLiteLedger — log_paper_trade + log_compact_decision"
```

---

### Task 12: Verbose `DecisionTraceWriter`

**Files:**
- Create: `ofi-lab-v3/storage/decision_trace.py`
- Test: `ofi-lab-v3/tests/test_decision_trace.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_decision_trace.py
import json
import pytest

from storage.db import open_database, init_schema
from storage.decision_trace import DecisionTraceWriter, FilterEval


def setup_db(tmp_path):
    conn = open_database(str(tmp_path / "v3.db"))
    init_schema(conn)
    return conn


def test_write_emits_one_row_with_all_filters(tmp_path):
    conn = setup_db(tmp_path)
    w = DecisionTraceWriter(conn)
    w.write(
        prediction_id="pid_900_n",
        filters=[
            FilterEval("confidence", threshold=0.55, input_value=0.54, passed=False),
            FilterEval("ev_threshold", threshold=0.0, input_value=-0.001, passed=False),
        ],
        kelly_raw=0.1, kelly_capped=0.05, bankroll_used=200.0,
        per_trade_cap_usdc=5.0,
        fee_model="polymarket", fee_amount=0.018,
        platform_gate={"kalshi_allow_list": False},
        warmup=False, consensus_data=None,
        policy_config_hash="c"*64, calibration_map_hash="d"*64,
        registry_load_generation=0,
    )
    row = conn.execute("SELECT * FROM decision_traces").fetchone()
    parsed = json.loads(row["filters_json"])
    assert {f["name"] for f in parsed} == {"confidence", "ev_threshold"}
    confidence = next(f for f in parsed if f["name"] == "confidence")
    assert confidence["threshold"] == 0.55
    assert confidence["input_value"] == 0.54
    assert confidence["passed"] == 0
    assert row["fee_model"] == "polymarket"
    assert row["kelly_raw"] == 0.1
    assert row["kelly_capped"] == 0.05


def test_write_serializes_consensus_payload(tmp_path):
    conn = setup_db(tmp_path)
    w = DecisionTraceWriter(conn)
    w.write(
        prediction_id="pid_900_n", filters=[],
        kelly_raw=None, kelly_capped=None, bankroll_used=None,
        per_trade_cap_usdc=None, fee_model="kalshi_taker", fee_amount=None,
        platform_gate=None, warmup=True,
        consensus_data={"models": ["a", "b"], "agreement": False},
        policy_config_hash="c"*64, calibration_map_hash="d"*64,
        registry_load_generation=2,
    )
    row = conn.execute("SELECT * FROM decision_traces").fetchone()
    assert json.loads(row["consensus_data_json"])["agreement"] is False
    assert row["registry_load_generation"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_decision_trace.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `decision_trace.py`**

```python
# ofi-lab-v3/storage/decision_trace.py
"""Verbose decision trace writer.

Append-only forensic log. Every prediction-with-trade-decision (whether
executed, suppressed, or gated) writes a row capturing the full filter
chain, Kelly math, fee computation, and platform gating context. Linked
to the prediction by ``prediction_id``.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class FilterEval:
    name: str
    threshold: Optional[float]
    input_value: Optional[float]
    passed: bool

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "threshold": self.threshold,
            "input_value": self.input_value,
            "passed": int(self.passed),
        }


class DecisionTraceWriter:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def write(
        self,
        *,
        prediction_id: str,
        filters: Iterable[FilterEval],
        kelly_raw: Optional[float],
        kelly_capped: Optional[float],
        bankroll_used: Optional[float],
        per_trade_cap_usdc: Optional[float],
        fee_model: str,
        fee_amount: Optional[float],
        platform_gate: Optional[dict],
        warmup: bool,
        consensus_data: Optional[dict],
        policy_config_hash: str,
        calibration_map_hash: str,
        registry_load_generation: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO decision_traces ("
                " prediction_id, ts, filters_json,"
                " kelly_raw, kelly_capped, bankroll_used, per_trade_cap_usdc,"
                " fee_model, fee_amount, platform_gate_json,"
                " warmup, consensus_data_json,"
                " policy_config_hash, calibration_map_hash,"
                " registry_load_generation"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    prediction_id, _utc_iso(),
                    json.dumps([f.to_dict() for f in filters]),
                    kelly_raw, kelly_capped, bankroll_used, per_trade_cap_usdc,
                    fee_model, fee_amount,
                    json.dumps(platform_gate) if platform_gate is not None else None,
                    int(warmup),
                    json.dumps(consensus_data) if consensus_data is not None else None,
                    policy_config_hash, calibration_map_hash,
                    registry_load_generation,
                ),
            )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_decision_trace.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/decision_trace.py \
        ofi-lab-v3/tests/test_decision_trace.py
git commit -m "v3: DecisionTraceWriter — verbose forensic trace"
```

---

### Task 13: Resolution writers — `record_native_resolution`, `record_evaluation_resolution`, `record_trade_resolution`

**Files:**
- Modify: `ofi-lab-v3/storage/sqlite_ledger.py`
- Modify: `ofi-lab-v3/tests/test_sqlite_ledger.py`

- [ ] **Step 1: Extend the test**

Append to `tests/test_sqlite_ledger.py`:

```python
def test_record_native_resolution_updates_prediction_and_calibration(ledger):
    led, conn = ledger
    rows = plan_resolution_rows(1_000_000, 900, [900])
    pid = led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=1_000_000, ts_contract_open_ms=1_000_000,
        rows=rows, pred_proba_raw=0.55, pred_proba_calibrated=0.53,
        pred_direction="up", above_threshold=True, warmup=False,
        platform="paper",
    )
    led.record_native_resolution(
        prediction_id=pid,
        ts_resolved_ms=1_900_000,
        price_at_open=60_000.0,
        price_at_close=60_100.0,
        contract_result="up",
        prediction_correct=True,
    )
    row = conn.execute(
        "SELECT resolved, contract_result, prediction_correct,"
        " price_at_open, price_at_close FROM predictions WHERE prediction_id=?",
        (pid,)).fetchone()
    assert row["resolved"] == 1
    assert row["contract_result"] == "up"
    assert row["prediction_correct"] == 1
    assert row["price_at_open"] == 60_000.0
    cal = conn.execute(
        "SELECT * FROM calibration_outcomes WHERE prediction_id=?",
        (pid,)).fetchone()
    assert cal is not None
    assert cal["resolution_type"] == "native"
    assert cal["won"] == 1


def test_record_evaluation_resolution_does_not_write_calibration(ledger):
    led, conn = ledger
    rows = plan_resolution_rows(1_000_000, 900, [300, 900])
    led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=1_000_000, ts_contract_open_ms=1_000_000,
        rows=rows, pred_proba_raw=0.55, pred_proba_calibrated=0.53,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper",
    )
    eval_pid = conn.execute(
        "SELECT prediction_id FROM predictions WHERE resolution_type='evaluation'"
    ).fetchone()["prediction_id"]
    led.record_evaluation_resolution(
        prediction_id=eval_pid, ts_resolved_ms=1_300_000,
        price_at_open=60_000.0, price_at_close=60_050.0,
        contract_result="up", prediction_correct=True,
    )
    cal = conn.execute(
        "SELECT count(*) AS n FROM calibration_outcomes"
    ).fetchone()
    assert cal["n"] == 0  # evaluation rows must never feed calibration


def test_record_trade_resolution_updates_pnl(ledger):
    led, conn = ledger
    rows = plan_resolution_rows(1_000_000, 900, [900])
    pid = led.log_prediction_set(
        envelope=_envelope(), symbol="BTCUSDT",
        ts_model_ran_ms=1_000_000, ts_contract_open_ms=1_000_000,
        rows=rows, pred_proba_raw=0.55, pred_proba_calibrated=0.53,
        pred_direction="up", above_threshold=True, warmup=False,
        platform="paper",
    )
    tid = led.log_paper_trade(
        prediction_id=pid, envelope=_envelope(), symbol="BTCUSDT",
        market_window_seconds=900, resolution_type="native",
        ts_model_ran_ms=1_000_000, ts_contract_open_ms=1_000_000,
        ts_resolve_at_ms=1_900_000,
        pred_proba_raw=0.55, pred_proba_calibrated=0.53,
        pred_direction="up", confidence_threshold_used=0.52,
        simulated_stake_usdc=10.0, decision_outcome="executed",
        decision_reason=None, ev_estimate=0.018,
        kelly_fraction_capped=0.25, final_size_usdc=10.0,
        order_type="maker", warmup=False, platform="paper",
    )
    led.record_trade_resolution(
        trade_id=tid, ts_resolved_ms=1_900_000,
        price_at_close=60_100.0, contract_result="up",
        prediction_correct=True, gross_pnl=8.07,
        fee_paid=0.18, net_pnl=7.89,
        trade_result="win", pnl_method="binary_polymarket",
    )
    row = conn.execute(
        "SELECT resolved, net_pnl, trade_result FROM paper_trades WHERE trade_id=?",
        (tid,)).fetchone()
    assert row["resolved"] == 1
    assert abs(row["net_pnl"] - 7.89) < 1e-9
    assert row["trade_result"] == "win"
```

- [ ] **Step 2: Run tests to confirm new ones fail**

Expected: 3 `AttributeError`s.

- [ ] **Step 3: Implement the resolution writers**

Append to `storage/sqlite_ledger.py`:

```python
    # ------------------------------------------------------------------
    # Resolution writers
    # ------------------------------------------------------------------
    def record_native_resolution(
        self,
        *,
        prediction_id: str,
        ts_resolved_ms: int,
        price_at_open: float,
        price_at_close: float,
        contract_result: str,
        prediction_correct: bool,
    ) -> None:
        """Resolve a native row and append a calibration outcome.

        Calibration outcomes are written **only** for native rows.
        Evaluation rows resolve via ``record_evaluation_resolution`` and
        never touch ``calibration_outcomes``.
        """
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT model_name, symbol, market_window_seconds,"
                " resolution_type, pred_proba_calibrated, warmup,"
                " regime_volatility, regime_liquidity"
                " FROM predictions WHERE prediction_id = ?",
                (prediction_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown prediction_id {prediction_id!r}")
            if row["resolution_type"] != "native":
                raise ValueError(
                    f"record_native_resolution called on "
                    f"{row['resolution_type']!r} row {prediction_id!r}"
                )
            self._conn.execute(
                "UPDATE predictions SET"
                "  resolved = 1, ts_resolved_ms = ?,"
                "  price_at_open = ?, price_at_close = ?,"
                "  contract_result = ?, prediction_correct = ?"
                " WHERE prediction_id = ?",
                (ts_resolved_ms, price_at_open, price_at_close,
                 contract_result, int(prediction_correct), prediction_id),
            )
            self._conn.execute(
                "INSERT INTO calibration_outcomes ("
                " ts, prediction_id, model_name, symbol,"
                " market_window_seconds, resolution_type,"
                " side_conf, won, warmup,"
                " regime_volatility, regime_liquidity"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_utc_iso_seconds(ts_resolved_ms), prediction_id,
                 row["model_name"], row["symbol"],
                 row["market_window_seconds"], "native",
                 float(row["pred_proba_calibrated"]),
                 int(prediction_correct), row["warmup"],
                 row["regime_volatility"], row["regime_liquidity"]),
            )

    def record_evaluation_resolution(
        self,
        *,
        prediction_id: str,
        ts_resolved_ms: int,
        price_at_open: float,
        price_at_close: float,
        contract_result: str,
        prediction_correct: bool,
    ) -> None:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT resolution_type FROM predictions WHERE prediction_id = ?",
                (prediction_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown prediction_id {prediction_id!r}")
            if row["resolution_type"] != "evaluation":
                raise ValueError(
                    f"record_evaluation_resolution called on "
                    f"{row['resolution_type']!r} row {prediction_id!r}"
                )
            self._conn.execute(
                "UPDATE predictions SET"
                "  resolved = 1, ts_resolved_ms = ?,"
                "  price_at_open = ?, price_at_close = ?,"
                "  contract_result = ?, prediction_correct = ?"
                " WHERE prediction_id = ?",
                (ts_resolved_ms, price_at_open, price_at_close,
                 contract_result, int(prediction_correct), prediction_id),
            )

    def record_trade_resolution(
        self,
        *,
        trade_id: str,
        ts_resolved_ms: int,
        price_at_close: float,
        contract_result: str,
        prediction_correct: bool,
        gross_pnl: float,
        fee_paid: float,
        net_pnl: float,
        trade_result: str,
        pnl_method: str,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE paper_trades SET"
                "  resolved = 1, ts_resolved_ms = ?,"
                "  price_at_close = ?, contract_result = ?,"
                "  prediction_correct = ?,"
                "  gross_pnl = ?, fee_paid = ?, net_pnl = ?,"
                "  trade_result = ?, pnl_method = ?"
                " WHERE trade_id = ?",
                (ts_resolved_ms, price_at_close, contract_result,
                 int(prediction_correct), gross_pnl, fee_paid, net_pnl,
                 trade_result, pnl_method, trade_id),
            )
```

Add helper near the top of the file (below `_new_id_prefix`):

```python
from datetime import datetime, timezone

def _utc_iso_seconds(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_sqlite_ledger.py -v
```

Expected: all 9 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/sqlite_ledger.py \
        ofi-lab-v3/tests/test_sqlite_ledger.py
git commit -m "v3: SQLiteLedger resolution writers — native, evaluation, trade"
```

---

### Task 14: `PendingResolutionQueue` keyed on (boundary, model, symbol, window, generation)

**Files:**
- Create: `ofi-lab-v3/storage/pending_queue.py`
- Test: `ofi-lab-v3/tests/test_pending_queue.py`

The legacy `pending_resolutions.json` and `pending_pred_resolutions.json` are flat arrays. v3 keys pending entries by the same composite that the idempotency index uses, so a hot reload that increments the generation can prune stale entries cleanly (Steering 10h primitive). The full hot-reload-driven cleanup ships in Plan B, but the queue must already support it.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_pending_queue.py
import json
from pathlib import Path

import pytest

from storage.pending_queue import PendingResolutionQueue, PendingEntry


def test_enqueue_dequeue_roundtrip(tmp_path):
    q = PendingResolutionQueue(tmp_path / "pending.json")
    e = PendingEntry(
        prediction_id="pid_900_n",
        boundary_ms=1_000_000,
        model_name="900s_btc_v3_20260315",
        symbol="BTCUSDT",
        market_window_seconds=900,
        registry_load_generation=0,
        ts_resolve_at_ms=1_900_000,
        resolution_type="native",
        price_at_open=60_000.0,
    )
    q.enqueue(e)
    q.persist()

    q2 = PendingResolutionQueue(tmp_path / "pending.json")
    q2.load()
    found = list(q2.iter_ripe(now_ms=2_000_000))
    assert len(found) == 1
    assert found[0].prediction_id == "pid_900_n"


def test_iter_ripe_excludes_future(tmp_path):
    q = PendingResolutionQueue(tmp_path / "pending.json")
    q.enqueue(PendingEntry(
        prediction_id="pid_a", boundary_ms=1_000_000,
        model_name="m", symbol="s", market_window_seconds=900,
        registry_load_generation=0, ts_resolve_at_ms=2_000_000,
        resolution_type="native", price_at_open=1.0,
    ))
    q.enqueue(PendingEntry(
        prediction_id="pid_b", boundary_ms=1_000_000,
        model_name="m", symbol="s", market_window_seconds=300,
        registry_load_generation=0, ts_resolve_at_ms=1_300_000,
        resolution_type="evaluation", price_at_open=1.0,
    ))
    ripe = list(q.iter_ripe(now_ms=1_400_000))
    assert [r.prediction_id for r in ripe] == ["pid_b"]


def test_remove_after_resolution(tmp_path):
    q = PendingResolutionQueue(tmp_path / "pending.json")
    q.enqueue(PendingEntry(
        prediction_id="pid_a", boundary_ms=1_000_000,
        model_name="m", symbol="s", market_window_seconds=900,
        registry_load_generation=0, ts_resolve_at_ms=1_900_000,
        resolution_type="native", price_at_open=1.0,
    ))
    q.remove("pid_a")
    assert list(q.iter_ripe(now_ms=2_000_000)) == []


def test_prune_for_missing_models(tmp_path):
    q = PendingResolutionQueue(tmp_path / "pending.json")
    for name in ("alpha", "beta"):
        q.enqueue(PendingEntry(
            prediction_id=f"{name}_p", boundary_ms=1_000_000,
            model_name=name, symbol="s", market_window_seconds=900,
            registry_load_generation=0, ts_resolve_at_ms=1_900_000,
            resolution_type="native", price_at_open=1.0,
        ))
    pruned = q.prune_for_models(active_models={"alpha"})
    remaining = [e.prediction_id for e in q.iter_all()]
    assert remaining == ["alpha_p"]
    assert pruned == 1


def test_persist_atomic_via_temp_file(tmp_path):
    p = tmp_path / "pending.json"
    q = PendingResolutionQueue(p)
    q.enqueue(PendingEntry(
        prediction_id="pid_a", boundary_ms=1, model_name="m", symbol="s",
        market_window_seconds=900, registry_load_generation=0,
        ts_resolve_at_ms=2, resolution_type="native", price_at_open=1.0,
    ))
    q.persist()
    # No leftover .tmp file
    leftovers = [x for x in p.parent.iterdir() if x.suffix == ".tmp"]
    assert leftovers == []
    raw = json.loads(p.read_text())
    assert raw[0]["prediction_id"] == "pid_a"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_pending_queue.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `pending_queue.py`**

```python
# ofi-lab-v3/storage/pending_queue.py
"""Pending-resolution queue.

Keyed on the same composite the idempotency index uses so hot-reload
cleanup is unambiguous. Persisted as a single JSON file (atomic
write-and-rename). Plan B replaces the JSON file with a SQLite table
if scale demands; the public API stays the same.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator, Set


@dataclass
class PendingEntry:
    prediction_id: str
    boundary_ms: int
    model_name: str
    symbol: str
    market_window_seconds: int
    registry_load_generation: int
    ts_resolve_at_ms: int
    resolution_type: str   # 'native' | 'evaluation'
    price_at_open: float
    # Note: no full features dict here — Steering 10i. Verbose features
    # live in decision_traces.

    def composite_key(self) -> tuple:
        return (
            self.model_name, self.symbol, self.market_window_seconds,
            self.boundary_ms, self.registry_load_generation,
        )


class PendingResolutionQueue:
    def __init__(self, path) -> None:
        self._path = Path(path)
        self._entries: dict[str, PendingEntry] = {}
        if self._path.exists():
            self.load()

    def load(self) -> None:
        raw = json.loads(self._path.read_text())
        self._entries = {r["prediction_id"]: PendingEntry(**r) for r in raw}

    def persist(self) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps([asdict(e) for e in self._entries.values()]))
        os.replace(tmp, self._path)

    def enqueue(self, entry: PendingEntry) -> None:
        self._entries[entry.prediction_id] = entry

    def remove(self, prediction_id: str) -> None:
        self._entries.pop(prediction_id, None)

    def iter_all(self) -> Iterator[PendingEntry]:
        return iter(self._entries.values())

    def iter_ripe(self, now_ms: int) -> Iterator[PendingEntry]:
        for e in self._entries.values():
            if e.ts_resolve_at_ms <= now_ms:
                yield e

    def prune_for_models(self, active_models: Set[str]) -> int:
        before = len(self._entries)
        self._entries = {
            pid: e for pid, e in self._entries.items()
            if e.model_name in active_models
        }
        return before - len(self._entries)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_pending_queue.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/storage/pending_queue.py \
        ofi-lab-v3/tests/test_pending_queue.py
git commit -m "v3: PendingResolutionQueue keyed on composite + model prune"
```

---

### Task 15: Per-model `ProbabilityCalibrator` with `MIN_SAMPLES_PER_BIN = 20` and staleness

**Files:**
- Modify: `ofi-lab-v3/execution/calibration.py`
- Test: `ofi-lab-v3/tests/test_calibration_v3.py`

Calibration is the only Plan A change to a non-storage module. The change is small: keyed cache of `ProbabilityCalibrator` instances per `(model_name, symbol, market_window_seconds)`, `MIN_SAMPLES_PER_BIN` raised to 20, and a `last_refit_at` timestamp surfaced for the staleness warning.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_calibration_v3.py
import json
import time
from pathlib import Path

import pytest

import execution.calibration as cal_mod
from execution.calibration import (
    ProbabilityCalibrator, MIN_SAMPLES_PER_BIN,
    CalibratorRegistry,
)


def test_min_samples_per_bin_is_twenty():
    assert MIN_SAMPLES_PER_BIN == 20


def test_per_model_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    reg = CalibratorRegistry(base_dir=str(tmp_path))
    a = reg.get("900s_btc_v3_20260315", "BTCUSDT", 900)
    b = reg.get("60s_btc_v3_20260315", "BTCUSDT", 60)
    assert a is not b
    a.record_outcome(0.55, True)
    a.record_outcome(0.55, True)
    # b unaffected
    assert len(b._outcomes) == 0


def test_min_samples_per_bin_filters_overfit_extremes(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    reg = CalibratorRegistry(base_dir=str(tmp_path))
    c = reg.get("m", "S", 900)
    # 25 samples at conf=0.52 (eligible), 19 samples at conf=0.54 (filtered)
    for _ in range(25):
        c.record_outcome(0.52, True)
    for _ in range(19):
        c.record_outcome(0.54, True)
    refit_keys = {b["raw"] for b in c._bins}
    assert 0.52 in refit_keys
    assert 0.54 not in refit_keys


def test_last_refit_at_updates_after_refit(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    reg = CalibratorRegistry(base_dir=str(tmp_path))
    c = reg.get("m", "S", 900)
    assert c.last_refit_at is None
    for _ in range(40):  # exceeds MIN_REFIT_SAMPLES=30
        c.record_outcome(0.52, True)
    assert c.last_refit_at is not None


def test_is_stale_after_24h(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    reg = CalibratorRegistry(base_dir=str(tmp_path))
    c = reg.get("m", "S", 900)
    for _ in range(40):
        c.record_outcome(0.52, True)
    # Force last_refit_at into the past
    c.last_refit_at = c.last_refit_at - 25 * 3600
    assert c.is_stale(now_epoch=time.time())
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_calibration_v3.py -v
```

Expected: `ImportError: cannot import name 'CalibratorRegistry'` and the `MIN_SAMPLES_PER_BIN == 20` assertion failing.

- [ ] **Step 3: Modify `execution/calibration.py`**

Change the constant near the top of the file:

```python
MIN_SAMPLES_PER_BIN = 20  # raised from 5 to prevent overfit on extreme bins
```

Add `last_refit_at` to `ProbabilityCalibrator.__init__`:

```python
self.last_refit_at: Optional[float] = None
```

Set it inside `_refit()` immediately after the atomic write:

```python
import time
self.last_refit_at = time.time()
```

Add `is_stale`:

```python
def is_stale(self, now_epoch: float, ttl_seconds: int = 86_400) -> bool:
    if self.last_refit_at is None:
        return False
    return (now_epoch - self.last_refit_at) > ttl_seconds
```

At the bottom of the file add the registry:

```python
class CalibratorRegistry:
    """Per-(model, symbol, market_window) calibrator factory.

    Each combination has its own JSON map and outcomes log under
    ``base_dir``. Sharing a single calibrator across multiple models is
    a v2 footgun: a strong-signal h300 outcome would skew the h60 map.
    """

    def __init__(self, base_dir: str) -> None:
        self._base = base_dir
        self._cache: dict[tuple, ProbabilityCalibrator] = {}

    def get(self, model_name: str, symbol: str,
            market_window_seconds: int) -> ProbabilityCalibrator:
        key = (model_name, symbol, market_window_seconds)
        if key not in self._cache:
            slug = f"{model_name}__{symbol}__{market_window_seconds}"
            map_path = f"{self._base}/calibration_{slug}.json"
            self._cache[key] = ProbabilityCalibrator(path=map_path)
        return self._cache[key]
```

Also expose the underlying outcomes list as a `_outcomes` attribute (it already exists internally — just confirm the name matches the test).

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_calibration_v3.py -v
```

Expected: 5 passed. Run the full suite to confirm no regressions:

```bash
python -m pytest tests/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/execution/calibration.py \
        ofi-lab-v3/tests/test_calibration_v3.py
git commit -m "v3: per-model ProbabilityCalibrator + MIN_SAMPLES=20 + staleness"
```

---

### Task 16: Wire `SQLiteLedger` into `PaperTrader.__init__` (parallel to legacy `Ledger`)

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Test: `ofi-lab-v3/tests/test_paper_trader_init_v3.py`

This task adds the SQLite path **alongside** the existing JSONL ledger. Behavior of the JSONL path is preserved exactly so the running system can be observed in dual-write mode during initial v3 boot. The handoff (JSONL removal) happens in T22 after every other write site is converted.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_paper_trader_init_v3.py
"""Smoke tests on PaperTrader v3 init wiring.

We do not boot the WebSocket or score real predictions here. We only
confirm that:
  - PaperTrader holds a SQLiteLedger and a CalibratorRegistry
  - RegistryState and PolicySnapshot are bootstrapped at init
  - The legacy Ledger is still constructed (dual-write phase)
"""
import os
from pathlib import Path

import pytest


def make_trader(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    # Use a single fake model file so the loader doesn't crash.
    fake = tmp_path / "model.lgb"
    # Real LightGBM file from the test fixture set
    fixture = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not fixture.exists():
        pytest.skip("tiny_model.lgb fixture not present; produced by T_FIX")
    fake.write_bytes(fixture.read_bytes())
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": str(fake)},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_trader_has_sqlite_ledger(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    from storage.sqlite_ledger import SQLiteLedger
    assert isinstance(t.sqlite_ledger, SQLiteLedger)


def test_trader_has_calibrator_registry(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    from execution.calibration import CalibratorRegistry
    assert isinstance(t.calibrators, CalibratorRegistry)


def test_registry_state_bootstrapped(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    assert t.registry_state.current_generation() == 0


def test_policy_snapshot_captured_at_boot(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    v, h = t.policy_snapshot.current()
    assert v == 0
    assert len(h) == 64


def test_legacy_jsonl_ledger_still_present(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    from trading.ledger import Ledger
    assert all(isinstance(l, Ledger) for l in t.ledgers.values())
```

A separate fixture task (T_FIX, run once before T16) is required: copy a real LightGBM artifact into `ofi-lab-v3/tests/fixtures/tiny_model.lgb` from the existing baseline (`/data/models/latest_h300/model.lgb` from a v2 backup or a freshly trained tiny model). Document this in `tests/fixtures/README.md`.

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_paper_trader_init_v3.py -v
```

Expected: `AttributeError: 'PaperTrader' object has no attribute 'sqlite_ledger'`.

- [ ] **Step 3: Modify `PaperTrader.__init__`**

In `trading/paper_trader.py`, near the top:

```python
from storage.db import open_database, init_schema
from storage.registry_state import RegistryState
from storage.policy_snapshot import PolicySnapshot
from storage.sqlite_ledger import SQLiteLedger
from storage.decision_trace import DecisionTraceWriter
from storage.pending_queue import PendingResolutionQueue
from storage.provenance import (
    sha256_file, feature_names_hash, calibration_map_hash,
)
from execution.calibration import CalibratorRegistry
import config
```

In `__init__`, after the existing model-loading block, append:

```python
# v3 storage layer (parallel to legacy JSONL during cutover)
db_path = os.environ.get("STORAGE_DB_PATH", config.STORAGE_DB_PATH)
self._db_conn = open_database(db_path)
init_schema(self._db_conn)
self.registry_state = RegistryState(self._db_conn)
self.registry_state.bootstrap_if_empty(reason="paper_trader boot")

self.policy_snapshot = PolicySnapshot(self._db_conn)
self._policy_snapshot_dict = self._capture_policy_dict()
self.policy_snapshot.capture(self._policy_snapshot_dict, initiated_by="boot")

self.sqlite_ledger = SQLiteLedger(self._db_conn)
self.decision_trace = DecisionTraceWriter(self._db_conn)
self.pending_queue = PendingResolutionQueue(
    Path(self.log_dir) / "pending_v3.json"
)

calib_dir = os.environ.get("KALSHI_CALIBRATION_DIR", "/data")
self.calibrators = CalibratorRegistry(base_dir=calib_dir)

# Compute provenance hashes for each loaded model
self._model_envelopes: dict[str, dict] = {}
for name, path in model_paths.items():
    self._model_envelopes[name] = {
        "model_artifact_hash": sha256_file(path),
        "feature_names_hash": feature_names_hash(self.feature_names[name]),
    }
```

Add the helper method:

```python
def _capture_policy_dict(self) -> dict:
    """Snapshot the runtime-mutable filter/threshold/Kelly config.

    This is the canonical input to PolicySnapshot.capture(). Any field
    that influences a trade decision and can change at runtime must
    appear here.
    """
    f = self.filters
    return {
        "confidence_threshold": f.get("confidence_threshold"),
        "per_symbol_confidence": f.get("per_symbol_confidence", {}),
        "kelly_fraction": f.get("kelly_fraction"),
        "ev_threshold": f.get("ev_threshold", 0.0),
        "circuit_breaker_drawdown": f.get("circuit_breaker_drawdown"),
        "clob_divergence_min_edge": f.get("clob_divergence_min_edge"),
        "filter_mode": f.get("filter_mode"),
        "blackout_hours_utc": list(f.get("blackout_hours_utc", [])),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_paper_trader_init_v3.py -v
python -m pytest tests/ -v
```

Expected: new tests pass, no existing tests regress.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py \
        ofi-lab-v3/tests/test_paper_trader_init_v3.py \
        ofi-lab-v3/tests/fixtures/
git commit -m "v3: PaperTrader.__init__ — wire SQLite ledger, registry, policy, calibrators"
```

---

### Task 17: Build `ProvenanceEnvelope` per model and pass through scoring path

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Modify: `ofi-lab-v3/config.py` — extend `PAPER_TRADING["models"]` with native horizon metadata
- Test: `ofi-lab-v3/tests/test_paper_trader_provenance.py`

The model registry in Plan A is still the simple `dict[str, path]` that v2 uses; Plan B replaces it with `model_registry.json`. To carry training horizons / train dates / feature version through Plan A, those fields are added as a parallel dict in `config.py`. The values for the baseline are populated from the audit document and the `freeze-20260428/` artifact directory.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_paper_trader_provenance.py
from pathlib import Path

import pytest


def make_trader(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    fixture = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not fixture.exists():
        pytest.skip("tiny_model.lgb fixture missing")
    fake = tmp_path / "m.lgb"
    fake.write_bytes(fixture.read_bytes())
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": str(fake)},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_envelope_carries_full_provenance(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    env = t._build_envelope("900s_btc_v3_20260315", platform="paper")
    assert env.model_name == "900s_btc_v3_20260315"
    assert len(env.model_artifact_hash) == 64
    assert len(env.feature_names_hash) == 64
    assert len(env.policy_config_hash) == 64
    assert len(env.calibration_map_hash) == 64
    assert env.feature_version == "v3"
    assert env.training_horizon_seconds == 900
    assert env.platform == "paper"
    assert env.registry_load_generation == 0
    assert env.decision_policy_version == 0


def test_envelope_picks_up_policy_change(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    env_a = t._build_envelope("900s_btc_v3_20260315", platform="paper")
    # Simulate a runtime config change (e.g. via API)
    t.filters["confidence_threshold"] = 0.60
    t.policy_snapshot.capture(t._capture_policy_dict(), initiated_by="api")
    env_b = t._build_envelope("900s_btc_v3_20260315", platform="paper")
    assert env_b.decision_policy_version == env_a.decision_policy_version + 1
    assert env_b.policy_config_hash != env_a.policy_config_hash
    # Model artifact hash unchanged
    assert env_b.model_artifact_hash == env_a.model_artifact_hash


def test_envelope_kalshi_platform(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    env = t._build_envelope("900s_btc_v3_20260315", platform="kalshi")
    assert env.platform == "kalshi"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_paper_trader_provenance.py -v
```

Expected: `AttributeError: '_build_envelope'`.

- [ ] **Step 3: Add model metadata to config and `_build_envelope` to PaperTrader**

In `ofi-lab-v3/config.py` extend `PAPER_TRADING`:

```python
PAPER_TRADING["model_metadata"] = {
    # Baseline (immutable identity)
    "900s_btc_v3_20260315": {
        "feature_version": "v3",
        "training_horizon_seconds": 900,
        "train_window_start": "2025-04-01",
        "train_window_end":   "2026-03-15",
        "train_cutoff":       "2026-03-15",
        "symbol":             "BTCUSDT",
    },
    # Dual-symbol h60 if loaded
    "60s_btc_v3_20260315": {
        "feature_version": "v3",
        "training_horizon_seconds": 60,
        "train_window_start": "2025-04-01",
        "train_window_end":   "2026-03-15",
        "train_cutoff":       "2026-03-15",
        "symbol":             "BTCUSDT",
    },
    # Legacy aliases the v2 CLI may still pass at startup
    "h300": {
        "feature_version": "v3",
        "training_horizon_seconds": 900,
        "train_window_start": "2025-04-01",
        "train_window_end":   "2026-03-15",
        "train_cutoff":       "2026-03-15",
        "symbol":             "BTCUSDT",
    },
    "h60": {
        "feature_version": "v3",
        "training_horizon_seconds": 60,
        "train_window_start": "2025-04-01",
        "train_window_end":   "2026-03-15",
        "train_cutoff":       "2026-03-15",
        "symbol":             "BTCUSDT",
    },
}
```

In `trading/paper_trader.py`:

```python
from storage.provenance import ProvenanceEnvelope, calibration_map_hash

def _build_envelope(self, model_name: str, platform: str) -> ProvenanceEnvelope:
    """Construct the provenance envelope for the next prediction.

    Re-captures the policy snapshot (cheap; only writes a new audit row
    if the canonical form changed) and re-hashes the active calibration
    map for the model so any out-of-band refit flows through.
    """
    meta = config.PAPER_TRADING["model_metadata"][model_name]
    art_hash = self._model_envelopes[model_name]["model_artifact_hash"]
    fname_hash = self._model_envelopes[model_name]["feature_names_hash"]
    policy_v, policy_h = self.policy_snapshot.capture(
        self._capture_policy_dict(), initiated_by="prediction"
    )
    cal = self.calibrators.get(model_name, meta["symbol"],
                                meta["training_horizon_seconds"])
    cal_map = {"method": "binmap", "bins": list(cal._bins)}
    cal_h = calibration_map_hash(cal_map)
    return ProvenanceEnvelope(
        model_name=model_name,
        model_artifact_hash=art_hash,
        feature_names_hash=fname_hash,
        feature_version=meta["feature_version"],
        training_horizon_seconds=meta["training_horizon_seconds"],
        train_window_start=meta["train_window_start"],
        train_window_end=meta["train_window_end"],
        train_cutoff=meta["train_cutoff"],
        registry_load_generation=self.registry_state.current_generation(),
        policy_config_hash=policy_h,
        decision_policy_version=policy_v,
        calibration_map_hash=cal_h,
        platform=platform,
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_paper_trader_provenance.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/config.py \
        ofi-lab-v3/tests/test_paper_trader_provenance.py
git commit -m "v3: PaperTrader._build_envelope + model_metadata in config"
```

---

### Task 18: Replace prediction logging in `_run_predictions` with native + evaluation rows

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` (lines ~700–760 of v2 — the per-model scoring block)
- Test: `ofi-lab-v3/tests/test_paper_trader_scoring.py`

This is the critical bug fix for Steering 10a/10e. The v2 code computes a single `pred_resolve_at_ms = boundary_ms + 900_000` and writes one prediction row. v3 builds the row plan from the model's native horizon plus `EVALUATION_WINDOWS`, writes one native row + N evaluation rows via `SQLiteLedger.log_prediction_set`, and enqueues each row in the pending queue.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_paper_trader_scoring.py
"""Direct test on the scoring helper that emits prediction rows.

We extract the row-emission step into a method ``_emit_prediction_rows``
so it can be unit-tested without the full WebSocket / boundary loop.
"""
from pathlib import Path

import pytest


def make_trader(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    fixture = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not fixture.exists():
        pytest.skip("tiny_model.lgb fixture missing")
    fake = tmp_path / "m.lgb"
    fake.write_bytes(fixture.read_bytes())
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": str(fake)},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_emit_prediction_rows_writes_native_plus_three(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    pid = t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315",
        symbol="BTCUSDT",
        boundary_ms=1_700_000_000_000,
        ts_model_ran_ms=1_700_000_000_000 - 500,
        pred_proba_raw=0.54, pred_proba_calibrated=0.51,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", p_market=0.50,
        utc_hour=12, day_of_week=2, is_weekend=0,
        relative_spread=0.00012,
        price_at_open=60_000.0,
    )
    rows = t._db_conn.execute(
        "SELECT market_window_seconds, resolution_type, ts_resolve_at_ms"
        " FROM predictions WHERE model_name=?",
        ("900s_btc_v3_20260315",)).fetchall()
    by_w = {r["market_window_seconds"]: r for r in rows}
    assert by_w[900]["resolution_type"] == "native"
    for w in (300, 1800, 3600):
        assert by_w[w]["resolution_type"] == "evaluation"
        assert by_w[w]["ts_resolve_at_ms"] == 1_700_000_000_000 + w * 1000
    assert by_w[900]["ts_resolve_at_ms"] == 1_700_000_000_000 + 900_000
    # Pending queue has 4 entries
    assert len(list(t.pending_queue.iter_all())) == 4


def test_h60_horizon_emits_native_60_not_900(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    fixture = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not fixture.exists():
        pytest.skip()
    fake = tmp_path / "m.lgb"
    fake.write_bytes(fixture.read_bytes())
    t = PaperTrader(
        model_paths={"60s_btc_v3_20260315": str(fake)},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    t._emit_prediction_rows(
        model_name="60s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=1_700_000_000_000,
        ts_model_ran_ms=1_700_000_000_000,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    rows = t._db_conn.execute(
        "SELECT market_window_seconds, resolution_type, ts_resolve_at_ms"
        " FROM predictions").fetchall()
    by_w = {r["market_window_seconds"]: r for r in rows}
    assert by_w[60]["resolution_type"] == "native"
    assert by_w[60]["ts_resolve_at_ms"] == 1_700_000_000_000 + 60_000
    for w in (300, 900, 1800, 3600):
        assert by_w[w]["resolution_type"] == "evaluation"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_paper_trader_scoring.py -v
```

Expected: `AttributeError: '_emit_prediction_rows'`.

- [ ] **Step 3: Implement `_emit_prediction_rows`**

Add to `trading/paper_trader.py`:

```python
from storage.window_planner import plan_resolution_rows
from storage.pending_queue import PendingEntry

def _emit_prediction_rows(
    self,
    *,
    model_name: str,
    symbol: str,
    boundary_ms: int,
    ts_model_ran_ms: int,
    pred_proba_raw: float,
    pred_proba_calibrated: float,
    pred_direction: str,
    above_threshold: bool,
    warmup: bool,
    platform: str,
    price_at_open: float,
    p_market: Optional[float] = None,
    p_model_minus_market: Optional[float] = None,
    utc_hour: Optional[int] = None,
    day_of_week: Optional[int] = None,
    is_weekend: Optional[int] = None,
    relative_spread: Optional[float] = None,
) -> str:
    """Insert the native + evaluation prediction rows for one boundary
    and enqueue each in the pending resolution queue. Returns the
    native row's prediction_id.
    """
    meta = config.PAPER_TRADING["model_metadata"][model_name]
    horizon = meta["training_horizon_seconds"]
    rows = plan_resolution_rows(
        boundary_ms=boundary_ms,
        training_horizon_seconds=horizon,
        evaluation_windows=config.EVALUATION_WINDOWS,
    )
    envelope = self._build_envelope(model_name, platform=platform)
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
    )
    # Build prediction_id for each row to enqueue. Mirror the suffix
    # scheme from SQLiteLedger.log_prediction_set: <prefix>_<window>_<n|e>.
    prefix = native_pid.rsplit("_", 2)[0]
    for r in rows:
        suffix = f"{r.market_window_seconds}_{r.resolution_type[0]}"
        pid = f"{prefix}_{suffix}"
        self.pending_queue.enqueue(PendingEntry(
            prediction_id=pid,
            boundary_ms=boundary_ms,
            model_name=model_name,
            symbol=symbol,
            market_window_seconds=r.market_window_seconds,
            registry_load_generation=envelope.registry_load_generation,
            ts_resolve_at_ms=r.ts_resolve_at_ms,
            resolution_type=r.resolution_type,
            price_at_open=price_at_open,
        ))
    self.pending_queue.persist()
    return native_pid
```

Then **replace** the v2 prediction-logging block inside `_run_predictions` (the `for symbol in PREDICTION_SYMBOLS: for model_name, model in self.models.items():` block, lines ~700–752 of v2) so that after computing `pred_proba`, instead of calling `self.ledgers[model_name].log_prediction(...)` and stashing into the legacy pending file, it calls:

```python
native_pid = self._emit_prediction_rows(
    model_name=model_name, symbol=symbol,
    boundary_ms=boundary_ms,
    ts_model_ran_ms=now_ms,
    pred_proba_raw=pred_proba_raw,
    pred_proba_calibrated=pred_proba_calibrated,
    pred_direction=pred_direction,
    above_threshold=above_threshold,
    warmup=is_warmup,
    platform="paper",
    price_at_open=price_at_open,
    p_market=p_market,
    p_model_minus_market=p_model_minus_market,
    utc_hour=utc_hour, day_of_week=day_of_week, is_weekend=is_weekend,
    relative_spread=feat_row.get("relative_spread"),
)
```

Also keep the legacy `self.ledgers[model_name].log_prediction(...)` call in place during the dual-write phase. Both write paths run; the SQLite path is the one queried by the new endpoints. T22 removes the legacy path.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_paper_trader_scoring.py -v
python -m pytest tests/ -v
```

Expected: new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py \
        ofi-lab-v3/tests/test_paper_trader_scoring.py
git commit -m "v3: emit native+evaluation prediction rows; fix hardcoded 900s (Steering 10a/10e)"
```

---

### Task 19: Horizon-aware native resolution + evaluation resolution loop

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` — replace `_check_prediction_resolutions` body
- Test: `ofi-lab-v3/tests/test_paper_trader_resolution.py`

The v2 method resolves all pending predictions at `boundary + 900s`. v3 walks the pending queue, picks up every entry whose `ts_resolve_at_ms <= now_ms`, fetches the close price for that exact moment, and dispatches to either `record_native_resolution` or `record_evaluation_resolution` based on `resolution_type`. Calibrator outcomes are recorded **only** for native rows.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_paper_trader_resolution.py
"""Resolution loop with multi-window pending entries.

Uses a fake feature computer that returns a deterministic close price
for any ``ts``. The test injects pending entries, calls the resolution
loop, and asserts that native rows update calibration_outcomes and
evaluation rows do not.
"""
from pathlib import Path

import pytest

from storage.pending_queue import PendingEntry


class FakeFeatureComputer:
    def __init__(self):
        self._prices = {}

    def set_price(self, symbol, ts_ms, price):
        self._prices[(symbol, ts_ms)] = price

    def price_at(self, symbol, ts_ms):
        # Deterministic; falls back to last known mid
        return self._prices[(symbol, ts_ms)]


def make_trader(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    fixture = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not fixture.exists():
        pytest.skip()
    fake = tmp_path / "m.lgb"
    fake.write_bytes(fixture.read_bytes())
    t = PaperTrader(
        model_paths={"900s_btc_v3_20260315": str(fake)},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )
    t.feature_computer = FakeFeatureComputer()
    return t


def test_native_resolution_writes_calibration_outcome(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    boundary = 1_700_000_000_000
    t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=boundary, ts_model_ran_ms=boundary,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=True, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    # Provide a close price for native (boundary + 900s)
    t.feature_computer.set_price("BTCUSDT", boundary + 900_000, 60_100.0)
    # Run resolution loop with now_ms past native but before all evals
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        t._check_prediction_resolutions_v3(now_ms=boundary + 901_000)
    )
    cal = t._db_conn.execute(
        "SELECT count(*) AS n FROM calibration_outcomes"
    ).fetchone()
    assert cal["n"] == 1
    pred = t._db_conn.execute(
        "SELECT contract_result, prediction_correct, market_window_seconds"
        " FROM predictions WHERE resolved=1"
    ).fetchall()
    # Only native (and 300s eval, since 300s < 900s already ripe)
    native = [r for r in pred if r["market_window_seconds"] == 900]
    assert native[0]["contract_result"] == "up"
    assert native[0]["prediction_correct"] == 1


def test_evaluation_resolution_does_not_write_calibration(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    boundary = 1_700_000_000_000
    t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=boundary, ts_model_ran_ms=boundary,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    # Only the 300s eval ripens
    t.feature_computer.set_price("BTCUSDT", boundary + 300_000, 60_050.0)
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        t._check_prediction_resolutions_v3(now_ms=boundary + 301_000)
    )
    cal = t._db_conn.execute(
        "SELECT count(*) AS n FROM calibration_outcomes"
    ).fetchone()
    assert cal["n"] == 0
    eval_row = t._db_conn.execute(
        "SELECT resolved, contract_result FROM predictions"
        " WHERE market_window_seconds=300 AND resolution_type='evaluation'"
    ).fetchone()
    assert eval_row["resolved"] == 1
    assert eval_row["contract_result"] == "up"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_paper_trader_resolution.py -v
```

Expected: `AttributeError: '_check_prediction_resolutions_v3'`.

- [ ] **Step 3: Implement the new resolution loop**

Add to `trading/paper_trader.py`:

```python
async def _check_prediction_resolutions_v3(self, now_ms: int) -> None:
    """Walk the pending queue and resolve every ripe row.

    Native rows update calibration_outcomes via SQLiteLedger;
    evaluation rows do not. Each resolved row is removed from the
    queue. The queue is persisted at the end so a crash mid-loop
    leaves the partially-resolved state recoverable.
    """
    ripe = list(self.pending_queue.iter_ripe(now_ms))
    if not ripe:
        return
    for entry in ripe:
        try:
            close_price = self.feature_computer.price_at(
                entry.symbol, entry.ts_resolve_at_ms,
            )
        except KeyError:
            # Price not yet available for this exact ts; leave queued.
            continue
        result, correct = self._compute_outcome(
            direction=self._direction_for(entry.prediction_id),
            price_open=entry.price_at_open,
            price_close=close_price,
        )
        if entry.resolution_type == "native":
            self.sqlite_ledger.record_native_resolution(
                prediction_id=entry.prediction_id,
                ts_resolved_ms=entry.ts_resolve_at_ms,
                price_at_open=entry.price_at_open,
                price_at_close=close_price,
                contract_result=result,
                prediction_correct=correct,
            )
            # Feed per-model calibrator (only for native, only when not
            # warmup)
            row = self._db_conn.execute(
                "SELECT pred_proba_raw, warmup, model_name, symbol,"
                " market_window_seconds FROM predictions"
                " WHERE prediction_id = ?",
                (entry.prediction_id,),
            ).fetchone()
            if row and row["warmup"] == 0:
                cal = self.calibrators.get(
                    row["model_name"], row["symbol"],
                    row["market_window_seconds"],
                )
                cal.record_outcome(float(row["pred_proba_raw"]), bool(correct))
        else:
            self.sqlite_ledger.record_evaluation_resolution(
                prediction_id=entry.prediction_id,
                ts_resolved_ms=entry.ts_resolve_at_ms,
                price_at_open=entry.price_at_open,
                price_at_close=close_price,
                contract_result=result,
                prediction_correct=correct,
            )
        self.pending_queue.remove(entry.prediction_id)
    self.pending_queue.persist()


def _direction_for(self, prediction_id: str) -> str:
    row = self._db_conn.execute(
        "SELECT pred_direction FROM predictions WHERE prediction_id = ?",
        (prediction_id,),
    ).fetchone()
    return row["pred_direction"] if row else "up"


@staticmethod
def _compute_outcome(direction, price_open, price_close):
    if price_close > price_open:
        result = "up"
    elif price_close < price_open:
        result = "down"
    else:
        result = "flat"
    correct = (result == direction)
    return result, correct
```

In the main `_contract_boundary_loop`, replace the call to `await self._check_prediction_resolutions(now_ms)` with `await self._check_prediction_resolutions_v3(now_ms)`. Keep the legacy method present but unused (removed in T22).

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_paper_trader_resolution.py -v
python -m pytest tests/ -v
```

Expected: new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py \
        ofi-lab-v3/tests/test_paper_trader_resolution.py
git commit -m "v3: horizon-aware native + evaluation resolution loop"
```

---

### Task 20: Generalize Kalshi dispatch (Steering 10c, 10d)

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py` — `_dispatch_kalshi_live` and the call site
- Modify: `ofi-lab-v3/config.py` — add `model_metadata[*]["kalshi_dispatch_enabled"]` flag
- Test: `ofi-lab-v3/tests/test_kalshi_dispatch_gating.py`

The v2 hardcode at `paper_trader.py:911-916` (`if model_name == "h300" and symbol == "BTCUSDT" and duration == 900`) must move to a registry-driven flag. Plan A's registry is still `config.PAPER_TRADING["model_metadata"]`; Plan B replaces it with `model_registry.json` but the call-site shape is the same. Steering 10d (the `+ 900` ticker resolution) is also fixed here by passing the model's native horizon to `_resolve_kalshi_ticker_for_boundary`.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_kalshi_dispatch_gating.py
from pathlib import Path

import pytest


def make_trader_two_models(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    fixture = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not fixture.exists():
        pytest.skip()
    a = tmp_path / "a.lgb"
    b = tmp_path / "b.lgb"
    a.write_bytes(fixture.read_bytes())
    b.write_bytes(fixture.read_bytes())
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={
            "900s_btc_v3_20260315": str(a),
            "60s_btc_v3_20260315":  str(b),
        },
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_only_dispatch_enabled_models_dispatch(tmp_path, monkeypatch):
    t = make_trader_two_models(tmp_path, monkeypatch)
    # Default flags: baseline kalshi_dispatch_enabled=True, others False
    assert t.kalshi_dispatch_eligible(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        market_window_seconds=900,
    ) is True
    assert t.kalshi_dispatch_eligible(
        model_name="60s_btc_v3_20260315", symbol="BTCUSDT",
        market_window_seconds=60,
    ) is False


def test_dispatch_only_on_native_horizon(tmp_path, monkeypatch):
    t = make_trader_two_models(tmp_path, monkeypatch)
    # Even baseline rejects evaluation-window dispatch
    assert t.kalshi_dispatch_eligible(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        market_window_seconds=300,
    ) is False
    assert t.kalshi_dispatch_eligible(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        market_window_seconds=900,
    ) is True


def test_dispatch_rejects_unknown_model(tmp_path, monkeypatch):
    t = make_trader_two_models(tmp_path, monkeypatch)
    assert t.kalshi_dispatch_eligible(
        model_name="ghost", symbol="BTCUSDT", market_window_seconds=900,
    ) is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_kalshi_dispatch_gating.py -v
```

Expected: `AttributeError: 'kalshi_dispatch_eligible'`.

- [ ] **Step 3: Add the flag and the eligibility helper**

In `config.py`, extend each `model_metadata` entry with a `kalshi_dispatch_enabled` flag. Default to `False`; set `True` only on the baseline:

```python
PAPER_TRADING["model_metadata"]["900s_btc_v3_20260315"]["kalshi_dispatch_enabled"] = True
PAPER_TRADING["model_metadata"]["h300"]["kalshi_dispatch_enabled"] = True
PAPER_TRADING["model_metadata"]["60s_btc_v3_20260315"]["kalshi_dispatch_enabled"] = False
PAPER_TRADING["model_metadata"]["h60"]["kalshi_dispatch_enabled"] = False
```

In `trading/paper_trader.py`:

```python
def kalshi_dispatch_eligible(
    self, *, model_name: str, symbol: str, market_window_seconds: int,
) -> bool:
    """Registry-driven Kalshi gate.

    Replaces the v2 hardcoded check
    ``model_name == 'h300' and symbol == 'BTCUSDT' and duration == 900``.
    """
    meta = config.PAPER_TRADING["model_metadata"].get(model_name)
    if meta is None:
        return False
    if not meta.get("kalshi_dispatch_enabled", False):
        return False
    if symbol != meta["symbol"]:
        return False
    # Only native horizon dispatches.
    if market_window_seconds != meta["training_horizon_seconds"]:
        return False
    return True
```

In `_run_predictions`, replace the existing condition (v2 line ~911):

```python
# v2:
# if model_name == "h300" and symbol == "BTCUSDT" and duration == 900: ...

# v3:
if self.kalshi_dispatch_eligible(
    model_name=model_name, symbol=symbol,
    market_window_seconds=meta["training_horizon_seconds"],
):
    ...
```

In `_resolve_kalshi_ticker_for_boundary` (v2 line ~551), replace the hardcoded `+ 900`:

```python
# v2: target_close_unix = boundary_ms // 1000 + 900
# v3:
target_close_unix = boundary_ms // 1000 + market_window_seconds
```

…and pass `market_window_seconds` (the dispatching model's native horizon) into the function from `_dispatch_kalshi_live`.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_kalshi_dispatch_gating.py -v
python -m pytest tests/ -v
```

Expected: new tests pass, no regressions.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py ofi-lab-v3/config.py \
        ofi-lab-v3/tests/test_kalshi_dispatch_gating.py
git commit -m "v3: registry-driven Kalshi dispatch (Steering 10c, 10d)"
```

---

### Task 21: Warmup tagging — `boot_ts_ms + WARMUP_SECONDS`

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Test: `ofi-lab-v3/tests/test_warmup_tagging.py`

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_warmup_tagging.py
import time
from pathlib import Path

import pytest

import config


def make_trader(tmp_path, monkeypatch, warmup_seconds=5):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    monkeypatch.setattr(config, "WARMUP_SECONDS", warmup_seconds)
    fixture = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not fixture.exists():
        pytest.skip()
    fake = tmp_path / "m.lgb"
    fake.write_bytes(fixture.read_bytes())
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": str(fake)},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_in_warmup_returns_true_immediately_after_boot(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch, warmup_seconds=5)
    now_ms = t._boot_ts_ms + 1000
    assert t.is_in_warmup(now_ms) is True


def test_in_warmup_returns_false_past_window(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch, warmup_seconds=5)
    now_ms = t._boot_ts_ms + 6000
    assert t.is_in_warmup(now_ms) is False


def test_emit_prediction_rows_stamps_warmup_flag(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch, warmup_seconds=10)
    now_ms = t._boot_ts_ms + 2000  # within warmup
    t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=now_ms, ts_model_ran_ms=now_ms,
        pred_proba_raw=0.55, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=False,
        warmup=t.is_in_warmup(now_ms),
        platform="paper", price_at_open=60_000.0,
    )
    rows = t._db_conn.execute("SELECT warmup FROM predictions").fetchall()
    assert all(r["warmup"] == 1 for r in rows)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_warmup_tagging.py -v
```

Expected: `AttributeError: '_boot_ts_ms'` or `'is_in_warmup'`.

- [ ] **Step 3: Implement warmup tracking**

In `PaperTrader.__init__` add:

```python
import time as _time
self._boot_ts_ms = int(_time.time() * 1000)
```

Add the predicate:

```python
def is_in_warmup(self, now_ms: int) -> bool:
    return now_ms < self._boot_ts_ms + config.WARMUP_SECONDS * 1000
```

In `_run_predictions`, compute `is_warmup = self.is_in_warmup(now_ms)` and pass it to both `_emit_prediction_rows` and the trade-log path. The Kalshi dispatch check must early-return when `is_warmup` is True.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_warmup_tagging.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py \
        ofi-lab-v3/tests/test_warmup_tagging.py
git commit -m "v3: warmup tagging on every record (boot_ts_ms + WARMUP_SECONDS)"
```

---

### Task 22: Retire legacy JSONL writes from PaperTrader

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Modify: `ofi-lab-v3/tests/test_paper_trader_init_v3.py`

- [ ] **Step 1: Update the dual-write check to assert SQLite-only**

In `tests/test_paper_trader_init_v3.py`, replace `test_legacy_jsonl_ledger_still_present` with:

```python
def test_legacy_jsonl_ledger_is_removed(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    # PaperTrader no longer constructs legacy Ledger instances
    assert not hasattr(t, "ledgers") or t.ledgers == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_paper_trader_init_v3.py::test_legacy_jsonl_ledger_is_removed -v
```

Expected: fails because `t.ledgers` still has entries from T16.

- [ ] **Step 3: Remove legacy ledger construction and writes**

Delete the lines in `__init__` that build `self.ledgers`. Set `self.ledgers = {}` so any leftover reference fails fast. In `_run_predictions`, remove the `self.ledgers[model_name].log_prediction(...)` calls and the legacy `pending_resolutions.json` / `pending_pred_resolutions.json` save/load. Delete `_check_resolutions` (legacy trade resolution loop) and `_check_prediction_resolutions` (legacy predictions loop). Confirm the only resolution loop in the boundary loop is `_check_prediction_resolutions_v3`.

- [ ] **Step 4: Add a new resolution loop for trades that uses SQLiteLedger**

The legacy `_resolve_trade` did the binary-option PnL math. Lift the math (lines ~1100–1141 of v2) into a new helper:

```python
async def _check_trade_resolutions_v3(self, now_ms: int) -> None:
    """Resolve any open paper_trades whose ts_resolve_at_ms <= now_ms."""
    rows = self._db_conn.execute(
        "SELECT trade_id, prediction_id, symbol,"
        " ts_resolve_at_ms, pred_proba_calibrated, pred_direction,"
        " simulated_stake_usdc, market_window_seconds"
        " FROM paper_trades WHERE resolved = 0 AND ts_resolve_at_ms <= ?",
        (now_ms,),
    ).fetchall()
    for row in rows:
        try:
            close = self.feature_computer.price_at(
                row["symbol"], row["ts_resolve_at_ms"]
            )
        except KeyError:
            continue
        # Pull price_at_open from the linked prediction
        pred = self._db_conn.execute(
            "SELECT price_at_open FROM predictions WHERE prediction_id = ?",
            (row["prediction_id"],)
        ).fetchone()
        if pred is None or pred["price_at_open"] is None:
            continue
        gross, fee, net, result, correct = self._compute_paper_pnl(
            direction=row["pred_direction"],
            calibrated_p=row["pred_proba_calibrated"],
            stake=row["simulated_stake_usdc"],
            price_open=pred["price_at_open"],
            price_close=close,
        )
        self.sqlite_ledger.record_trade_resolution(
            trade_id=row["trade_id"],
            ts_resolved_ms=row["ts_resolve_at_ms"],
            price_at_close=close,
            contract_result=result,
            prediction_correct=correct,
            gross_pnl=gross,
            fee_paid=fee,
            net_pnl=net,
            trade_result="win" if correct else "loss",
            pnl_method="binary_polymarket",
        )

def _compute_paper_pnl(self, *, direction, calibrated_p, stake,
                      price_open, price_close):
    """Polymarket-style binary option PnL.

    Lifted from v2 paper_trader.py lines ~1100-1141. The fee
    coefficient 0.072 matches Polymarket's published rate. Plan B
    extends this to a per-platform fee model.
    """
    if price_close > price_open:
        result = "up"
    elif price_close < price_open:
        result = "down"
    else:
        result = "flat"
    correct = (result == direction)
    fee = 0.072 * calibrated_p * (1 - calibrated_p) * stake
    if correct:
        gross = stake * (1 - calibrated_p) / calibrated_p if calibrated_p > 0 else 0
    else:
        gross = -stake
    net = gross - fee
    return gross, fee, net, result, correct
```

In the boundary loop, call `await self._check_trade_resolutions_v3(now_ms)` immediately after `_check_prediction_resolutions_v3`.

- [ ] **Step 5: Run full suite**

```bash
python -m pytest tests/ -v
```

Expected: all tests green. Any v2-specific JSONL tests that fail here should be deleted (the JSONL path no longer exists in v3).

- [ ] **Step 6: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py \
        ofi-lab-v3/tests/test_paper_trader_init_v3.py
git commit -m "v3: retire legacy JSONL ledger; SQLite-only paper trader"
```

---

### Task 23: Wire compact decision trace into the filter pipeline

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Test: `ofi-lab-v3/tests/test_compact_decision_trace.py`

The full 5-layer filter pipeline / EV gate ships in Plan B. Plan A wires the compact trace fields (`decision_outcome`, `decision_reason`, `ev_estimate`, `kelly_fraction_capped`, `final_size_usdc`, `order_type`) onto every prediction row using the existing v2 filter set.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_compact_decision_trace.py
from pathlib import Path

import pytest


def make_trader(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    fixture = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not fixture.exists():
        pytest.skip()
    fake = tmp_path / "m.lgb"
    fake.write_bytes(fixture.read_bytes())
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": str(fake)},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_executed_decision_records_compact_trace(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    pid = t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=1_700_000_000_000,
        ts_model_ran_ms=1_700_000_000_000,
        pred_proba_raw=0.58, pred_proba_calibrated=0.55,
        pred_direction="up", above_threshold=True, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    t._record_compact_decision(
        prediction_id=pid,
        outcome="executed", reason=None,
        ev_estimate=0.018, kelly_fraction_capped=0.25,
        final_size_usdc=10.0, order_type="maker",
    )
    row = t._db_conn.execute(
        "SELECT decision_outcome, decision_reason, ev_estimate,"
        " kelly_fraction_capped, final_size_usdc, order_type"
        " FROM predictions WHERE prediction_id = ?", (pid,)
    ).fetchone()
    assert row["decision_outcome"] == "executed"
    assert row["decision_reason"] is None
    assert row["order_type"] == "maker"


def test_suppressed_decision_records_reason(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    pid = t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=1_700_000_000_000,
        ts_model_ran_ms=1_700_000_000_000,
        pred_proba_raw=0.51, pred_proba_calibrated=0.50,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    t._record_compact_decision(
        prediction_id=pid,
        outcome="suppressed", reason="below_confidence",
        ev_estimate=None, kelly_fraction_capped=0.0,
        final_size_usdc=0.0, order_type="skipped",
    )
    row = t._db_conn.execute(
        "SELECT decision_outcome, decision_reason FROM predictions"
        " WHERE prediction_id = ?", (pid,)
    ).fetchone()
    assert row["decision_outcome"] == "suppressed"
    assert row["decision_reason"] == "below_confidence"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_compact_decision_trace.py -v
```

Expected: `AttributeError: '_record_compact_decision'`.

- [ ] **Step 3: Add the helper and call it from `_run_predictions`**

```python
def _record_compact_decision(
    self,
    *,
    prediction_id: str,
    outcome: str,
    reason: Optional[str],
    ev_estimate: Optional[float],
    kelly_fraction_capped: Optional[float],
    final_size_usdc: Optional[float],
    order_type: Optional[str],
) -> None:
    self.sqlite_ledger.log_compact_decision(
        prediction_id=prediction_id,
        decision_outcome=outcome,
        decision_reason=reason,
        ev_estimate=ev_estimate,
        kelly_fraction_capped=kelly_fraction_capped,
        final_size_usdc=final_size_usdc,
        order_type=order_type,
    )
```

In `_run_predictions`, after computing whether the prediction passed all filters, immediately invoke `_record_compact_decision` on the **native** prediction_id with the appropriate outcome:

- `outcome="executed"` if the prediction was logged as a paper trade
- `outcome="suppressed"` with the v2 suppression reason if the filters rejected it
- `outcome="gated"` if it was gated by warmup, blackout, or below-threshold

The existing v2 suppression-reason strings (`below_confidence`, `circuit_breaker`, `clob_divergence`, `volatility`, `non_15m_boundary`, `utc_blackout`, `contract_mismatch`) map directly into `reason`.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_compact_decision_trace.py -v
python -m pytest tests/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py \
        ofi-lab-v3/tests/test_compact_decision_trace.py
git commit -m "v3: compact decision trace on every prediction row"
```

---

### Task 24: Wire verbose `DecisionTraceWriter` into `_run_predictions`

**Files:**
- Modify: `ofi-lab-v3/trading/paper_trader.py`
- Test: `ofi-lab-v3/tests/test_verbose_decision_trace.py`

The verbose writer must be called **once per prediction**, capturing every filter that was evaluated (with its threshold + computed input value), the Kelly math breakdown (raw, capped, bankroll, per-trade cap), the active fee model + amount, any platform gate context, and the policy hash + calibration hash + generation pulled from the envelope used to score.

- [ ] **Step 1: Write the failing test**

```python
# ofi-lab-v3/tests/test_verbose_decision_trace.py
import json
from pathlib import Path

import pytest


def make_trader(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    fixture = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not fixture.exists():
        pytest.skip()
    fake = tmp_path / "m.lgb"
    fake.write_bytes(fixture.read_bytes())
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": str(fake)},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_verbose_trace_captures_every_filter(tmp_path, monkeypatch):
    t = make_trader(tmp_path, monkeypatch)
    pid = t._emit_prediction_rows(
        model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
        boundary_ms=1_700_000_000_000,
        ts_model_ran_ms=1_700_000_000_000,
        pred_proba_raw=0.51, pred_proba_calibrated=0.50,
        pred_direction="up", above_threshold=False, warmup=False,
        platform="paper", price_at_open=60_000.0,
    )
    t._write_verbose_trace_for_v2_filters(
        prediction_id=pid,
        envelope=t._build_envelope("900s_btc_v3_20260315", "paper"),
        filter_inputs={
            "confidence": (0.55, 0.51, False),
            "circuit_breaker": (50.0, 5.0, True),
            "clob_divergence": (0.02, 0.005, False),
            "volatility": (0.0005, 0.0003, True),
        },
        kelly_raw=0.05, kelly_capped=0.025,
        bankroll_used=200.0, per_trade_cap_usdc=5.0,
        fee_model="polymarket", fee_amount=0.018,
        platform_gate={"kalshi_allow_list": False},
        warmup=False, consensus_data=None,
    )
    row = t._db_conn.execute(
        "SELECT * FROM decision_traces WHERE prediction_id = ?", (pid,)
    ).fetchone()
    parsed = json.loads(row["filters_json"])
    names = {f["name"] for f in parsed}
    assert names == {"confidence", "circuit_breaker", "clob_divergence", "volatility"}
    confidence = next(f for f in parsed if f["name"] == "confidence")
    assert confidence["threshold"] == 0.55
    assert confidence["input_value"] == 0.51
    assert confidence["passed"] == 0
    assert row["fee_model"] == "polymarket"
    assert row["registry_load_generation"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_verbose_decision_trace.py -v
```

Expected: `AttributeError: '_write_verbose_trace_for_v2_filters'`.

- [ ] **Step 3: Implement the writer wrapper**

```python
from storage.decision_trace import FilterEval

def _write_verbose_trace_for_v2_filters(
    self,
    *,
    prediction_id: str,
    envelope,
    filter_inputs: dict,
    kelly_raw: Optional[float],
    kelly_capped: Optional[float],
    bankroll_used: Optional[float],
    per_trade_cap_usdc: Optional[float],
    fee_model: str,
    fee_amount: Optional[float],
    platform_gate: Optional[dict],
    warmup: bool,
    consensus_data: Optional[dict],
) -> None:
    """Adapter from the v2 filter dict to FilterEval rows.

    ``filter_inputs`` shape: {name: (threshold, input_value, passed)}.
    """
    filters = [
        FilterEval(name=n, threshold=t, input_value=v, passed=bool(p))
        for n, (t, v, p) in filter_inputs.items()
    ]
    self.decision_trace.write(
        prediction_id=prediction_id,
        filters=filters,
        kelly_raw=kelly_raw, kelly_capped=kelly_capped,
        bankroll_used=bankroll_used,
        per_trade_cap_usdc=per_trade_cap_usdc,
        fee_model=fee_model, fee_amount=fee_amount,
        platform_gate=platform_gate,
        warmup=warmup, consensus_data=consensus_data,
        policy_config_hash=envelope.policy_config_hash,
        calibration_map_hash=envelope.calibration_map_hash,
        registry_load_generation=envelope.registry_load_generation,
    )
```

In `_run_predictions`, immediately after `_record_compact_decision`, build the `filter_inputs` dict from the v2 filter eval (the same data that fed `_check_filters`) and call `_write_verbose_trace_for_v2_filters`. The Kelly inputs come from the existing Kelly computation in v2; the fee model is `"polymarket"` for paper rows and `"kalshi_maker"` / `"kalshi_taker"` when dispatched live.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_verbose_decision_trace.py -v
python -m pytest tests/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/trading/paper_trader.py \
        ofi-lab-v3/tests/test_verbose_decision_trace.py
git commit -m "v3: verbose decision trace per prediction"
```

---

### Task 25: Migration script `scripts/migrate_jsonl_to_sqlite.py`

**Files:**
- Create: `ofi-lab-v3/scripts/migrate_jsonl_to_sqlite.py`
- Create: `ofi-lab-v3/tests/test_migration.py`
- Create: `ofi-lab-v3/tests/fixtures/v2_logs/` — small synthetic JSONL fixtures

Imports historical v2 data into the v3 SQLite schema. Uses `merge_predictions_with_resolutions` and `merge_trades_with_resolutions` from the preserved legacy `trading/ledger.py` to reconstruct full records, then maps them into the new schema.

Provenance backfill rules:
- `model_name`: rewrite `h300` → `900s_btc_v3_20260315`, `h60` → `60s_btc_v3_20260315`, `h60_v3` → `60s_btc_v3d_20260315` (legacy aliases collapse).
- `model_artifact_hash`: SHA-256 of the artifact at the path encoded in `freeze-20260428/` if available, else the literal string `"v2_unknown"` padded to 64 chars (still 64 chars so the schema CHECK passes; documented as legacy).
- `feature_names_hash`: hash of the v2 `V3_FEATURE_COLS`.
- `feature_version`: `"v3"` for h300/h60, `"v3d"` for `h60_v3`.
- `training_horizon_seconds`: 900 for h300, 60 for h60/h60_v3 — but every legacy row was resolved at +900s so `market_window_seconds=900` and `resolution_type="native"` for h300, `resolution_type="evaluation"` for h60/h60_v3 (legacy data does not contain native h60 resolutions).
- `train_*` dates: from config metadata (T17).
- `registry_load_generation`: 0.
- `policy_config_hash`: derived from a single canonical "v2 final" snapshot reconstructed from `/data/kalshi.env`, hashed once.
- `decision_policy_version`: 0.
- `calibration_map_hash`: hashed from the v2 `/data/calibration.json` at migration time.
- `platform`: `"paper"` for predictions/paper_trades, `"kalshi"` for kalshi_orders.
- `warmup`: 0 (legacy did not track this; documented).

- [ ] **Step 1: Create fixtures**

```bash
mkdir -p ofi-lab-v3/tests/fixtures/v2_logs
```

Add `tests/fixtures/v2_logs/predictions_h300.jsonl`:

```jsonl
{"record_type":"prediction","prediction_id":"p1","ts_model_ran_ms":1700000000000,"ts_contract_open_ms":1700000000000,"symbol":"BTCUSDT","pred_proba":0.55,"pred_direction":"up","above_threshold":true,"price_at_open":60000.0}
{"record_type":"resolution","prediction_id":"p1","ts_contract_close_ms":1700000900000,"price_at_close":60100.0,"contract_result":"up","prediction_correct":true,"contract_duration_seconds":900}
```

Add `tests/fixtures/v2_logs/paper_trades_h300.jsonl`:

```jsonl
{"record_type":"trade_entry","trade_id":"t1","prediction_id":"p1","ts_model_ran_ms":1700000000000,"ts_contract_open_ms":1700000000000,"symbol":"BTCUSDT","pred_proba":0.55,"pred_direction":"up","confidence_threshold":0.52,"contract_duration_seconds":900,"price_at_open":60000.0,"simulated_stake_usdc":10.0}
{"record_type":"trade_resolution","trade_id":"t1","prediction_id":"p1","ts_contract_close_ms":1700000900000,"price_at_close":60100.0,"contract_result":"up","prediction_correct":true,"gross_pnl":8.07,"fee_paid":0.18,"net_pnl":7.89,"trade_result":"win","pnl_method":"binary_polymarket"}
```

- [ ] **Step 2: Write the failing test**

```python
# ofi-lab-v3/tests/test_migration.py
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "v2_logs"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_jsonl_to_sqlite.py"


def test_migration_imports_predictions_and_trades(tmp_path):
    db = tmp_path / "v3.db"
    res = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--db", str(db),
         "--source", str(FIXTURES)],
        capture_output=True, text=True, check=False,
    )
    assert res.returncode == 0, res.stderr
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    preds = conn.execute("SELECT * FROM predictions").fetchall()
    assert len(preds) == 1
    p = preds[0]
    assert p["model_name"] == "900s_btc_v3_20260315"
    assert p["resolution_type"] == "native"
    assert p["resolved"] == 1
    assert p["prediction_correct"] == 1
    assert p["price_at_close"] == 60100.0
    trades = conn.execute("SELECT * FROM paper_trades").fetchall()
    assert len(trades) == 1
    assert trades[0]["resolved"] == 1
    assert abs(trades[0]["net_pnl"] - 7.89) < 1e-9


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "v3.db"
    cmd = [sys.executable, str(SCRIPT), "--db", str(db),
           "--source", str(FIXTURES)]
    subprocess.run(cmd, check=True, capture_output=True)
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    import sqlite3
    conn = sqlite3.connect(str(db))
    n_preds = conn.execute(
        "SELECT count(*) FROM predictions"
    ).fetchone()[0]
    assert n_preds == 1


def test_migration_summary_logged(tmp_path, capsys):
    db = tmp_path / "v3.db"
    res = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--source", str(FIXTURES)],
        capture_output=True, text=True, check=True,
    )
    assert "predictions imported: 1" in res.stdout
    assert "paper_trades imported: 1" in res.stdout
```

- [ ] **Step 3: Run test to verify it fails**

```bash
python -m pytest tests/test_migration.py -v
```

Expected: `FileNotFoundError`.

- [ ] **Step 4: Implement the migration script**

```python
# ofi-lab-v3/scripts/migrate_jsonl_to_sqlite.py
"""Migrate v2 JSONL ledger files into v3 SQLite.

One-shot importer. Idempotent — uses the schema's idempotency index
plus pre-insert SELECT checks. Provenance fields are backfilled per
the rules in Plan A Task 25.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from storage.db import open_database, init_schema, DEFAULT_DB_PATH
from storage.provenance import (
    ProvenanceEnvelope, sha256_canonical_json, feature_names_hash,
    calibration_map_hash, sha256_file,
)
from storage.registry_state import RegistryState
from trading.ledger import (
    read_records, merge_predictions_with_resolutions,
    merge_trades_with_resolutions,
)
from trading.live_features import V3_FEATURE_COLS

LEGACY_NAME_MAP = {
    "h300":   "900s_btc_v3_20260315",
    "h60":    "60s_btc_v3_20260315",
    "h60_v3": "60s_btc_v3d_20260315",
}

LEGACY_FEATURE_VERSION = {
    "h300": "v3", "h60": "v3", "h60_v3": "v3d",
}

LEGACY_HORIZON_SECONDS = {"h300": 900, "h60": 60, "h60_v3": 60}


def _legacy_envelope(legacy_name, calibration_path, policy_path):
    feat_hash = feature_names_hash(V3_FEATURE_COLS)
    if Path(calibration_path).exists():
        import json
        cal = json.loads(Path(calibration_path).read_text())
        cal_hash = calibration_map_hash(cal)
    else:
        cal_hash = "0" * 64
    if Path(policy_path).exists():
        import json
        snap = json.loads(Path(policy_path).read_text())
        policy_hash = sha256_canonical_json(snap)
    else:
        policy_hash = "0" * 64
    artifact_hash = "v2_legacy_" + "0" * (64 - len("v2_legacy_"))
    return ProvenanceEnvelope(
        model_name=LEGACY_NAME_MAP[legacy_name],
        model_artifact_hash=artifact_hash,
        feature_names_hash=feat_hash,
        feature_version=LEGACY_FEATURE_VERSION[legacy_name],
        training_horizon_seconds=LEGACY_HORIZON_SECONDS[legacy_name],
        train_window_start="2025-04-01",
        train_window_end="2026-03-15",
        train_cutoff="2026-03-15",
        registry_load_generation=0,
        policy_config_hash=policy_hash,
        decision_policy_version=0,
        calibration_map_hash=cal_hash,
        platform="paper",
    )


def _import_predictions(conn, source_dir, legacy_name, env, summary):
    path = source_dir / f"predictions_{legacy_name}.jsonl"
    if not path.exists():
        return
    merged = merge_predictions_with_resolutions(read_records(path))
    n = 0
    for rec in merged:
        pid = rec["prediction_id"]
        existing = conn.execute(
            "SELECT 1 FROM predictions WHERE prediction_id = ?", (pid,)
        ).fetchone()
        if existing:
            continue
        ts_open = int(rec["ts_contract_open_ms"])
        ts_resolve = ts_open + 900_000  # legacy was always 900s
        conn.execute(
            "INSERT INTO predictions ("
            " prediction_id, model_name, model_artifact_hash, feature_names_hash,"
            " feature_version, training_horizon_seconds, train_window_start,"
            " train_window_end, train_cutoff, registry_load_generation,"
            " policy_config_hash, decision_policy_version, calibration_map_hash,"
            " symbol, market_window_seconds, resolution_type,"
            " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
            " pred_proba_raw, pred_proba_calibrated, pred_direction,"
            " above_threshold, warmup, platform,"
            " price_at_open, price_at_close, contract_result,"
            " prediction_correct, resolved, ts_resolved_ms"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pid, env.model_name, env.model_artifact_hash, env.feature_names_hash,
                env.feature_version, env.training_horizon_seconds,
                env.train_window_start, env.train_window_end, env.train_cutoff,
                env.registry_load_generation, env.policy_config_hash,
                env.decision_policy_version, env.calibration_map_hash,
                rec.get("symbol", "BTCUSDT"), 900, "native",
                int(rec["ts_model_ran_ms"]), ts_open, ts_resolve,
                float(rec["pred_proba"]), float(rec["pred_proba"]),
                rec["pred_direction"], int(rec.get("above_threshold", 0)),
                0, "paper",
                rec.get("price_at_open"), rec.get("price_at_close"),
                rec.get("contract_result"),
                int(rec["prediction_correct"]) if rec.get("prediction_correct") is not None else None,
                1 if rec.get("contract_result") is not None else 0,
                rec.get("ts_contract_close_ms"),
            ),
        )
        n += 1
    summary["predictions"] += n


def _import_paper_trades(conn, source_dir, legacy_name, env, summary):
    path = source_dir / f"paper_trades_{legacy_name}.jsonl"
    if not path.exists():
        return
    merged = merge_trades_with_resolutions(read_records(path))
    n = 0
    for rec in merged:
        tid = rec["trade_id"]
        existing = conn.execute(
            "SELECT 1 FROM paper_trades WHERE trade_id = ?", (tid,)
        ).fetchone()
        if existing:
            continue
        ts_open = int(rec["ts_contract_open_ms"])
        ts_resolve = ts_open + 900_000
        conn.execute(
            "INSERT INTO paper_trades ("
            " trade_id, prediction_id,"
            " model_name, model_artifact_hash, policy_config_hash,"
            " decision_policy_version, calibration_map_hash,"
            " registry_load_generation, feature_version,"
            " training_horizon_seconds,"
            " symbol, market_window_seconds, resolution_type,"
            " ts_model_ran_ms, ts_contract_open_ms, ts_resolve_at_ms,"
            " pred_proba_raw, pred_proba_calibrated, pred_direction,"
            " confidence_threshold_used, simulated_stake_usdc,"
            " warmup, platform,"
            " decision_outcome,"
            " price_at_open, price_at_close, contract_result,"
            " prediction_correct, gross_pnl, fee_paid, net_pnl,"
            " trade_result, pnl_method, resolved, ts_resolved_ms"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                tid, rec["prediction_id"],
                env.model_name, env.model_artifact_hash,
                env.policy_config_hash, env.decision_policy_version,
                env.calibration_map_hash, env.registry_load_generation,
                env.feature_version, env.training_horizon_seconds,
                rec.get("symbol", "BTCUSDT"), 900, "native",
                int(rec["ts_model_ran_ms"]), ts_open, ts_resolve,
                float(rec["pred_proba"]), float(rec["pred_proba"]),
                rec["pred_direction"],
                float(rec.get("confidence_threshold", 0.52)),
                float(rec.get("simulated_stake_usdc", 10.0)),
                0, "paper",
                "executed",
                rec.get("price_at_open"), rec.get("price_at_close"),
                rec.get("contract_result"),
                int(rec["prediction_correct"]) if rec.get("prediction_correct") is not None else None,
                rec.get("gross_pnl"), rec.get("fee_paid"), rec.get("net_pnl"),
                rec.get("trade_result"), rec.get("pnl_method"),
                1 if rec.get("contract_result") is not None else 0,
                rec.get("ts_contract_close_ms"),
            ),
        )
        n += 1
    summary["paper_trades"] += n


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DEFAULT_DB_PATH)
    p.add_argument("--source", required=True,
                   help="Directory containing v2 JSONL files.")
    p.add_argument("--calibration-path", default="/data/calibration.json")
    p.add_argument("--policy-path", default="/data/policy_v2_final.json")
    args = p.parse_args()

    conn = open_database(args.db)
    init_schema(conn)
    RegistryState(conn).bootstrap_if_empty(reason="migration import")
    src = Path(args.source)
    summary = {"predictions": 0, "paper_trades": 0}
    for legacy in ("h300", "h60", "h60_v3"):
        env = _legacy_envelope(legacy, args.calibration_path, args.policy_path)
        _import_predictions(conn, src, legacy, env, summary)
        _import_paper_trades(conn, src, legacy, env, summary)
    print(f"predictions imported: {summary['predictions']}")
    print(f"paper_trades imported: {summary['paper_trades']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m pytest tests/test_migration.py -v
```

Expected: 3 passed.

- [ ] **Step 6: Run a dry-run against a snapshot of real v2 data**

```bash
# Pull a recent snapshot off the VPS
mkdir -p /tmp/v2_snapshot
rsync -av -e "ssh -o StrictHostKeyChecking=no -i ~/.ssh/id_vps_n2" \
  johnny@34.67.75.48:/data/logs/predictions_*.jsonl \
  johnny@34.67.75.48:/data/logs/paper_trades_*.jsonl \
  /tmp/v2_snapshot/

cd /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3
python -m scripts.migrate_jsonl_to_sqlite \
  --db /tmp/v3_dryrun.db \
  --source /tmp/v2_snapshot

sqlite3 /tmp/v3_dryrun.db "SELECT model_name, count(*) FROM predictions GROUP BY model_name;"
```

Expected output: counts roughly match the v2 line counts from the audit (`predictions_h300.jsonl` ~15K, `predictions_h60.jsonl` ~15K — divided by 2 since each prediction had a separate resolution row in v2).

- [ ] **Step 7: Commit**

```bash
git add ofi-lab-v3/scripts/migrate_jsonl_to_sqlite.py \
        ofi-lab-v3/tests/test_migration.py \
        ofi-lab-v3/tests/fixtures/v2_logs/
git commit -m "v3: migration script JSONL → SQLite with provenance backfill"
```

---

### Task 26: `Dockerfile.v3` and entrypoint

**Files:**
- Create: `ofi-lab-v3/Dockerfile.v3`
- Create: `ofi-lab-v3/scripts/entrypoint_v3.sh`
- Test: container build smoke test (manual; no automated test)

The new image is `golden-goose-v3:foundation`. The entrypoint runs `init_db.py` if `/data/v3.db` is missing and otherwise launches the paper trader. The container must NOT auto-enable Kalshi live trading on first boot — `KALSHI_LIVE_ENABLED` defaults to `false`, and the migrated baseline is the only model with `kalshi_dispatch_enabled=true` in config metadata.

- [ ] **Step 1: Create the Dockerfile**

```dockerfile
# ofi-lab-v3/Dockerfile.v3
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod +x scripts/entrypoint_v3.sh

ENV STORAGE_DB_PATH=/data/v3.db
ENV KALSHI_LIVE_ENABLED=false

ENTRYPOINT ["scripts/entrypoint_v3.sh"]
```

- [ ] **Step 2: Create the entrypoint**

```bash
#!/usr/bin/env bash
# ofi-lab-v3/scripts/entrypoint_v3.sh
set -euo pipefail

if [ ! -f "${STORAGE_DB_PATH}" ]; then
  echo "[entrypoint] bootstrapping ${STORAGE_DB_PATH}"
  python -m scripts.init_db --db "${STORAGE_DB_PATH}" \
    --bootstrap-reason "container start"
fi

# If a v2 snapshot directory is mounted at /data/v2_snapshot, run the
# migration on first boot. Idempotent.
if [ -d "/data/v2_snapshot" ] && [ -z "${SKIP_MIGRATION:-}" ]; then
  echo "[entrypoint] running migration"
  python -m scripts.migrate_jsonl_to_sqlite \
    --db "${STORAGE_DB_PATH}" --source /data/v2_snapshot
fi

exec python -m trading.paper_trader "$@"
```

- [ ] **Step 3: Build the image locally**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3
docker build -t golden-goose-v3:foundation -f Dockerfile.v3 .
```

Expected: clean build.

- [ ] **Step 4: Smoke-run the image (no Kalshi, no Bybit) — verify entrypoint bootstraps and paper trader fails fast on missing model**

```bash
docker run --rm \
  -v /tmp/v3_smoke_data:/data \
  golden-goose-v3:foundation \
  --help 2>&1 | head -40
```

Expected: paper trader CLI help is printed; `/tmp/v3_smoke_data/v3.db` is created.

- [ ] **Step 5: Commit**

```bash
git add ofi-lab-v3/Dockerfile.v3 ofi-lab-v3/scripts/entrypoint_v3.sh
git commit -m "v3: Dockerfile.v3 + entrypoint with bootstrap + migration"
```

---

### Task 27: End-to-end smoke harness — replay 60 minutes of v2 features through v3

**Files:**
- Create: `ofi-lab-v3/scripts/replay_v2_features.py`
- Create: `ofi-lab-v3/tests/test_replay_smoke.py`

**Goal of this task:** confirm that the v3 paper trader, when fed the same minute-bar feature vectors v2 used, produces well-formed predictions with full provenance, native + evaluation rows, calibration outcomes only on native, and a populated decision trace. We do **not** assert byte-equal predictions vs v2 (per the clarified preference); we assert structural correctness.

- [ ] **Step 1: Implement `replay_v2_features.py`**

```python
# ofi-lab-v3/scripts/replay_v2_features.py
"""Feed minute-bar feature vectors from a v2 parquet directly into the
v3 PaperTrader's scoring helpers (skipping the WebSocket layer).

This is a deterministic offline harness for plan-A correctness checks.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading.paper_trader import PaperTrader
from storage.db import open_database, init_schema
import config


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--features-parquet", required=True)
    p.add_argument("--model-path", required=True)
    p.add_argument("--db", default="/tmp/v3_replay.db")
    p.add_argument("--max-rows", type=int, default=60)  # ~60 minutes
    args = p.parse_args()

    df = pl.read_parquet(args.features_parquet).head(args.max_rows)
    trader = PaperTrader(
        model_paths={"900s_btc_v3_20260315": args.model_path},
        log_dir="/tmp/v3_replay_logs",
        confidence_threshold=0.55,
    )
    trader._db_conn = open_database(args.db)
    init_schema(trader._db_conn)

    rows_emitted = 0
    for row in df.iter_rows(named=True):
        boundary_ms = int(row["ts_ms"])
        # Score with the loaded model directly (bypasses live feature
        # computer).
        feat_vec = [row[c] for c in trader.feature_names["900s_btc_v3_20260315"]]
        import numpy as np
        proba = float(trader.models["900s_btc_v3_20260315"].predict(
            np.array(feat_vec).reshape(1, -1)
        )[0])
        direction = "up" if proba > 0.5 else "down"
        trader._emit_prediction_rows(
            model_name="900s_btc_v3_20260315", symbol="BTCUSDT",
            boundary_ms=boundary_ms, ts_model_ran_ms=boundary_ms,
            pred_proba_raw=proba, pred_proba_calibrated=proba,
            pred_direction=direction, above_threshold=(proba >= 0.55),
            warmup=False, platform="paper",
            price_at_open=float(row["mid_price"]),
        )
        rows_emitted += 1
    print(f"emitted {rows_emitted} prediction sets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Write the smoke test**

```python
# ofi-lab-v3/tests/test_replay_smoke.py
import subprocess
import sys
from pathlib import Path

import pytest


def test_replay_writes_well_formed_rows(tmp_path):
    parquet = Path(__file__).parent / "fixtures" / "btc_minute_bars.parquet"
    model = Path(__file__).parent / "fixtures" / "tiny_model.lgb"
    if not parquet.exists() or not model.exists():
        pytest.skip("replay fixtures missing; produce via T_FIX")
    db = tmp_path / "v3.db"
    res = subprocess.run(
        [sys.executable, "-m", "scripts.replay_v2_features",
         "--features-parquet", str(parquet),
         "--model-path", str(model),
         "--db", str(db),
         "--max-rows", "10"],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    import sqlite3
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    n = conn.execute("SELECT count(*) FROM predictions").fetchone()[0]
    # 10 boundaries × 4 rows (1 native + 3 evaluation) = 40
    assert n == 40
    natives = conn.execute(
        "SELECT count(*) FROM predictions WHERE resolution_type='native'"
    ).fetchone()[0]
    assert natives == 10
    # Every row has full provenance hashes (length 64)
    rows = conn.execute(
        "SELECT model_artifact_hash, feature_names_hash,"
        " policy_config_hash, calibration_map_hash FROM predictions"
    ).fetchall()
    for r in rows:
        assert len(r["model_artifact_hash"]) == 64
        assert len(r["feature_names_hash"]) == 64
        assert len(r["policy_config_hash"]) == 64
        assert len(r["calibration_map_hash"]) == 64
```

The fixture parquet (`tests/fixtures/btc_minute_bars.parquet`) is created once per workstation by sampling from the v2 feature directory; document this in `tests/fixtures/README.md`.

- [ ] **Step 3: Run the smoke test**

```bash
python -m pytest tests/test_replay_smoke.py -v
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add ofi-lab-v3/scripts/replay_v2_features.py \
        ofi-lab-v3/tests/test_replay_smoke.py \
        ofi-lab-v3/tests/fixtures/README.md
git commit -m "v3: replay smoke harness — 60min of v2 features through v3 scoring"
```

---

### Task 28: Final full-suite pass + plan exit

**Files:** none new

- [ ] **Step 1: Run the entire test suite**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3
python -m pytest tests/ -v 2>&1 | tee /tmp/v3_plan_a_test_run.txt
```

Expected: all tests pass. If any v2-specific test fails because it referenced JSONL writers that are now removed, delete the stale test rather than restoring the JSONL path.

- [ ] **Step 2: Confirm the schema invariants by direct query**

```bash
sqlite3 /tmp/v3_replay.db <<'SQL'
SELECT 'predictions' AS t, count(*) AS n FROM predictions
UNION ALL SELECT 'paper_trades', count(*) FROM paper_trades
UNION ALL SELECT 'decision_traces', count(*) FROM decision_traces
UNION ALL SELECT 'calibration_outcomes', count(*) FROM calibration_outcomes;

-- No row should have a null hash
SELECT count(*) AS bad FROM predictions
 WHERE length(model_artifact_hash) <> 64
    OR length(policy_config_hash) <> 64
    OR length(calibration_map_hash) <> 64;
SQL
```

Expected: counts are non-zero where the replay scored predictions; `bad = 0`.

- [ ] **Step 3: Tag the milestone**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem
git tag v3-plan-a-foundation-complete
git log --oneline -30
```

- [ ] **Step 4: Hand off to Plan B**

Plan B (Multi-Model + Filters + Lifecycle) builds on this foundation. It depends on:
- The `model_metadata` dict in `config.py` being the single source of model identity (Plan B replaces it with a JSON registry — drop-in change).
- `RegistryState.increment` being callable on hot reload.
- `PolicySnapshot.capture` being called whenever filters mutate.
- Pending-queue composite keys including `registry_load_generation` (already in place).
- `resolution_type='native'` partial index existing for fast lifecycle queries.

---

## Self-Review

This section was run against the final plan after writing all 28 tasks.

### 1. Spec coverage

Cross-checked every Builder Prompt phase + steering item against tasks. Items not in Plan A are explicitly listed under "Out of scope" at the top with their target plan. Three items deserve explicit notes:

- **Phase 1.3 Feature parquet unchanged.** No task; verified by Task 1's structural-parity step.
- **Steering §10b native vs evaluation.** Covered across T9 (planner), T15 (writer), T18 (paper trader emission), T19 (resolution loop), and the `idx_pred_native_for_decay` partial index in T3.
- **Steering §10h pending queue model cleanup.** The primitive (`prune_for_models`) ships in T14. The actual cleanup-on-reload caller is in Plan B's hot reload flow, since Plan A does not yet support reloads.

### 2. Placeholder scan

No `TBD`, `TODO`, `fill in details`, or `add appropriate error handling` instances were left in the plan. Every task that changes code shows the full code; every test step includes the actual test code; every command line is concrete.

The migration script's "v2_legacy_..." artifact-hash placeholder is a documented sentinel value (64-char hex string), not a plan placeholder — it represents the legitimate "we never had the original artifact" case for migrated rows.

### 3. Type / signature consistency

Verified by walking the plan top-to-bottom:

- `ProvenanceEnvelope` field set declared in T4 is the same field set used in T10, T17, T18, T20, T24, T25.
- `SQLiteLedger.log_prediction_set` signature in T10 is the same signature called in T18 and T25.
- `SQLiteLedger.log_paper_trade` keyword arguments in T11 match T22's `_check_trade_resolutions_v3` writer call.
- `PendingEntry` constructor signature in T14 matches the call sites in T18 and (implicitly) T19's resolution loop.
- `_build_envelope` signature `(model_name, platform)` is consistent in T17, T18, T24, T27.
- `kalshi_dispatch_eligible` signature `(model_name, symbol, market_window_seconds)` is consistent between T20 and the call site reference in T21's warmup early-return note.

### 4. Risk-tier alignment with steering §9

| Risk Area | Tasks |
|---|---|
| Provenance correctness | T4, T17, T27 |
| Storage migration | T25 (with synthetic + real-snapshot dry-run) |
| Multi-window resolution | T9, T18, T19 |
| Decision trace completeness | T23 (compact), T24 (verbose) |
| Hot reload safety primitives | T5 (generation counter), T14 (composite key) |
| Calibration MIN_SAMPLES + per-model | T15 |

Filter-pipeline-EV and lifecycle-hysteresis tests are explicitly in Plan B because the components do not yet exist in Plan A.

### 5. Anti-patterns avoided

- No "this works the same as Task N" cross-references — every code block is self-contained.
- No tasks longer than ~5 minutes of action steps; T18 and T22 are the longest, both ~10 minutes.
- TDD discipline preserved: every behavioral task has a failing-test step before implementation.
- Frequent commits: every task ends with a commit step.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-09-v3-plan-a-foundation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Plan-A scope (28 tasks) is well-suited to this because tasks are independent and many can be completed in parallel by different subagents (T2/T3/T4/T5/T6/T7/T8 have no inter-dependencies; T9 is also independent).

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**




