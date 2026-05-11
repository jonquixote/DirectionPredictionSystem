# Model A v3 — Fleet Training Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Train fleet of `4 symbols × 7 horizons × 3 train-day windows = 84 models`. All register into v3 `model_registry`. All score every boundary simultaneously. Compare each `(model, trading_window)` cell to identify optimal model per (symbol, trading-window) — same way h300 BTC beats other BTC models at 900s trading.

**Architecture:** No model architecture changes. Only `--horizon`, `--symbol`, `--train-days` vary. Reuse existing `validation/retrain.py` + `validation/run_training.py`. Add (a) data catchup gate, (b) batched training driver, (c) auto-registration into `model_registry.json` AND SQLite `model_registry` table, (d) paper trader switches from CLI-flag model loading to registry-driven, (e) evaluation windows configured per-model in registry.

**Scope axes:**
- **Symbols:** BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT
- **Horizons:** 60, 180, 300, 600, 900, 1200, 1800 seconds
- **Train day windows:** 90, 180, 330 days
- **Evaluation windows (trading windows):** 300, 900, 1800s = 5/15/30 min — every model scored at every window
- **Feature version:** v3 (frozen, 43 features, horizon-agnostic)

**Pre-deploy state (verified 2026-05-11):**
- v3 paper trader live, kill switch resumed
- Baseline h300_btc only; rest of fleet missing
- Data freshness: last training cutoff was 2026-03-04 per `validation/retrain_v3.py:67`. **Gap ~67 days.** User notes data should already exist up to ~2 weeks ago — need to confirm + fill remainder.
- `/data/features_v3/` on VPS (not in local checkout) — investigation via SSH required to confirm freshness per symbol.

---

## Investigation answers (from haiku run 2026-05-11)

| Q | Answer |
|---|---|
| Training entry | `validation/retrain.py` → `validation/run_training.py`. CLI: `--horizon`, `--symbol`, `--feature-version v3`, `--train-days 330`, `--train-end YYYY-MM-DD`, `--feature-dir`, `--output-dir` |
| Feature schema | V3_FEATURE_COLS = 43 features. Horizon-agnostic. Single set per (symbol, minute). EWM-z mid_price = price-level invariant |
| Artifact layout | `{OUT}/model.lgb`, `feature_names.json`, `metrics.json`, `config_snapshot.json` |
| Auto-register | **None.** `retrain_pipeline.sh:101` says "review artifacts and add to model_registry.json" manually. Plan B `model_registry` SQLite table not auto-populated by training either |
| Paper trader multi-model | Currently hardcoded `--h60-model`/`--h300-model`. Underlying `dict[name, path]` load loop is generic. Tests confirm arbitrary horizons supported |
| Evaluation windows | `CONTRACT_DURATIONS = [300, 900]` hardcoded in `paper_trader.py:81`. `window_planner.plan_resolution_rows()` already emits native + evaluation rows but list not param at train time |
| Symbol coverage | XRPUSDT supported end-to-end. Download, features, range_computer all generalize |
| Data download | `data/download_klines.py` — Bybit REST, gap-safe (≥1440 rows/day), supports all 4 symbols. No cron |
| 330-day default | `--train-days 330` is CLI default. `retrain_v3.py` hardcoded 63-day window for one-off backtest only. Existing models' actual training window: unknown without inspecting `/data/models/latest_*/metrics.json` on VPS |

---

## File Structure

### New files
- `scripts/data_catchup.py` — fetch klines + rebuild features for any symbol up to `--through` date. Wraps existing `download_klines` + features rebuild.
- `scripts/train_fleet.py` — driver that iterates (symbol, horizon, train_days), shells out to `validation/retrain.py`, writes results to `/data/models/fleet/{name}/`, registers each into registry.
- `scripts/register_model.py` — reads `{OUT}/metrics.json` + `config_snapshot.json`, upserts row into `model_registry` SQLite table AND `model_registry.json` JSON file. Computes `artifact_hash = sha256(model.lgb)`. Sets `is_baseline=False` for all fleet models (h300_btc baseline stays untouched).
- `trading/fleet_loader.py` — replaces hardcoded CLI flags. Queries `model_registry` table for `paper_active=1` rows, loads each into PaperTrader's models dict at boot.
- `tests/test_train_fleet.py` — driver invariants (resume after crash, idempotency, fleet-only registers Non-baseline).
- `tests/test_register_model.py` — registry upsert correctness.
- `tests/test_fleet_loader.py` — verifies loader picks up registry rows.

