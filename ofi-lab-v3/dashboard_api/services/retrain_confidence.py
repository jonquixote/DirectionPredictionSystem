"""Tier 4 lineage: predictive retrain confidence.

Fits a simple OLS regression that maps predecessor metrics + cell drift +
training noise into a predicted post-retrain metric band.  After each
retrain the band is written to retrain_confidence_predictions so ModelDetail
can surface a "predicted range" chip.  When the successor_lookback window
elapses, realize_pending_predictions() back-fills the actual value and the
predictor self-calibrates on the growing realized set.

Spec: docs/2026-05-31-model-lineage-analytics-spec.md  Tier 4.
"""
from __future__ import annotations

import json
import logging
import math
import time as _time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Metric columns / logic ---------------------------------------------------
_VALID_METRICS = ("composite", "win_rate", "roi")
_DEFAULT_PREDECESSOR_LOOKBACK = 14  # days
_DEFAULT_SUCCESSOR_LOOKBACK = 7     # days


def _get_db():
    try:
        from services.db import get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore[assignment]
    conn = get_db()
    # Allow up to 10s of WAL contention before raising OperationalError.
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _now_ms() -> int:
    return int(_time.time() * 1000)


# ---------------------------------------------------------------------------
# Helpers: read metric value for a model over a date window
# ---------------------------------------------------------------------------

def _metric_value(
    db,
    model_name: str,
    symbol: str,
    since_date: str,
    metric: str,
) -> float | None:
    """Aggregate the requested metric for model_name over all market windows
    since since_date.  Returns None if no data available.

    composite → average composite_score from model_tier_score
    win_rate  → SUM(n_correct)/SUM(n) from predictions_daily_rollup
    roi       → SUM(sum_pnl)/SUM(n_resolved_trades) * 100
    """
    if metric == "composite":
        row = db.execute(
            """
            SELECT AVG(composite_score) AS v
            FROM model_tier_score
            WHERE model_name = ?
              AND ts >= datetime(?, 'start of day')
            """,
            (model_name, since_date),
        ).fetchone()
        v = row["v"] if row else None
        return float(v) if v is not None else None

    if metric == "win_rate":
        row = db.execute(
            """
            SELECT SUM(n) AS sn, SUM(n_correct) AS sc
            FROM predictions_daily_rollup
            WHERE model_name = ? AND date_utc >= ?
            """,
            (model_name, since_date),
        ).fetchone()
        if row and row["sn"]:
            return float(row["sc"] or 0) / float(row["sn"])
        return None

    if metric == "roi":
        row = db.execute(
            """
            SELECT SUM(sum_pnl) AS sp, SUM(n_resolved_trades) AS sr
            FROM predictions_daily_rollup
            WHERE model_name = ? AND date_utc >= ?
            """,
            (model_name, since_date),
        ).fetchone()
        if row and row["sr"]:
            return float(row["sp"] or 0) / float(row["sr"]) * 100.0
        return None

    return None


def _days_since_date(days: int) -> str:
    """Return YYYY-MM-DD string for (today - days)."""
    t = _time.time() - days * 86400.0
    return _time.strftime("%Y-%m-%d", _time.gmtime(t))


def _date_after(iso_date: str, days: int) -> str:
    """Return YYYY-MM-DD string for iso_date + days."""
    try:
        t = _time.mktime(_time.strptime(iso_date, "%Y-%m-%d")) + days * 86400.0
    except Exception:
        return iso_date
    return _time.strftime("%Y-%m-%d", _time.gmtime(t))


# ---------------------------------------------------------------------------
# Feature: cell drift (x2) via training_drift if available
# ---------------------------------------------------------------------------

