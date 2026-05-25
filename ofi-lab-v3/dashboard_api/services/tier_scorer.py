"""Composite tier scoring for model governance (Phase 3b).

Computes a single composite_score per (model, symbol, market_window, regime)
combining recency-weighted EV from `decay_metrics`, optional walk-forward
EV, calibration drift since training, decay slope, and stability.

Composite formula
-----------------
    score = w_live  * live_rwev
          + w_paper * paper_rwev
          + w_wf    * walk_forward_ev
          - w_drift * abs(brier_now - brier_baseline)
          - w_decay * max(0, -decay_slope)
          + w_stab  * (1 - clamp(variance, 0, 1))

When live trade data is unavailable (the common case while we're still in
paper-only mode), live's weight folds into paper.

Cadence
-------
Called from `_tier_scoring_loop` background task every 60 minutes per
active model. Snapshots written to `model_tier_score`. Idempotent via
INSERT only — no upsert (we want the history for trend analysis).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

logger = logging.getLogger("dashboard.tier_scorer")


_DEFAULT_WEIGHTS_LIVE = {
    "live": 0.50,
    "paper": 0.25,
    "wf": 0.15,
    "drift": 0.10,
    "decay": 0.10,
    "stability": 0.05,
}

_DEFAULT_WEIGHTS_PAPER_ONLY = {
    "live": 0.0,
    "paper": 0.65,
    "wf": 0.15,
    "drift": 0.10,
    "decay": 0.10,
    "stability": 0.05,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _safe(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _pull_decay_components(conn: sqlite3.Connection,
                           model_name: str, symbol: str,
                           market_window: int) -> dict:
    """Return the most-recent + 7d-baseline decay state for the given cell.

    Components:
      paper_rwev:        latest recency_weighted_ev
      live_rwev:         None for now (we don't separate paper-only vs live trades
                         in decay_metrics; Phase 5 will populate)
      brier_now:         latest brier_score
      brier_baseline_7d: avg brier over the prior 7 days
      decay_slope:       (latest rolling_ev) - (7d-ago rolling_ev)
      variance:          variance of rolling_ev over last 7d
      sample_count:      latest sample_count
    """
    row = conn.execute(
        "SELECT recency_weighted_ev, rolling_ev, brier_score, calibration_error,"
        "       sample_count, ts"
        " FROM decay_metrics"
        " WHERE model_name = ? AND symbol = ? AND market_window_seconds = ?"
        " ORDER BY ts DESC LIMIT 1",
        (model_name, symbol, market_window),
    ).fetchone()
    if not row:
        return {
            "paper_rwev": None, "live_rwev": None,
            "brier_now": None, "brier_baseline_7d": None,
            "decay_slope": None, "variance": None, "sample_count": 0,
        }
    base = conn.execute(
        "SELECT AVG(brier_score) AS avg_brier,"
        "       AVG(rolling_ev) AS avg_rolling_ev"
        " FROM decay_metrics"
        " WHERE model_name = ? AND symbol = ? AND market_window_seconds = ?"
        "   AND ts >= datetime('now','-7 day')",
        (model_name, symbol, market_window),
    ).fetchone()
    # Slope: latest rolling_ev vs avg of 7d-ago window. Simple approximation.
    earliest = conn.execute(
        "SELECT rolling_ev FROM decay_metrics"
        " WHERE model_name = ? AND symbol = ? AND market_window_seconds = ?"
        "   AND ts >= datetime('now','-7 day')"
        " ORDER BY ts ASC LIMIT 1",
        (model_name, symbol, market_window),
    ).fetchone()
    # Variance: simple SUM((x-avg)^2)/N over 7d rolling_ev samples.
    var_rows = conn.execute(
        "SELECT rolling_ev FROM decay_metrics"
        " WHERE model_name = ? AND symbol = ? AND market_window_seconds = ?"
        "   AND ts >= datetime('now','-7 day')"
        "   AND rolling_ev IS NOT NULL",
        (model_name, symbol, market_window),
    ).fetchall()
    variance: float | None = None
    if var_rows and base and base["avg_rolling_ev"] is not None:
        avg = base["avg_rolling_ev"]
        n = len(var_rows)
        if n > 1:
            variance = sum((r[0] - avg) ** 2 for r in var_rows) / n
    decay_slope: float | None = None
    if row and earliest and row["rolling_ev"] is not None and earliest[0] is not None:
        decay_slope = row["rolling_ev"] - earliest[0]
    return {
        "paper_rwev": _safe(row["recency_weighted_ev"]),
        "live_rwev": None,
        "brier_now": _safe(row["brier_score"]),
        "brier_baseline_7d": _safe(base["avg_brier"]) if base else None,
        "decay_slope": _safe(decay_slope),
        "variance": _safe(variance),
        "sample_count": int(row["sample_count"]) if row["sample_count"] is not None else 0,
    }


def _compute_composite(components: Mapping[str, float | None],
                       weights: Mapping[str, float]) -> float:
    """Linear combination with None-handling: missing components zero out."""
    def g(k: str) -> float:
        v = components.get(k)
        return float(v) if v is not None else 0.0

    live = g("live_rwev")
    paper = g("paper_rwev")
    wf = g("walk_forward_ev")
    drift_abs = abs(g("brier_now") - g("brier_baseline_7d")) if components.get("brier_now") is not None and components.get("brier_baseline_7d") is not None else 0.0
    decay_neg = max(0.0, -g("decay_slope"))
    var = max(0.0, min(1.0, g("variance")))
    stability = 1.0 - var

    return (
        weights.get("live", 0.0)  * live
        + weights.get("paper", 0.0) * paper
        + weights.get("wf", 0.0)    * wf
        - weights.get("drift", 0.0) * drift_abs
        - weights.get("decay", 0.0) * decay_neg
        + weights.get("stability", 0.0) * stability
    )


def compute_tier_score(conn: sqlite3.Connection,
                       model_name: str, symbol: str,
                       market_window: int,
                       walk_forward_ev: float | None = None) -> dict:
    """Build a single composite tier-score snapshot row for the given cell.

    Returns dict suitable for INSERT into model_tier_score.
    """
    comps = _pull_decay_components(conn, model_name, symbol, market_window)
    # Tag with whether any live trade data exists for the model. For now,
    # paper-only mode: live weight stays 0.
    weights = dict(_DEFAULT_WEIGHTS_PAPER_ONLY)
    composite = _compute_composite(
        {**comps, "walk_forward_ev": walk_forward_ev},
        weights,
    )
    return {
        "ts": _now_iso(),
        "model_name": model_name,
        "symbol": symbol,
        "market_window_seconds": market_window,
        "regime_label": "*",
        "composite_score": composite,
        "component_live_rwev": comps["live_rwev"],
        "component_paper_rwev": comps["paper_rwev"],
        "component_walk_forward_ev": walk_forward_ev,
        "component_calibration_drift": (
            (comps["brier_now"] or 0.0) - (comps["brier_baseline_7d"] or 0.0)
            if comps["brier_now"] is not None and comps["brier_baseline_7d"] is not None
            else None
        ),
        "component_decay_slope": comps["decay_slope"],
        "component_stability": (1.0 - max(0.0, min(1.0, comps["variance"]))) if comps["variance"] is not None else None,
        "weights_json": json.dumps(weights),
        "sample_count": comps["sample_count"],
    }


def write_tier_score(conn: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    """INSERT one snapshot row into model_tier_score."""
    conn.execute(
        "INSERT INTO model_tier_score("
        " ts, model_name, symbol, market_window_seconds, regime_label,"
        " composite_score, component_live_rwev, component_paper_rwev,"
        " component_walk_forward_ev, component_calibration_drift,"
        " component_decay_slope, component_stability, weights_json,"
        " sample_count"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            row["ts"], row["model_name"], row["symbol"],
            row["market_window_seconds"], row["regime_label"],
            row["composite_score"], row["component_live_rwev"],
            row["component_paper_rwev"], row["component_walk_forward_ev"],
            row["component_calibration_drift"], row["component_decay_slope"],
            row["component_stability"], row["weights_json"], row["sample_count"],
        ),
    )


def compute_all_tier_scores(conn: sqlite3.Connection) -> list[dict]:
    """Iterate all paper_active=1 models and write a tier score for each
    (model, symbol, market_window) triple seen in `decay_metrics`.

    Returns list of inserted snapshot dicts (for tests / observability).
    """
    triples = conn.execute(
        "SELECT DISTINCT mr.name, dm.symbol, dm.market_window_seconds"
        " FROM model_registry mr"
        " JOIN decay_metrics dm ON dm.model_name = mr.name"
        " WHERE mr.paper_active = 1"
    ).fetchall()
    out: list[dict] = []
    for row in triples:
        try:
            snap = compute_tier_score(
                conn, row["name"], row["symbol"], row["market_window_seconds"],
                walk_forward_ev=None,
            )
            write_tier_score(conn, snap)
            out.append(snap)
        except Exception as exc:
            logger.warning(
                "compute_tier_score failed for %s/%s/%s: %s",
                row["name"], row["symbol"], row["market_window_seconds"], exc,
            )
    conn.commit()
    return out