### Modified files
- `storage/schema.sql` — add `evaluation_windows TEXT` column (JSON array) to `model_registry`. Add `artifact_path TEXT`, `feature_names_path TEXT`, `artifact_hash TEXT`, `train_window_start TEXT`, `train_window_end TEXT`, `feature_version TEXT` columns.
- `trading/paper_trader.py` — if no `--*-model` flags passed, fall through to `fleet_loader`. `CONTRACT_DURATIONS` becomes runtime-derived from union of `evaluation_windows` in registry (default `[300, 900, 1800]`).
- `validation/retrain.py` — accept `--symbol`, `--train-days` already; add `--evaluation-windows "300,900,1800"` flag, write to `metrics.json`.

---

## Cost / time estimate

**Per-model training time:** Unknown without empirical run. LightGBM on 43 features × 1m bars × 330 days ≈ 475k rows → minutes per model on VPS CPU (no GPU).

**Fleet total:** 84 models × ~5 min/model = ~7 hours wall-clock if serial. **Parallelize** — VPS likely has many cores. 4-way parallelism = ~1.75 hours. **Phase batches to avoid OOM** (each booster takes ~50-100MB RAM).

**Disk:** 84 × ~5MB artifact + features ≈ <1 GB. /data has 93G free.

---

# Phase 0 — Pre-train gates

## Task F1: Confirm data freshness per symbol

**Files:** none — investigation step.

- [ ] **Step 1: SSH and survey existing feature data**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 <<'EOF'
for sym in BTCUSDT ETHUSDT SOLUSDT XRPUSDT; do
  echo "=== $sym ==="
  ls /data/features_v3/$sym/ 2>/dev/null | tail -3
  ls /data/parquet/klines/${sym}_klines_1m.parquet 2>/dev/null && \
    python3 -c "import pyarrow.parquet as pq; t=pq.read_table('/data/parquet/klines/${sym}_klines_1m.parquet'); print('rows=',t.num_rows,'newest_ts=',t.column('ts_ms')[-1].as_py() if t.num_rows else 'empty')"
done
EOF
```
Expected: per-symbol newest day file + kline parquet row count + newest timestamp. Operator notes any gaps.

- [ ] **Step 2: Determine training end date**

Use `today - 1 full day` UTC. For 2026-05-11, that's `2026-05-10`. Reserve last 14 days for val (30d) + test (14d) overlap if needed; baseline strategy: `--train-end 2026-04-26`, val 2026-04-27 → 2026-05-10 (14d), test held out for live observation.

Decision logged: `train_end = 2026-04-26`, `val_days=14`, `test_days=0` (live observation replaces test).

## Task F2: Fill data gaps

**Files:** Create `scripts/data_catchup.py`.

The existing `data/download_klines.py` is gap-safe but only handles klines. Features rebuild from klines + L2. If L2 not preserved, features after 2026-03-04 may be missing.

- [ ] **Step 1: Inventory missing days**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 << 'EOF'
cd /home/johnny/ofi-lab-v3
for sym in BTCUSDT ETHUSDT SOLUSDT XRPUSDT; do
  echo "=== $sym ==="
  ls /data/features_v3/$sym/ 2>/dev/null | awk -F'_' '{print $1}' | sort -u | tail -5
  echo "First missing day after newest:"
  # diff between today-1 and newest
done
EOF
```

- [ ] **Step 2: Re-run feature build for any missing days**