def _get_cell_drift(predecessor_name: str, successor_name: str) -> float:
    """Return max KS drift between predecessor and successor training snapshots.

    Gracefully degrades to 0.0 if T3 module is absent or snapshots missing.
    """
    try:
        try:
            from services.training_drift import compute_cell_drift_chain  # type: ignore
        except ModuleNotFoundError:
            from dashboard_api.services.training_drift import compute_cell_drift_chain  # type: ignore[assignment]
        result = compute_cell_drift_chain([predecessor_name, successor_name])
        if result and isinstance(result, dict):
            return float(result.get("max_ks", 0.0) or 0.0)
        if result and isinstance(result, (int, float)):
            return float(result)
    except Exception as e:
        logger.debug("training_drift unavailable for (%s, %s): %s", predecessor_name, successor_name, e)
    return 0.0


# ---------------------------------------------------------------------------
# Dataset assembly
# ---------------------------------------------------------------------------

def _build_regression_dataset(
    db,
    metric: str,
    predecessor_lookback_days: int,
    successor_lookback_days: int,
) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """Build (X, y) arrays from historical succession pairs.

    Scans model_registry for (predecessor, successor) pairs in the same cell
    (same symbol + training_horizon_seconds + train_days), ordered by
    train_window_end.  For each pair:

      x1 = predecessor metric over its first predecessor_lookback_days
      x2 = cell drift (max KS, 0 if missing)
      x3 = per-cell historical residual sd from the most recent prior fit
           (0 if no prior fit)
      x4 = training_brier_delta = successor_training_brier - predecessor_training_brier
           (0 if either missing)
      x5 = log(n_train_obs) of the successor (0 if missing)
      y  = successor metric over its first successor_lookback_days

    Returns (X [n×6 with intercept], y [n], meta list).
    """
    # All models, grouped by cell, ordered by train_window_end
    rows = db.execute(
        """
        SELECT mr.name, mr.symbol, mr.training_horizon_seconds, mr.train_days,
               mr.train_window_end,
               tds.training_brier, tds.n_train_obs
        FROM model_registry mr
        LEFT JOIN training_data_snapshots tds ON tds.model_name = mr.name
        WHERE mr.symbol IS NOT NULL
          AND mr.training_horizon_seconds IS NOT NULL
          AND mr.train_days IS NOT NULL
          AND COALESCE(mr.is_baseline, 0) = 0
        ORDER BY mr.symbol, mr.training_horizon_seconds, mr.train_days,
                 COALESCE(mr.train_window_end, mr.created_at) ASC
        """
    ).fetchall()

    # Group by cell
    from collections import defaultdict
    cells: dict[str, list] = defaultdict(list)
    for r in rows:
        ck = f"{r['symbol']}_{r['training_horizon_seconds']}_{r['train_days']}"
        cells[ck].append(r)

    # Per-cell residual sd from most recent prior fit (x3 proxy)
    # We simply default to 0; will fill from prior model fits after first run.
    prior_residual_sd: dict[str, float] = {}
    try:
        prior_rows = db.execute(
            """
            SELECT feature_importance_json
            FROM retrain_confidence_models
            WHERE metric = ?
            ORDER BY fitted_at_ms DESC
            LIMIT 1
            """,
            (metric,),
        ).fetchone()
        if prior_rows and prior_rows["feature_importance_json"]:
            fi = json.loads(prior_rows["feature_importance_json"])
            prior_residual_sd = fi.get("cell_residual_sd", {})
    except Exception:
        pass

    X_rows: list[list[float]] = []
    y_vals: list[float] = []
    meta_rows: list[dict] = []

    for ck, cell_models in cells.items():
        if len(cell_models) < 2:
            continue
        symbol = cell_models[0]["symbol"]
        for i in range(len(cell_models) - 1):
            pred_row = cell_models[i]
            succ_row = cell_models[i + 1]
            pred_name = pred_row["name"]
            succ_name = succ_row["name"]

            # Determine date range for each window
            pred_train_end = (pred_row["train_window_end"] or "")[:10]
            succ_train_end = (succ_row["train_window_end"] or "")[:10]

            if not pred_train_end or not succ_train_end:
                continue  # no date anchor → can't define the windows

            pred_since = _date_after(pred_train_end, -predecessor_lookback_days) if pred_train_end else None
            succ_since = succ_train_end  # successor window starts at its own train_end

            if pred_since is None:
                continue

            # x1: predecessor metric
            x1 = _metric_value(db, pred_name, symbol, pred_since, metric)
            if x1 is None:
                continue  # need this or pair is useless

            # y: successor metric
            succ_since_plus = _date_after(succ_train_end, successor_lookback_days)
            y = _metric_value(db, succ_name, symbol, succ_since, metric)
            if y is None:
                continue  # no target

            # x2: cell drift
            x2 = _get_cell_drift(pred_name, succ_name)
            x2_missing = 1 if x2 == 0.0 else 0  # flag (unused in linear fit, stored in meta)

            # x3: per-cell historical residual sd
            x3 = float(prior_residual_sd.get(ck, 0.0))

            # x4: training_brier_delta
            pb = pred_row["training_brier"]
            sb = succ_row["training_brier"]
            x4 = float(sb - pb) if (pb is not None and sb is not None) else 0.0
            x4_missing = 1 if (pb is None or sb is None) else 0

            # x5: log(n_train_obs) of successor
            nobs = succ_row["n_train_obs"]
            x5 = math.log(float(nobs)) if (nobs and nobs > 0) else 0.0
            x5_missing = 1 if not nobs else 0

            X_rows.append([1.0, x1, x2, x3, x4, x5])  # intercept + 5 features
            y_vals.append(y)
            meta_rows.append({
                "cell_key": ck,
                "predecessor": pred_name,
                "successor": succ_name,
                "x2_missing": x2_missing,
                "x4_missing": x4_missing,
                "x5_missing": x5_missing,
            })

    if not X_rows:
        return np.zeros((0, 6)), np.zeros(0), []

    return np.array(X_rows, dtype=float), np.array(y_vals, dtype=float), meta_rows


