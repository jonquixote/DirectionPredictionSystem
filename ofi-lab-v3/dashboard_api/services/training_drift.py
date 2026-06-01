"""Training-data drift snapshots — Tier 3 of the model-lineage analytics spec.

Powers GET /api/analysis/cell-drift. Each model has a fingerprint of its
training feature distribution (percentiles + mean + std per feature). Drift
between two successive-fleet models is KS distance approximated from the
percentile fingerprints (9 grid points, linear interpolation).

Feature parquets live at /data/features_v3/{SYMBOL}/{DATE}_{SYMBOL}_features.parquet.
Training metrics are read from {artifact_dir}/metrics.json (written by retrain.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time as _time
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Percentile names stored in feature_dist_json (must be stable across schema versions)
_PCT_KEYS = ("p01", "p05", "p25", "p50", "p75", "p95", "p99")
_PCT_VALS = (0.01, 0.05, 0.25, 0.50, 0.75, 0.95, 0.99)

# Exclude these from feature fingerprint
_EXCLUDE_PREFIXES = ("_target_", "_label_")
_EXCLUDE_EXACT = {"symbol", "timestamp_ms", "ts_ms"}


def _get_db():
    """Same import dance as services/lineage.py for path compatibility."""
    try:
        from services.db import get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore[assignment]
    return get_db()


def _get_write_db():
    """Separate connection for snapshot inserts with a generous busy timeout.

    The main dashboard connection uses WAL mode and holds read snapshots
    for extended periods. Using a dedicated write connection with a 15 s
    busy_timeout prevents 'database is locked' errors during backfill and
    on-demand compute.
    """
    import os
    import sqlite3
    from pathlib import Path
    db_path = os.environ.get(
        "STORAGE_DB_PATH",
        str(Path(__file__).resolve().parents[2] / "data" / "v3.db"),
    )
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


# ---------------------------------------------------------------------------
# Parquet discovery
# ---------------------------------------------------------------------------

def _feature_columns(df) -> list[str]:
    """Return the feature columns from a DataFrame (exclude metadata cols)."""
    cols = []
    for c in df.columns:
        if c in _EXCLUDE_EXACT:
            continue
        if any(c.startswith(p) for p in _EXCLUDE_PREFIXES):
            continue
        cols.append(c)
    return cols


def _date_range(start: str, end: str):
    """Iterate dates [start, end] inclusive as YYYY-MM-DD strings."""
    d0 = date.fromisoformat(start[:10])
    d1 = date.fromisoformat(end[:10])
    cur = d0
    while cur <= d1:
        yield cur.strftime("%Y-%m-%d")
        cur += timedelta(days=1)


def _parquet_path(symbol: str, day: str) -> str:
    return f"/data/features_v3/{symbol}/{day}_{symbol}_features.parquet"


# ---------------------------------------------------------------------------
# Public API — compute_feature_fingerprint
# ---------------------------------------------------------------------------

def compute_feature_fingerprint(
    symbol: str,
    train_window_start: str,
    train_window_end: str,
) -> dict:
    """Read feature parquets for [train_window_start, train_window_end] and
    compute per-feature percentile fingerprint + mean + std + n_nulls.

    Streams files one-by-one to avoid loading all data at once. Each parquet
    is ~1 day × ~1440 rows. Aggregates running Welford-style stats so the
    peak memory is O(one file) rather than O(all files).

    Returns the feature_dist_json structure documented in schema.sql.
    """
    try:
        import pandas as pd
        import numpy as np
    except ImportError as e:
        raise RuntimeError("pandas/numpy required for feature fingerprint") from e

    feature_cols: list[str] | None = None
    # Per-feature accumulators: list of column arrays, flushed after each file
    # Use reservoir-style: keep a sample of up to _MAX_SAMPLE rows per feature.
    _MAX_SAMPLE = 500_000
    # We'll accumulate all values as numpy arrays then compute percentiles at end.
    # Memory-cautious: each file contributes at most 1440 rows × 34 float32 cols ≈ 200 KB.
    # With 400 files that's ~80 MB — acceptable.
    accumulated: dict[str, list] = {}
    total_rows = 0

    dates = list(_date_range(train_window_start, train_window_end))
    loaded = 0
    for day in dates:
        fpath = _parquet_path(symbol, day)
        if not os.path.exists(fpath):
            continue
        try:
            df = pd.read_parquet(fpath)
        except Exception as exc:
            logger.warning("Skipping %s: %s", fpath, exc)
            continue

        if feature_cols is None:
            feature_cols = _feature_columns(df)

        total_rows += len(df)
        loaded += 1

        for col in feature_cols:
            if col not in df.columns:
                continue
            vals = df[col].values
            # Replace inf with NaN before storing
            try:
                import numpy as _np
                vals = _np.where(_np.isinf(vals.astype(float)), _np.nan, vals.astype(float))
            except (TypeError, ValueError):
                continue
            accumulated.setdefault(col, []).append(vals)

    if not loaded or feature_cols is None:
        raise ValueError(
            f"No parquet files found for {symbol} in [{train_window_start}, {train_window_end}]"
        )

    import numpy as np

    features_out: dict[str, dict] = {}
    for col in feature_cols:
        if col not in accumulated or not accumulated[col]:
            continue
        arr = np.concatenate(accumulated[col])
        n_nulls = int(np.sum(np.isnan(arr)))
        valid = arr[~np.isnan(arr)]
        if len(valid) == 0:
            features_out[col] = {
                k: None for k in (*_PCT_KEYS, "mean", "std", "n_nulls")
            }
            features_out[col]["n_nulls"] = n_nulls
            continue
        pcts = np.percentile(valid, [p * 100 for p in _PCT_VALS]).tolist()
        features_out[col] = {
            k: round(float(v), 8) for k, v in zip(_PCT_KEYS, pcts)
        }
        features_out[col]["mean"] = round(float(np.mean(valid)), 8)
        features_out[col]["std"] = round(float(np.std(valid)), 8)
        features_out[col]["n_nulls"] = n_nulls

    dist = {
        "symbol": symbol,
        "n_train_obs": total_rows,
        "features": features_out,
    }

    # Stable hash of feature names
    feat_hash = hashlib.sha256(
        json.dumps(sorted(features_out.keys())).encode()
    ).hexdigest()[:16]

    return {"feature_dist": dist, "feature_names_hash": feat_hash}


# ---------------------------------------------------------------------------
# Public API — compute_drift
# ---------------------------------------------------------------------------

def compute_drift(snapshot_a: dict, snapshot_b: dict) -> dict:
    """Per-feature KS approximation using percentile fingerprints.

    snapshot_a / snapshot_b are the feature_dist_json dicts (already parsed).
    Returns per-feature KS stats plus max_ks headline.

    KS = max(|F_a(x) - F_b(x)|) evaluated at the union of the 9 percentile
    grid points for each feature, with linear interpolation for CDF values.
    """
    feats_a = snapshot_a.get("features", {})
    feats_b = snapshot_b.get("features", {})
    common = set(feats_a.keys()) & set(feats_b.keys())

    per_feature: dict[str, dict] = {}
    for feat in common:
        fa = feats_a[feat]
        fb = feats_b[feat]
        pa = [fa.get(k) for k in _PCT_KEYS]
        pb = [fb.get(k) for k in _PCT_KEYS]
        if any(v is None for v in pa) or any(v is None for v in pb):
            continue

        # Build CDF approximation at union of grid points
        all_xs = sorted(set(pa) | set(pb))
        # Interpolate F_a and F_b at each x
        def _interp_cdf(xs_grid, ps_grid, xs_eval):
            """Linear interpolation of CDF given (value, cumprob) pairs."""
            import numpy as np
            return np.interp(xs_eval, xs_grid, ps_grid, left=0.0, right=1.0)

        import numpy as np
        cdf_a = _interp_cdf(pa, list(_PCT_VALS), all_xs)
        cdf_b = _interp_cdf(pb, list(_PCT_VALS), all_xs)
        ks = float(np.max(np.abs(cdf_a - cdf_b)))

        per_feature[feat] = {
            "ks": round(ks, 6),
            "p_predecessor": pa,
            "p_successor": pb,
        }

    if not per_feature:
        return {
            "per_feature": {},
            "max_ks": None,
            "max_ks_feature": None,
            "mean_ks": None,
            "n_features_compared": 0,
        }

    ks_vals = {f: d["ks"] for f, d in per_feature.items()}
    max_feat = max(ks_vals, key=lambda x: ks_vals[x])
    all_ks = list(ks_vals.values())

    return {
        "per_feature": per_feature,
        "max_ks": round(ks_vals[max_feat], 6),
        "max_ks_feature": max_feat,
        "mean_ks": round(sum(all_ks) / len(all_ks), 6),
        "n_features_compared": len(per_feature),
    }


# ---------------------------------------------------------------------------
# Public API — get_or_compute_snapshot
# ---------------------------------------------------------------------------

def get_or_compute_snapshot(model_name: str) -> dict | None:
    """Look up training_data_snapshots for model_name. If missing and the
    model has artifact_path on disk, attempt to compute + insert.

    Returns the row dict (with parsed feature_dist_json) or None.
    """
    db = _get_db()
    row = db.execute(
        "SELECT * FROM training_data_snapshots WHERE model_name = ?",
        (model_name,),
    ).fetchone()

    if row is not None:
        d = dict(row)
        if d.get("feature_dist_json"):
            try:
                d["feature_dist_json"] = json.loads(d["feature_dist_json"])
            except Exception:
                pass
        return d

    # Not cached — try to compute
    reg = db.execute(
        """
        SELECT symbol, train_window_start, train_window_end, artifact_path
        FROM model_registry WHERE name = ?
        """,
        (model_name,),
    ).fetchone()
    if reg is None:
        logger.warning("get_or_compute_snapshot: model %s not in registry", model_name)
        return None
    if not reg["train_window_start"] or not reg["train_window_end"]:
        return None

    try:
        fp = compute_feature_fingerprint(
            symbol=reg["symbol"],
            train_window_start=reg["train_window_start"],
            train_window_end=reg["train_window_end"],
        )
    except Exception as exc:
        logger.warning("compute_feature_fingerprint failed for %s: %s", model_name, exc)
        return None

    # Try to read metrics.json
    brier = log_loss = auc = n_obs = None
    artifact_path = reg["artifact_path"]
    if artifact_path:
        artifact_dir = os.path.dirname(artifact_path)
        metrics_path = os.path.join(artifact_dir, "metrics.json")
        if os.path.exists(metrics_path):
            try:
                with open(metrics_path) as f:
                    m = json.load(f)
                brier = m.get("brier_score")
                log_loss = m.get("log_loss")
                auc = m.get("auc_at_contract_times") or m.get("auc_full")
                n_obs = m.get("train_size")
            except Exception as exc:
                logger.warning("metrics.json read failed for %s: %s", model_name, exc)

    dist_json = json.dumps(fp["feature_dist"])
    computed_at = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())

    # Use a dedicated write connection with busy_timeout to avoid "database is
    # locked" when the dashboard's long-lived WAL reader is active.
    wdb = _get_write_db()
    try:
        wdb.execute(
            """
            INSERT OR REPLACE INTO training_data_snapshots
              (model_name, feature_names_hash, feature_dist_json,
               training_brier, training_log_loss, training_auc, n_train_obs, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_name,
                fp["feature_names_hash"],
                dist_json,
                brier,
                log_loss,
                auc,
                fp["feature_dist"].get("n_train_obs") or n_obs,
                computed_at,
            ),
        )
        wdb.commit()
    except Exception as exc:
        logger.warning("DB insert failed for %s: %s", model_name, exc)
    finally:
        try:
            wdb.close()
        except Exception:
            pass

    result = {
        "model_name": model_name,
        "feature_names_hash": fp["feature_names_hash"],
        "feature_dist_json": fp["feature_dist"],
        "training_brier": brier,
        "training_log_loss": log_loss,
        "training_auc": auc,
        "n_train_obs": fp["feature_dist"].get("n_train_obs") or n_obs,
        "computed_at": computed_at,
    }
    return result