Find the feature build script (haiku didn't surface canonical name; likely `validation/build_features_v3.py` or similar):

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cd /home/johnny/ofi-lab-v3 && ls validation/build_features* feature_engineering/ scripts/build_features* 2>&1 | head -10'
```

If feature build requires raw L2 we no longer have, **stop here** and document the constraint. May need to limit fleet train_end to 2026-03-04 (the last training cutoff).

- [ ] **Step 3: Fill kline gaps**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cd /home/johnny/ofi-lab-v3 && PYTHONPATH=. .venv/bin/python -m data.download_klines --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT --through 2026-05-10'
```

- [ ] **Step 4: Verify completeness gate**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cd /home/johnny/ofi-lab-v3 && PYTHONPATH=. .venv/bin/python -c "from validation.completeness import check_feature_coverage; print(check_feature_coverage(\"/data/features_v3/BTCUSDT\", start=\"2025-06-01\", end=\"2026-05-10\"))"'
```
For each symbol. `max_consecutive_missing > 2` aborts training.

- [ ] **Step 5: Commit catchup script**

If new scripts added: commit + rsync to VPS.

## Task F3: Schema extension for fleet registry fields

**Files:** `storage/schema.sql`, migration script.

Add columns to `model_registry`:

```sql
ALTER TABLE model_registry ADD COLUMN artifact_path TEXT;
ALTER TABLE model_registry ADD COLUMN feature_names_path TEXT;
ALTER TABLE model_registry ADD COLUMN artifact_hash TEXT;
ALTER TABLE model_registry ADD COLUMN train_window_start TEXT;
ALTER TABLE model_registry ADD COLUMN train_window_end TEXT;
ALTER TABLE model_registry ADD COLUMN train_days INTEGER;
ALTER TABLE model_registry ADD COLUMN feature_version TEXT DEFAULT 'v3';
ALTER TABLE model_registry ADD COLUMN evaluation_windows TEXT DEFAULT '[300,900,1800]';
```

- [ ] **Step 1: Write migration**

`storage/migrations/0002_model_registry_fleet_cols.sql`:

```sql
BEGIN;
ALTER TABLE model_registry ADD COLUMN artifact_path TEXT;
ALTER TABLE model_registry ADD COLUMN feature_names_path TEXT;
ALTER TABLE model_registry ADD COLUMN artifact_hash TEXT;
ALTER TABLE model_registry ADD COLUMN train_window_start TEXT;
ALTER TABLE model_registry ADD COLUMN train_window_end TEXT;
ALTER TABLE model_registry ADD COLUMN train_days INTEGER;
ALTER TABLE model_registry ADD COLUMN feature_version TEXT DEFAULT 'v3';
ALTER TABLE model_registry ADD COLUMN evaluation_windows TEXT DEFAULT '[300,900,1800]';
CREATE INDEX IF NOT EXISTS idx_reg_symbol_horizon ON model_registry(symbol, training_horizon_seconds);
COMMIT;
```

- [ ] **Step 2: Apply on VPS**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'sqlite3 /data/v3.db < /home/johnny/ofi-lab-v3/storage/migrations/0002_model_registry_fleet_cols.sql || echo "no sqlite3 CLI — fall back to python"'
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cd /home/johnny/ofi-lab-v3 && PYTHONPATH=. .venv/bin/python -c "import sqlite3; c=sqlite3.connect(\"/data/v3.db\"); c.executescript(open(\"storage/migrations/0002_model_registry_fleet_cols.sql\").read()); print(\"migrated\")"'
```

- [ ] **Step 3: Verify columns added**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cd /home/johnny/ofi-lab-v3 && PYTHONPATH=. .venv/bin/python -c "import sqlite3; c=sqlite3.connect(\"/data/v3.db\"); print([r[1] for r in c.execute(\"PRAGMA table_info(model_registry)\").fetchall()])"'
```

- [ ] **Step 4: Update local schema.sql to match**

Append the new columns to the `CREATE TABLE model_registry` block in `storage/schema.sql` so fresh installs include them. Commit.

---

# Phase 1 — Training infrastructure

## Task F4: `register_model.py` — ingests training output, writes registry

**Files:** Create `scripts/register_model.py`. Test: `tests/test_register_model.py`.

- [ ] **Step 1: Failing test**

```python
def test_register_model_writes_sqlite_row(tmp_path, db_conn):
    # Create fake training output
    out = tmp_path / "fleet" / "h180_xrp_v3_330d"
    out.mkdir(parents=True)
    (out / "model.lgb").write_bytes(b"FAKE_LGB_DATA")
    (out / "feature_names.json").write_text(json.dumps(["mlofi", "ofi"]))
    (out / "metrics.json").write_text(json.dumps({
        "horizon_seconds": 180,
        "symbol": "XRPUSDT",
        "train_days": 330,
        "train_window_start": "2025-05-31",
        "train_window_end": "2026-04-26",
        "auc": 0.512,
        "brier": 0.247,
        "feature_version": "v3",
    }))

    from scripts.register_model import register_model
    register_model(conn=db_conn, artifact_dir=str(out),
                   evaluation_windows=[300, 900, 1800])

    row = db_conn.execute(
        "SELECT * FROM model_registry WHERE name='h180_xrp_v3_330d'"
    ).fetchone()
    assert row["symbol"] == "XRPUSDT"
    assert row["training_horizon_seconds"] == 180
    assert row["train_days"] == 330
    assert row["is_baseline"] == 0
    assert row["paper_active"] == 1
    assert row["live_eligible"] == 0
    assert json.loads(row["evaluation_windows"]) == [300, 900, 1800]
    assert row["artifact_hash"] is not None
    assert len(row["artifact_hash"]) == 64

def test_register_model_does_not_touch_baseline(db_conn):
    db_conn.execute("INSERT INTO model_registry (name, is_baseline, symbol, training_horizon_seconds) "
                    "VALUES ('h300_btc', 1, 'BTCUSDT', 900)")
    db_conn.commit()
    # ... call register_model on h300_btc artifact ...
    # Verify is_baseline stays 1, lifecycle_state unchanged.
```

- [ ] **Step 2: Implement**

```python
# scripts/register_model.py
"""Register a trained model artifact into model_registry."""
import argparse, hashlib, json, sqlite3, sys, time
from pathlib import Path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def register_model(*, conn: sqlite3.Connection, artifact_dir: str,
                   evaluation_windows: list[int]) -> str:
    art = Path(artifact_dir)
    metrics = json.loads((art / "metrics.json").read_text())
    horizon = metrics["horizon_seconds"]
    symbol = metrics["symbol"]
    train_days = metrics.get("train_days", 330)
    name = f"h{horizon}_{symbol.replace('USDT','').lower()}_v3_{train_days}d"

    artifact_hash = _sha256(art / "model.lgb")

    existing = conn.execute(
        "SELECT is_baseline, lifecycle_state, paper_active, live_eligible "
        "FROM model_registry WHERE name=?", (name,)).fetchone()
    if existing and existing["is_baseline"]:
        # Don't trample baseline state — only refresh artifact pointer
        conn.execute(
            "UPDATE model_registry SET artifact_path=?, feature_names_path=?, "
            "artifact_hash=?, train_window_start=?, train_window_end=?, "
            "train_days=?, feature_version=?, evaluation_windows=? "
            "WHERE name=?",
            (str(art / "model.lgb"), str(art / "feature_names.json"),
             artifact_hash, metrics["train_window_start"],
             metrics["train_window_end"], train_days,
             metrics.get("feature_version", "v3"),
             json.dumps(evaluation_windows), name))
    else:
        conn.execute(
            "INSERT OR REPLACE INTO model_registry "
            "(name, is_baseline, paper_active, live_eligible, lifecycle_state, "
            "symbol, training_horizon_seconds, generation, artifact_path, "
            "feature_names_path, artifact_hash, train_window_start, "
            "train_window_end, train_days, feature_version, evaluation_windows) "
            "VALUES (?, 0, 1, 0, 'active', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, symbol, horizon, str(art / "model.lgb"),
             str(art / "feature_names.json"), artifact_hash,
             metrics["train_window_start"], metrics["train_window_end"],
             train_days, metrics.get("feature_version", "v3"),
             json.dumps(evaluation_windows)))

    # Audit
    conn.execute(
        "INSERT INTO model_audit (model_name, action, by_user, detail) "
        "VALUES (?, 'register', ?, ?)",
        (name, "fleet_trainer", json.dumps(metrics)))
    conn.commit()
    return name


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="/data/v3.db")
    p.add_argument("--artifact-dir", required=True)
    p.add_argument("--evaluation-windows", default="300,900,1800")
    args = p.parse_args()
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    name = register_model(
        conn=conn, artifact_dir=args.artifact_dir,
        evaluation_windows=[int(x) for x in args.evaluation_windows.split(",")])
    print(f"registered: {name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run + commit**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3 && source .venv/bin/activate && python -m pytest tests/test_register_model.py -v
git add scripts/register_model.py tests/test_register_model.py
git commit -m "fleet: register_model.py — upsert artifact into model_registry"
```

## Task F5: Extend `validation/retrain.py` to emit `train_days` + `evaluation_windows` to metrics.json

**Files:** `validation/retrain.py`, `validation/run_training.py`.

Required so register_model has clean source of truth.

- [ ] **Step 1: Add to metrics.json output**

In `run_training.py` where `metrics.json` is written (around line 451), include:
```python
metrics = {
    ...,
    "symbol": args.symbol,
    "horizon_seconds": args.horizon,
    "train_days": args.train_days,
    "train_window_start": train_start.strftime("%Y-%m-%d"),
    "train_window_end": train_end.strftime("%Y-%m-%d"),
    "feature_version": args.feature_version,
}
```

- [ ] **Step 2: Verify by running one training**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cd /home/johnny/ofi-lab-v3 && PYTHONPATH=. .venv/bin/python validation/retrain.py --horizon 300 --symbol BTCUSDT --feature-version v3 --train-days 90 --train-end 2026-04-26 --feature-dir /data/features_v3 --output-dir /tmp/test_fleet_train'
cat /tmp/test_fleet_train/metrics.json | python -m json.tool
```

Verify expected keys present. Single 90-day h300 BTC training run = sanity gate.

- [ ] **Step 3: Commit**

## Task F6: `scripts/train_fleet.py` — batch driver

**Files:** Create `scripts/train_fleet.py`. Test: `tests/test_train_fleet.py`.

Drives 84 trainings, persists progress to a state file so it can resume after crash, parallelizes N at a time.

- [ ] **Step 1: Failing test**

```python
def test_train_fleet_dry_run_enumerates_84_models(tmp_path, monkeypatch):
    from scripts.train_fleet import enumerate_fleet
    fleet = enumerate_fleet(
        symbols=["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"],
        horizons=[60,180,300,600,900,1200,1800],
        train_days_list=[90,180,330],
    )
    assert len(fleet) == 4 * 7 * 3
    for cell in fleet:
        assert "symbol" in cell and "horizon" in cell and "train_days" in cell
        assert cell["name"].startswith("h")

def test_train_fleet_resume_skips_complete(tmp_path, monkeypatch):
    state = tmp_path / "fleet_state.json"
    state.write_text(json.dumps({
        "completed": ["h60_btc_v3_90d"],
        "failed": [],
        "in_progress": [],
    }))
    from scripts.train_fleet import filter_pending
    fleet = [{"name": "h60_btc_v3_90d"}, {"name": "h60_btc_v3_180d"}]
    pending = filter_pending(fleet, state)
    assert len(pending) == 1
    assert pending[0]["name"] == "h60_btc_v3_180d"
```

- [ ] **Step 2: Implement**

```python
# scripts/train_fleet.py
"""Drive fleet training: iterate (symbol, horizon, train_days), shell to retrain.py."""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

DEFAULT_SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT"]
DEFAULT_HORIZONS = [60,180,300,600,900,1200,1800]
DEFAULT_TRAIN_DAYS = [90,180,330]


def enumerate_fleet(symbols, horizons, train_days_list):
    cells = []
    for s in symbols:
        for h in horizons:
            for d in train_days_list:
                name = f"h{h}_{s.replace('USDT','').lower()}_v3_{d}d"
                cells.append({"symbol": s, "horizon": h,
                              "train_days": d, "name": name})
    return cells


def filter_pending(fleet, state_path: Path):
    if not state_path.exists():
        return fleet
    state = json.loads(state_path.read_text())
    done = set(state.get("completed", []))
    return [c for c in fleet if c["name"] not in done]


def train_one(cell, *, train_end, feature_dir, output_root, evaluation_windows):
    out_dir = Path(output_root) / cell["name"]
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "validation.retrain",
        "--horizon", str(cell["horizon"]),
        "--symbol", cell["symbol"],
        "--feature-version", "v3",
        "--train-days", str(cell["train_days"]),
        "--train-end", train_end,
        "--feature-dir", feature_dir,
        "--output-dir", str(out_dir),
    ]
    t0 = time.time()
    try:
        subprocess.run(cmd, check=True, timeout=3600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"name": cell["name"], "status": "FAILED",
                "err": str(e), "duration_s": time.time() - t0}

    # Register
    reg_cmd = [sys.executable, "scripts/register_model.py",
               "--artifact-dir", str(out_dir),
               "--evaluation-windows", ",".join(map(str, evaluation_windows))]
    try:
        subprocess.run(reg_cmd, check=True, timeout=60)
    except subprocess.CalledProcessError as e:
        return {"name": cell["name"], "status": "TRAINED_BUT_REGISTRATION_FAILED",
                "err": str(e), "duration_s": time.time() - t0}

    return {"name": cell["name"], "status": "DONE",
            "duration_s": time.time() - t0}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    p.add_argument("--horizons", default=",".join(map(str, DEFAULT_HORIZONS)))
    p.add_argument("--train-days", default=",".join(map(str, DEFAULT_TRAIN_DAYS)))
    p.add_argument("--train-end", required=True)
    p.add_argument("--feature-dir", default="/data/features_v3")
    p.add_argument("--output-root", default="/data/models/fleet")
    p.add_argument("--evaluation-windows", default="300,900,1800")
    p.add_argument("--state", default="/data/models/fleet/state.json")
    p.add_argument("--parallel", type=int, default=4)
    args = p.parse_args()

    symbols = args.symbols.split(",")
    horizons = [int(x) for x in args.horizons.split(",")]
    train_days_list = [int(x) for x in args.train_days.split(",")]
    eval_windows = [int(x) for x in args.evaluation_windows.split(",")]

    fleet = enumerate_fleet(symbols, horizons, train_days_list)
    state_path = Path(args.state)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    pending = filter_pending(fleet, state_path)
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "completed": [], "failed": []}

    print(f"fleet: {len(fleet)} cells, pending: {len(pending)}, parallel: {args.parallel}")

    with ProcessPoolExecutor(max_workers=args.parallel) as ex:
        futs = {ex.submit(train_one, c,
                          train_end=args.train_end,
                          feature_dir=args.feature_dir,
                          output_root=args.output_root,
                          evaluation_windows=eval_windows): c for c in pending}
        for fut in as_completed(futs):
            res = fut.result()
            print(f"  [{res['status']}] {res['name']} ({res['duration_s']:.1f}s)")
            if res["status"] == "DONE":
                state["completed"].append(res["name"])
            else:
                state["failed"].append({"name": res["name"], "err": res.get("err")})
            state_path.write_text(json.dumps(state, indent=2))

    print(f"done. completed={len(state['completed'])} failed={len(state['failed'])}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Test driver locally with one cell**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3 && source .venv/bin/activate && python -m pytest tests/test_train_fleet.py -v
```

- [ ] **Step 4: Commit**

## Task F7: `trading/fleet_loader.py` — registry-driven model loading

**Files:** Create `trading/fleet_loader.py`. Modify `trading/paper_trader.py` to use it when CLI flags absent.

- [ ] **Step 1: Failing test**

```python
def test_fleet_loader_returns_paper_active_models(db_conn):
    db_conn.executescript("""
    INSERT INTO model_registry (name, is_baseline, paper_active, live_eligible,
        lifecycle_state, symbol, training_horizon_seconds, artifact_path,
        feature_names_path, evaluation_windows)
    VALUES
      ('h300_btc', 1, 1, 0, 'active', 'BTCUSDT', 900, '/tmp/h300/model.lgb',
       '/tmp/h300/feature_names.json', '[300,900,1800]'),
      ('h60_xrp_v3_90d', 0, 1, 0, 'active', 'XRPUSDT', 60,
       '/tmp/h60/model.lgb', '/tmp/h60/feature_names.json', '[300,900,1800]'),
      ('h180_eth_v3_90d', 0, 0, 0, 'suspended', 'ETHUSDT', 180,
       '/tmp/dead/model.lgb', '/tmp/dead/feature_names.json', '[300,900,1800]');
    """)
    from trading.fleet_loader import load_active_fleet
    fleet = load_active_fleet(db_conn)
    names = sorted(c["name"] for c in fleet)
    assert names == ["h300_btc", "h60_xrp_v3_90d"]
    assert "h180_eth_v3_90d" not in names  # paper_active=0
    for c in fleet:
        assert "artifact_path" in c
        assert "evaluation_windows" in c
        assert isinstance(c["evaluation_windows"], list)
```

- [ ] **Step 2: Implement**

```python
# trading/fleet_loader.py
"""Load fleet from model_registry table."""
import json, sqlite3


def load_active_fleet(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT name, symbol, training_horizon_seconds, artifact_path,
               feature_names_path, evaluation_windows, generation, is_baseline,
               lifecycle_state
          FROM model_registry
         WHERE paper_active = 1 AND lifecycle_state != 'suspended'
         ORDER BY is_baseline DESC, training_horizon_seconds, name
    """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["evaluation_windows"] = json.loads(
            d["evaluation_windows"] or "[300,900,1800]")
        out.append(d)
    return out
```

- [ ] **Step 3: Wire into paper_trader.py**

In `main()` of `paper_trader.py`, after CLI parse:

```python
if not args.h60_model and not args.h300_model:
    # Registry-driven fleet mode
    from trading.fleet_loader import load_active_fleet
    conn = open_database(os.environ.get("STORAGE_DB_PATH", "/data/v3.db"))
    fleet = load_active_fleet(conn)
    model_paths = {c["name"]: c["artifact_path"] for c in fleet}
    if not model_paths:
        sys.exit("ERROR: no active models in registry. Run scripts/train_fleet.py first.")
    logger.info(f"Fleet mode: loading {len(model_paths)} models from registry")
    horizons_per_model = {c["name"]: c["training_horizon_seconds"] for c in fleet}
    eval_windows_per_model = {c["name"]: c["evaluation_windows"] for c in fleet}
else:
    # Legacy CLI mode (h60+h300 only)
    model_paths = {}
    if args.h60_model: model_paths["h60"] = args.h60_model
    if args.h300_model: model_paths["h300"] = args.h300_model
    horizons_per_model = {"h60": 60, "h300": 300}
    eval_windows_per_model = {n: [300, 900] for n in model_paths}
```

PaperTrader constructor should accept `eval_windows_per_model` dict and pass to `window_planner.plan_resolution_rows()` per model.

- [ ] **Step 4: Window planner integration**

In the boundary loop, when emitting prediction rows, derive evaluation windows from `eval_windows_per_model[model_name]` rather than hardcoded `CONTRACT_DURATIONS`.

- [ ] **Step 5: Tests + commit**

---

# Phase 2 — Execute fleet training

## Task F8: Data catchup on VPS

- [ ] **Step 1: Run download_klines for catchup window**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cd /home/johnny/ofi-lab-v3 && PYTHONPATH=. .venv/bin/python -m data.download_klines --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT --start 2025-05-01 --through 2026-05-10'
```
Should be no-op for existing complete day files. Estimated time: <10 min for ~370 days × 4 symbols.

- [ ] **Step 2: Rebuild features for new days**

Run the canonical feature build script (name TBD from F2 step 2). Cover all symbols for whatever days are newer than current features.

- [ ] **Step 3: Verify completeness gate**

`scripts/preflight_v3.py` already exists. Extend or write `scripts/check_fleet_data.py` that asserts ≥330 days continuous for each symbol up to `2026-04-26`.

## Task F9: Sanity train — one cell

Before launching all 84, train one cell end-to-end to validate the pipeline.

- [ ] **Step 1: Pick simplest cell**

`h300_btc_v3_90d` — smallest train window, known-good symbol/horizon.

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cd /home/johnny/ofi-lab-v3 && PYTHONPATH=. .venv/bin/python scripts/train_fleet.py --symbols BTCUSDT --horizons 300 --train-days 90 --train-end 2026-04-26 --parallel 1 --state /tmp/sanity_state.json --output-root /tmp/sanity_fleet'
```

- [ ] **Step 2: Verify artifact + registry**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'ls /tmp/sanity_fleet/h300_btc_v3_90d/; sqlite3 /data/v3.db "SELECT name, train_days, evaluation_windows FROM model_registry WHERE name=\"h300_btc_v3_90d\""'
```

Expected: model.lgb + metrics.json + feature_names.json present. Registry row exists with `train_days=90`, `evaluation_windows='[300,900,1800]'`.

- [ ] **Step 3: Compare metrics to known baseline**

Existing h300 BTC baseline metrics (from /data/models/latest_h300/metrics.json on VPS): AUC, brier. New 90-day model should be in same ballpark (±5%). If wildly off, debug pipeline before scaling.

## Task F10: Full fleet train

- [ ] **Step 1: Launch**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cd /home/johnny/ofi-lab-v3 && nohup PYTHONPATH=. .venv/bin/python scripts/train_fleet.py --train-end 2026-04-26 --parallel 4 --state /data/models/fleet/state.json --output-root /data/models/fleet > /var/log/v3/fleet_train.log 2>&1 &'
```

- [ ] **Step 2: Monitor**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'tail -f /var/log/v3/fleet_train.log'
```

Walk away. Estimated 1.5–2 hours at parallel=4. Resume on crash via `state.json`.

- [ ] **Step 3: Verify fleet registered**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'sqlite3 /data/v3.db "SELECT COUNT(*), SUM(is_baseline), SUM(paper_active) FROM model_registry"'
```

Expected: 85 rows (84 fleet + 1 baseline), 1 baseline, 85 paper_active. Failed cells visible in `state.json` `failed` list.

- [ ] **Step 4: Audit failed cells**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'cat /data/models/fleet/state.json | python3 -m json.tool | grep -A1 failed | head -40'
```

Retrain failed cells individually if root cause fixable (data gap, OOM, etc.).

## Task F11: Restart paper trader in fleet mode

- [ ] **Step 1: Verify paper_trader picks up fleet**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'sudo systemctl restart v3-paper-trader && sleep 8 && sudo journalctl -u v3-paper-trader --since "30 sec ago" --no-pager | grep -E "Fleet mode|Loaded model"'
```

Expected log: `Fleet mode: loading 85 models from registry`. Followed by 85 `Loaded model {name} from {path}` lines.

- [ ] **Step 2: Confirm /api/models/list returns full fleet**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'curl -sS -u admin:hNHsbNoiYIE7TEdF8lGE9x9CdoTnVNUH http://127.0.0.1:8081/api/models/list | python3 -c "import sys, json; m=json.load(sys.stdin)[\"models\"]; print(len(m), \"models\"); print(set(x[\"symbol\"] for x in m)); print(sorted(set(x[\"horizon\"] for x in m)))"'
```

Expected: `85 models {'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT'} [60, 180, 300, 600, 900, 1200, 1800]`.

- [ ] **Step 3: Watch first boundary**

Wait for next 900s boundary (max 15 min). Paper trader should emit 85 native prediction rows + 85 × (eval windows) evaluation rows.

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'sqlite3 /data/v3.db "SELECT model_name, COUNT(*) FROM predictions WHERE ts_model_ran_ms > strftime(\"%s\",\"now\",\"-15 min\")*1000 GROUP BY model_name"'
```

Expected: ~85 model names, each with 1+ row.

---

# Phase 3 — Observation + analysis

## Task F12: Wait 7 days for resolution data

Native predictions resolve at `boundary + horizon`. Need enough resolved rows per (model, eval_window) cell to compute decay metrics.

- [ ] **Step 1: Daily checks**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'curl -sS -u admin:hNHsbNoiYIE7TEdF8lGE9x9CdoTnVNUH http://127.0.0.1:8081/api/models/list | python3 -c "import sys,json; m=json.load(sys.stdin)[\"models\"]; print(\"\\n\".join(f\"{x[\"name\"]:30} ev={x[\"ewma_ev\"]} brier={x[\"ewma_brier\"]}\" for x in m if x[\"ewma_ev\"] is not None))"'
```

Watch ewma_ev populate per model. Lifecycle FSM auto-suspends models with EV < 0.02 (non-baselines).

## Task F13: Optimal (symbol, trading-window) lookup query

After ≥1000 resolved evaluations per cell, run analysis to identify best model per `(symbol, trading_window)`:

```sql
SELECT 
    p.symbol,
    p.market_window_seconds AS trading_window,
    p.model_name AS best_model,
    AVG(t.net_pnl) AS avg_ev_usd,
    COUNT(*) AS n
  FROM paper_trades t
  JOIN predictions p ON p.prediction_id = t.prediction_id
 WHERE p.resolution_type = 'evaluation'
   AND p.resolved = 1
   AND p.ts_resolve_at_ms > strftime('%s', 'now', '-30 days') * 1000
 GROUP BY p.symbol, p.market_window_seconds, p.model_name
HAVING COUNT(*) >= 100
 ORDER BY p.symbol, p.market_window_seconds, avg_ev_usd DESC;
```

Top row per `(symbol, trading_window)` group = optimal model. Lifecycle FSM doesn't auto-promote — operator promotes manually via dashboard `/api/models/{name}/enable_live` (still gated by eligibility checks).

## Task F14: Tag + memo

- [ ] **Step 1: Tag**

```bash
git tag v3-fleet-trained
git push origin v3-fleet-trained
```

- [ ] **Step 2: Update MEMORY.md**

Append:
```
- 2026-05-{DD}: v3 fleet of 85 models trained — 4 symbols × 7 horizons × 3 train-day windows. Optimal (symbol, trading-window) discovered after 30-day observation. h300_btc baseline still untouched.
```

---

## Self-Review

**Spec coverage:**
- Data catchup before training → F1, F2, F8 ✓
- Retrain old models for all 4 symbols → F10 ✓
- New horizons h180, h600, h900, h1200, h1800 (+ existing h60, h300) → 7 horizons in F6 ✓
- Train on 90d, 180d, 330d → F6 ✓
- All models predict simultaneously → F7, F11 ✓
- Don't change architecture, only horizon + train_days → F6 retain V3_FEATURE_COLS, retrain.py unchanged ✓
- All models trade at all trading windows (300/900/1800) → `evaluation_windows = [300, 900, 1800]` in F3, F7 ✓
- Find optimal per (symbol, trading_window) → F13 ✓
- Baseline (h300_btc) untouched → F4 step 2 (existing baseline protection in register_model) ✓

**Risks:**
- Feature data may not exist past 2026-03-04. Catches in F2 — pipeline may need feature rebuild from raw L2, may not be available. Fallback: cap `--train-end 2026-03-04` and retain old models as-is.
- Parallel training may OOM on small VPS. Mitigation: `--parallel 2` default if memory tight.
- 85 models × full features per boundary = compute pressure on paper trader. Mitigation: monitor CPU after F11; if pegged, reduce evaluation_windows to [900] for non-baseline models.
- Lifecycle FSM auto-suspends weak models at EV<0.02. Will trim fleet naturally. Watch for over-aggressive suspension; may need to widen hysteresis if 50%+ models suspend in first 24h.

**Placeholder scan:** None — all task steps have concrete commands. Feature rebuild script name flagged as "TBD from F2 step 2" because haiku investigation didn't surface it — that's the intentional in-plan discovery step, not a placeholder.

**Open question for user:**
1. Is `train_end = 2026-04-26` acceptable? (Reserves 14 days for live observation as test set.)
2. Should evaluation_windows be `[300, 900, 1800]` for all models or per-model? Plan assumes all-models-all-windows.
3. OK to leave Kalshi live disabled during fleet train + observation (no real-money trading until optimal models identified)?

---

## Deferred: bring back walk-forward CV

WF disabled via `--skip-wf` in fleet driver (commit 972c666) to speed up training from ~30h to ~3-4h. WF is sanity-check only — final model trains on full train+val regardless. After fleet runs in paper-trading for some time and we identify optimal `(symbol, trading_window)` cells, re-enable WF for the keeper models to validate their stability across folds.

Re-enable for a single model:
```bash
ssh ... 'cd /home/johnny/ofi-lab-v3 && PYTHONPATH=. .venv/bin/python validation/retrain.py --horizon 300 --symbol BTCUSDT --feature-version v3 --train-days 330 --train-end 2026-04-26 --val-days 10 --test-days 5 --feature-dir /data/features_v3 --output-dir /data/models/fleet_wf/h300_btc_v3_330d'
```
(omit `--skip-wf`)