# ---------------------------------------------------------------------------
# fit_predictor
# ---------------------------------------------------------------------------

def fit_predictor(
    metric: str = "composite",
    predecessor_lookback_days: int = _DEFAULT_PREDECESSOR_LOOKBACK,
    successor_lookback_days: int = _DEFAULT_SUCCESSOR_LOOKBACK,
) -> dict:
    """Build the regression dataset and fit OLS.  Inserts one row into
    retrain_confidence_models.  Returns the inserted-row dict.
    """
    if metric not in _VALID_METRICS:
        raise ValueError(f"metric must be one of {_VALID_METRICS}, got {metric!r}")

    db = _get_db()
    X, y, meta = _build_regression_dataset(
        db, metric, predecessor_lookback_days, successor_lookback_days
    )
    n = len(y)

    if n < 3:
        raise ValueError(
            f"Not enough succession pairs to fit (need ≥3, got {n}) for metric={metric}"
        )

    # OLS via numpy lstsq
    coefs, residuals, rank, sv = np.linalg.lstsq(X, y, rcond=None)

    y_hat = X @ coefs
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dof = max(n - len(coefs), 1)
    residual_sd = math.sqrt(ss_res / dof)

    coefs_dict = {
        "beta_0": float(coefs[0]),
        "beta_predecessor": float(coefs[1]),
        "beta_drift": float(coefs[2]),
        "beta_cell_residual_sd": float(coefs[3]),
        "beta_brier_delta": float(coefs[4]),
        "beta_log_n_obs": float(coefs[5]),
    }

    # Per-coef contribution = abs(beta * std(feature)) / sum(all contributions)
    feature_std = np.std(X[:, 1:], axis=0, ddof=1)  # exclude intercept col
    raw_contrib = np.abs(coefs[1:]) * feature_std
    total = float(np.sum(raw_contrib)) or 1.0
    feature_importance = {
        k: float(v / total)
        for k, v in zip(
            ["predecessor", "drift", "cell_residual_sd", "brier_delta", "log_n_obs"],
            raw_contrib,
        )
    }

    # Compute per-cell residual SD for future x3 features
    cell_residuals: dict[str, list[float]] = {}
    for i, m in enumerate(meta):
        ck = m["cell_key"]
        cell_residuals.setdefault(ck, []).append(float(y[i] - y_hat[i]))
    cell_residual_sd = {
        ck: float(np.std(vs, ddof=1)) if len(vs) > 1 else float(abs(vs[0]))
        for ck, vs in cell_residuals.items()
    }
    feature_importance["cell_residual_sd"] = cell_residual_sd  # type: ignore[assignment]

    fitted_at_ms = _now_ms()
    coefs_json = json.dumps(coefs_dict)
    feat_json = json.dumps(feature_importance)

    db.execute(
        """
        INSERT INTO retrain_confidence_models
          (fitted_at_ms, metric, predecessor_lookback_days, successor_lookback_days,
           coefs_json, n_train_pairs, r_squared, residual_sd, feature_importance_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fitted_at_ms, metric, predecessor_lookback_days, successor_lookback_days,
            coefs_json, n, round(r_squared, 6), round(residual_sd, 6), feat_json,
        ),
    )
    db.commit()
    logger.info(
        "retrain_confidence: fitted metric=%s n=%d r2=%.4f residual_sd=%.4f",
        metric, n, r_squared, residual_sd,
    )
    return {
        "metric": metric,
        "fitted_at_ms": fitted_at_ms,
        "n_train_pairs": n,
        "r_squared": round(r_squared, 6),
        "residual_sd": round(residual_sd, 6),
        "coefs": coefs_dict,
        "predecessor_lookback_days": predecessor_lookback_days,
        "successor_lookback_days": successor_lookback_days,
    }


# ---------------------------------------------------------------------------
# predict_for_model
# ---------------------------------------------------------------------------

def predict_for_model(
    model_name: str,
    metric: str = "composite",
) -> dict | None:
    """Apply the most-recent fitted model to model_name and write a prediction.

    Returns None if no fitted model exists or no predecessor data available.
    """
    if metric not in _VALID_METRICS:
        raise ValueError(f"metric must be one of {_VALID_METRICS}, got {metric!r}")

    db = _get_db()

    # Load most recent fit
    fit_row = db.execute(
        """
        SELECT fitted_at_ms, coefs_json, residual_sd,
               predecessor_lookback_days, successor_lookback_days,
               feature_importance_json
        FROM retrain_confidence_models
        WHERE metric = ?
        ORDER BY fitted_at_ms DESC
        LIMIT 1
        """,
        (metric,),
    ).fetchone()
    if fit_row is None:
        logger.debug("predict_for_model: no fitted model for metric=%s", metric)
        return None

    coefs = json.loads(fit_row["coefs_json"])
    residual_sd = float(fit_row["residual_sd"] or 0.0)
    pred_lookback = int(fit_row["predecessor_lookback_days"])
    fitted_at_ms = int(fit_row["fitted_at_ms"])

    # Look up cell for this model
    reg = db.execute(
        "SELECT symbol, training_horizon_seconds, train_days, "
        "train_window_end, parent_model_name "
        "FROM model_registry WHERE name = ?",
        (model_name,),
    ).fetchone()
    if reg is None:
        logger.debug("predict_for_model: model_name %s not in registry", model_name)
        return None

    symbol = reg["symbol"]
    train_end = (reg["train_window_end"] or "")[:10]
    cell_key = f"{symbol}_{reg['training_horizon_seconds']}_{reg['train_days']}"

    # Predecessor: use parent_model_name if set, else most recent prior model in same cell
    predecessor_name = reg["parent_model_name"]
    if not predecessor_name:
        pred_row = db.execute(
            """
            SELECT name FROM model_registry
            WHERE symbol = ? AND training_horizon_seconds = ? AND train_days = ?
              AND name != ?
              AND COALESCE(is_baseline, 0) = 0
            ORDER BY COALESCE(train_window_end, created_at) DESC
            LIMIT 1
            """,
            (symbol, reg["training_horizon_seconds"], reg["train_days"], model_name),
        ).fetchone()
        predecessor_name = pred_row["name"] if pred_row else None

    if not predecessor_name:
        logger.debug("predict_for_model: no predecessor found for %s", model_name)
        return None

    # Compute cell drift BEFORE any write operations to avoid WAL deadlock:
    # training_drift.get_or_compute_snapshot opens its own write connection,
    # which would race our pending INSERT below if called after db is opened
    # in write mode.
    x2 = _get_cell_drift(predecessor_name, model_name)

    # Build feature vector
    pred_since = (
        _date_after(train_end, -pred_lookback) if train_end
        else _days_since_date(pred_lookback)
    )
    x1 = _metric_value(db, predecessor_name, symbol, pred_since, metric)
    if x1 is None:
        x1 = 0.0  # no history yet — fallback

    # x3: cell residual sd from feature_importance_json
    x3 = 0.0
    try:
        fi = json.loads(fit_row["feature_importance_json"] or "{}")
        crs = fi.get("cell_residual_sd", {})
        x3 = float(crs.get(cell_key, 0.0))
    except Exception:
        pass

    # x4: brier delta
    snaps = db.execute(
        "SELECT model_name, training_brier FROM training_data_snapshots "
        "WHERE model_name IN (?, ?)",
        (predecessor_name, model_name),
    ).fetchall()
    snap_map = {r["model_name"]: r["training_brier"] for r in snaps}
    pb = snap_map.get(predecessor_name)
    sb = snap_map.get(model_name)
    x4 = float(sb - pb) if (pb is not None and sb is not None) else 0.0

    # x5: log(n_train_obs)
    nobs_row = db.execute(
        "SELECT n_train_obs FROM training_data_snapshots WHERE model_name = ?",
        (model_name,),
    ).fetchone()
    nobs = nobs_row["n_train_obs"] if nobs_row else None
    x5 = math.log(float(nobs)) if (nobs and nobs > 0) else 0.0

    xvec = np.array([1.0, x1, x2, x3, x4, x5])
    beta = np.array([
        coefs["beta_0"],
        coefs["beta_predecessor"],
        coefs["beta_drift"],
        coefs["beta_cell_residual_sd"],
        coefs["beta_brier_delta"],
        coefs["beta_log_n_obs"],
    ])
    predicted_value = float(xvec @ beta)
    predicted_lo = predicted_value - residual_sd
    predicted_hi = predicted_value + residual_sd

    inputs = {
        "predecessor": predecessor_name,
        "cell_key": cell_key,
        "x1_pred_metric": x1,
        "x2_drift": x2,
        "x3_cell_residual_sd": x3,
        "x4_brier_delta": x4,
        "x5_log_n_obs": x5,
    }

    predicted_at_ms = _now_ms()
    db.execute(
        """
        INSERT INTO retrain_confidence_predictions
          (model_name, metric, predicted_value, predicted_lo, predicted_hi,
           inputs_json, fitted_at_ms, predicted_at_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(model_name, metric, predicted_at_ms) DO UPDATE SET
          predicted_value = excluded.predicted_value,
          predicted_lo    = excluded.predicted_lo,
          predicted_hi    = excluded.predicted_hi,
          inputs_json     = excluded.inputs_json,
          fitted_at_ms    = excluded.fitted_at_ms
        """,
        (
            model_name, metric,
            round(predicted_value, 6),
            round(predicted_lo, 6),
            round(predicted_hi, 6),
            json.dumps(inputs),
            fitted_at_ms,
            predicted_at_ms,
        ),
    )
    db.commit()
    logger.info(
        "retrain_confidence: predicted %s metric=%s val=%.4f [%.4f, %.4f]",
        model_name, metric, predicted_value, predicted_lo, predicted_hi,
    )
    return {
        "model_name": model_name,
        "metric": metric,
        "predicted_value": round(predicted_value, 6),
        "predicted_lo": round(predicted_lo, 6),
        "predicted_hi": round(predicted_hi, 6),
        "predicted_at_ms": predicted_at_ms,
        "fitted_at_ms": fitted_at_ms,
        "inputs": inputs,
    }


# ---------------------------------------------------------------------------
# get_predicted_band
# ---------------------------------------------------------------------------

def get_predicted_band(model_name: str, metric: str = "composite") -> dict | None:
    """Return the most recent retrain_confidence_predictions row for
    (model_name, metric), or None if no prediction has ever been made.
    """
    db = _get_db()
    row = db.execute(
        """
        SELECT model_name, metric, predicted_value, predicted_lo, predicted_hi,
               inputs_json, fitted_at_ms, predicted_at_ms,
               realized_value, realized_at_ms
        FROM retrain_confidence_predictions
        WHERE model_name = ? AND metric = ?
        ORDER BY predicted_at_ms DESC
        LIMIT 1
        """,
        (model_name, metric),
    ).fetchone()
    if row is None:
        return None
    return {
        "model_name": row["model_name"],
        "metric": row["metric"],
        "predicted_value": row["predicted_value"],
        "predicted_lo": row["predicted_lo"],
        "predicted_hi": row["predicted_hi"],
        "inputs": json.loads(row["inputs_json"]) if row["inputs_json"] else None,
        "fitted_at_ms": row["fitted_at_ms"],
        "predicted_at_ms": row["predicted_at_ms"],
        "realized_value": row["realized_value"],
        "realized_at_ms": row["realized_at_ms"],
    }


# ---------------------------------------------------------------------------
# realize_pending_predictions
# ---------------------------------------------------------------------------

def realize_pending_predictions(now_ms: int) -> int:
    """Fill in realized_value for predictions whose window has elapsed.

    For each retrain_confidence_predictions row where realized_value IS NULL
    and predicted_at_ms is older than successor_lookback_days:
      1. Find the fitted model to get successor_lookback_days.
      2. Compute the model's actual metric over that window from
         predictions_daily_rollup / model_tier_score.
      3. UPDATE the row.

    Returns count of rows realized.
    """
    db = _get_db()

    # Load successor_lookback_days for each metric from the most recent fit
    lookback_by_metric: dict[str, int] = {}
    for m in _VALID_METRICS:
        row = db.execute(
            "SELECT successor_lookback_days FROM retrain_confidence_models "
            "WHERE metric = ? ORDER BY fitted_at_ms DESC LIMIT 1",
            (m,),
        ).fetchone()
        lookback_by_metric[m] = int(row["successor_lookback_days"]) if row else _DEFAULT_SUCCESSOR_LOOKBACK

    # Find pending rows whose window has elapsed
    pending = db.execute(
        """
        SELECT model_name, metric, predicted_at_ms
        FROM retrain_confidence_predictions
        WHERE realized_value IS NULL
        ORDER BY predicted_at_ms ASC
        """
    ).fetchall()

    count = 0
    for p in pending:
        model_name = p["model_name"]
        metric = p["metric"]
        predicted_at_ms = int(p["predicted_at_ms"])
        lookback_days = lookback_by_metric.get(metric, _DEFAULT_SUCCESSOR_LOOKBACK)
        cutoff_ms = predicted_at_ms + lookback_days * 86_400_000

        if now_ms < cutoff_ms:
            continue  # window hasn't elapsed yet

        # Determine symbol for this model
        reg = db.execute(
            "SELECT symbol, train_window_end FROM model_registry WHERE name = ?",
            (model_name,),
        ).fetchone()
        if reg is None:
            continue

        symbol = reg["symbol"]
        # Window = predicted_at_ms → predicted_at_ms + lookback_days
        since_date = _time.strftime("%Y-%m-%d", _time.gmtime(predicted_at_ms / 1000))
        realized = _metric_value(db, model_name, symbol, since_date, metric)
        if realized is None:
            continue  # still no data — try again next tick

        realized_at_ms = _now_ms()
        db.execute(
            """
            UPDATE retrain_confidence_predictions
            SET realized_value = ?, realized_at_ms = ?
            WHERE model_name = ? AND metric = ? AND predicted_at_ms = ?
            """,
            (round(realized, 6), realized_at_ms, model_name, metric, predicted_at_ms),
        )
        count += 1

    if count:
        db.commit()
        logger.info("retrain_confidence: realized %d predictions", count)
    return count