# ---------------------------------------------------------------------------
# Public API — compute_cell_drift_chain
# ---------------------------------------------------------------------------

def compute_cell_drift_chain(cell_key: str) -> dict:
    """Walk the cell's fleet chain (model_registry rows sorted by fleet_version),
    compute pairwise drift between each successive (predecessor, successor) pair.

    Returns:
      {
        "cell_key": "...",
        "fleet_chain": [
          {
            "predecessor_model": "...",
            "successor_model": "...",
            "predecessor_fleet": "2026-04-27",
            "successor_fleet": "2026-05-06",
            "max_ks": 0.18,
            "max_ks_feature": "vwap_dev_30s_std",
            "n_features_compared": 32,
            "mean_ks": 0.07,
          },
          ...
        ],
        "metadata": {...}
      }
    """
    # Parse cell_key
    parts = cell_key.split("_")
    if len(parts) != 3:
        raise ValueError(f"cell_key must be SYMBOL_HORIZON_TRAININGDAYS, got {cell_key!r}")
    symbol, horizon_s, train_days_s = parts
    try:
        horizon = int(horizon_s)
        train_days = int(train_days_s)
    except (TypeError, ValueError) as e:
        raise ValueError(f"cell_key parse failed: {cell_key!r}: {e}") from e

    db = _get_db()
    reg_rows = db.execute(
        """
        SELECT name, fleet_version, train_window_start, train_window_end
        FROM model_registry
        WHERE symbol = ? AND training_horizon_seconds = ? AND train_days = ?
        ORDER BY fleet_version ASC, created_at ASC
        """,
        (symbol, horizon, train_days),
    ).fetchall()

    if not reg_rows:
        return {
            "cell_key": cell_key,
            "fleet_chain": [],
            "metadata": {
                "computed_at_ms": int(_time.time() * 1000),
                "symbol": symbol,
                "horizon": horizon,
                "train_days": train_days,
                "n_models": 0,
            },
        }

    # Deduplicate: one model per fleet_version (take the first registered)
    seen_fleets: dict[str, dict] = {}
    for r in reg_rows:
        fv = r["fleet_version"]
        if fv not in seen_fleets:
            seen_fleets[fv] = dict(r)
    fleet_chain = sorted(seen_fleets.values(), key=lambda x: x["fleet_version"])

    drift_pairs: list[dict] = []
    for i in range(len(fleet_chain) - 1):
        pred_info = fleet_chain[i]
        succ_info = fleet_chain[i + 1]
        pred_name = pred_info["name"]
        succ_name = succ_info["name"]

        snap_pred = get_or_compute_snapshot(pred_name)
        snap_succ = get_or_compute_snapshot(succ_name)

        if snap_pred is None or snap_succ is None:
            drift_pairs.append({
                "predecessor_model": pred_name,
                "successor_model": succ_name,
                "predecessor_fleet": pred_info["fleet_version"],
                "successor_fleet": succ_info["fleet_version"],
                "max_ks": None,
                "max_ks_feature": None,
                "n_features_compared": 0,
                "mean_ks": None,
                "error": "snapshot unavailable",
            })
            continue

        pred_dist = snap_pred.get("feature_dist_json")
        succ_dist = snap_succ.get("feature_dist_json")
        if isinstance(pred_dist, str):
            try:
                pred_dist = json.loads(pred_dist)
            except Exception:
                pred_dist = None
        if isinstance(succ_dist, str):
            try:
                succ_dist = json.loads(succ_dist)
            except Exception:
                succ_dist = None

        if pred_dist is None or succ_dist is None:
            drift_pairs.append({
                "predecessor_model": pred_name,
                "successor_model": succ_name,
                "predecessor_fleet": pred_info["fleet_version"],
                "successor_fleet": succ_info["fleet_version"],
                "max_ks": None,
                "max_ks_feature": None,
                "n_features_compared": 0,
                "mean_ks": None,
                "error": "feature_dist_json missing",
            })
            continue

        try:
            drift = compute_drift(pred_dist, succ_dist)
        except Exception as exc:
            logger.warning("compute_drift failed for %s->%s: %s", pred_name, succ_name, exc)
            drift_pairs.append({
                "predecessor_model": pred_name,
                "successor_model": succ_name,
                "predecessor_fleet": pred_info["fleet_version"],
                "successor_fleet": succ_info["fleet_version"],
                "max_ks": None,
                "max_ks_feature": None,
                "n_features_compared": 0,
                "mean_ks": None,
                "error": str(exc),
            })
            continue

        drift_pairs.append({
            "predecessor_model": pred_name,
            "successor_model": succ_name,
            "predecessor_fleet": pred_info["fleet_version"],
            "successor_fleet": succ_info["fleet_version"],
            "max_ks": drift["max_ks"],
            "max_ks_feature": drift["max_ks_feature"],
            "n_features_compared": drift["n_features_compared"],
            "mean_ks": drift["mean_ks"],
        })

    return {
        "cell_key": cell_key,
        "fleet_chain": drift_pairs,
        "metadata": {
            "computed_at_ms": int(_time.time() * 1000),
            "symbol": symbol,
            "horizon": horizon,
            "train_days": train_days,
            "n_models": len(fleet_chain),
            "n_pairs": len(drift_pairs),
        },
    }
