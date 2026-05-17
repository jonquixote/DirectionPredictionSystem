"""Per-model probability calibration via isotonic regression.

Calibrators fit from resolved evaluation predictions in `predictions` table.
Cold start (insufficient data) → passthrough (returns raw probability).
Refit triggered every N new resolved predictions per model.
Hash exposed for provenance tracking.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import Optional

try:
    from sklearn.isotonic import IsotonicRegression
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

logger = logging.getLogger("calibrator_registry")

MIN_N_FOR_FIT = 100
DEFAULT_REFIT_TRIGGER = 50


class CalibratorRegistry:
    """Per-model isotonic calibration with database persistence.

    Maps raw model probability to empirically observed outcome frequency.
    Fit from resolved evaluation predictions; monotonic via isotonic regression.
    """

    def __init__(self, conn: sqlite3.Connection):
        """Initialize registry from database connection.

        Args:
            conn: SQLite3 connection with initialized schema.
        """
        self._conn = conn
        # model_name -> {iso, hash, n_obs, fit_at_ms}
        self._cache: dict[str, dict] = {}
        self._load_all()

    def _load_all(self) -> None:
        """Load all calibration maps from database into memory cache."""
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(
            "SELECT model_name, x_json, y_json, fit_at_ms, n_obs, map_hash "
            "FROM calibration_map"
        ).fetchall()
        for r in rows:
            self._cache[r["model_name"]] = self._materialize(dict(r))

    def _materialize(self, row: dict) -> dict:
        """Reconstruct IsotonicRegression from stored JSON knots."""
        x = json.loads(row["x_json"])
        y = json.loads(row["y_json"])
        iso = None
        if _HAS_SKLEARN and len(x) >= 2:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(x, y)
        return {
            "iso": iso,
            "x": x,
            "y": y,
            "hash": row["map_hash"],
            "n_obs": row["n_obs"],
            "fit_at_ms": row["fit_at_ms"],
        }

    def calibrate(self, model_name: str, raw_proba: float) -> tuple[float, Optional[str]]:
        """Map raw probability to calibrated via isotonic regression.

        Returns (calibrated_proba, map_hash). map_hash is None when passthrough
        (no calibrator fitted for model, or sklearn missing).
        """
        entry = self._cache.get(model_name)
        if entry is None or entry["iso"] is None:
            return raw_proba, None

        # Predict via isotonic regression (already bounded to [0, 1])
        cal = float(entry["iso"].predict([raw_proba])[0])
        cal = max(0.0, min(1.0, cal))  # defensive clip
        return cal, entry["hash"]

    def get_hash(self, model_name: str) -> Optional[str]:
        """Retrieve calibration map hash for provenance tracking."""
        entry = self._cache.get(model_name)
        return entry["hash"] if entry else None

    def maybe_refit(self, model_name: str, min_new_obs: int = DEFAULT_REFIT_TRIGGER) -> bool:
        """Check if model has accumulated enough new data to trigger refit.

        Returns True if refit was performed, False otherwise.
        """
        entry = self._cache.get(model_name)
        last_fit_ms = entry["fit_at_ms"] if entry else 0

        # Count new resolved evaluation predictions since last fit
        n_new = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM predictions "
        "WHERE model_name=? AND resolution_type='evaluation' AND resolved=1 "
        "AND ts_resolve_at_ms > ?",
            (model_name, last_fit_ms),
        ).fetchone()[0]

        # Total resolved for this model
        n_total = self._conn.execute(
            "SELECT COUNT(*) as cnt FROM predictions "
            "WHERE model_name=? AND resolution_type='evaluation' AND resolved=1",
            (model_name,),
        ).fetchone()[0]

        # Don't refit if insufficient total data
        if n_total < MIN_N_FOR_FIT:
            return False

        # Don't refit if not enough new data since last fit
        if entry is not None and n_new < min_new_obs:
            return False

        # Trigger refit
        return self.refit(model_name)

    def refit(self, model_name: str) -> bool:
        """Refit isotonic calibration from resolved evaluation predictions.
        
        Returns True if refit succeeded, False if insufficient data or sklearn missing.
        """
        if not _HAS_SKLEARN:
            logger.warning("calibrator_refit: sklearn not available for %s", model_name)
            return False

        # Fetch resolved evaluation predictions with direction and correctness
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(
            "SELECT pred_proba_raw, pred_direction, prediction_correct, ts_resolve_at_ms FROM predictions "
            "WHERE model_name=? AND resolution_type='evaluation' AND resolved=1 "
            "ORDER BY ts_resolve_at_ms",
            (model_name,),
        ).fetchall()

        if len(rows) < MIN_N_FOR_FIT:
            logger.debug("calibrator_refit: insufficient data (%d < %d) for %s",
                        len(rows), MIN_N_FOR_FIT, model_name)
            return False

        # Build (raw_prob, empirical_outcome) pairs
        x_arr = []
        y_arr = []
        for row in rows:
            p = row["pred_proba_raw"]
            d = row["pred_direction"]
            correct = row["prediction_correct"]

            # Determine if outcome was "up" from direction and correctness
            # If we predicted "up" and were correct, outcome was up.
            # If we predicted "down" and were correct, outcome was down.
            # If we predicted "up" and were wrong, outcome was down.
            # If we predicted "down" and were wrong, outcome was up.
            if d == "up" and correct == 1:
                outcome_up = 1
            elif d == "down" and correct == 0:
                outcome_up = 1
            else:
                outcome_up = 0

            x_arr.append(p)
            y_arr.append(outcome_up)

        # Fit isotonic regression
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(x_arr, y_arr)

        # Extract fitted curve knots for compact storage
        x_knots = [float(v) for v in iso.X_thresholds_]
        y_knots = [float(v) for v in iso.y_thresholds_]

        # Compute hash for provenance
        fit_at_ms = max(row["ts_resolve_at_ms"] for row in rows)
        map_hash = hashlib.sha256(
            json.dumps(
                {"x": x_knots, "y": y_knots, "n": len(rows)},
                sort_keys=True
            ).encode()
        ).hexdigest()[:16]

        # Persist to database
        self._conn.execute(
            "INSERT OR REPLACE INTO calibration_map "
            "(model_name, x_json, y_json, fit_at_ms, n_obs, map_hash, fit_method) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                model_name,
                json.dumps(x_knots),
                json.dumps(y_knots),
                fit_at_ms,
                len(rows),
                map_hash,
                "isotonic",
            ),
        )
        self._conn.commit()

        # Update in-memory cache
        self._cache[model_name] = {
            "iso": iso,
            "x": x_knots,
            "y": y_knots,
            "hash": map_hash,
            "n_obs": len(rows),
            "fit_at_ms": fit_at_ms,
        }

        logger.info(
            "calibrator_refit: %s fitted with %d resolved evaluation predictions, hash=%s",
            model_name, len(rows), map_hash,
        )
        return True

    def reload(self, model_name: str) -> None:
        """Reload calibrator from database after external changes.

        Useful if calibration_map table was modified directly.
        """
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute(
            "SELECT model_name, x_json, y_json, fit_at_ms, n_obs, map_hash "
            "FROM calibration_map WHERE model_name=?",
            (model_name,),
        ).fetchone()
        if row:
            self._cache[model_name] = self._materialize(dict(row))
        else:
            self._cache.pop(model_name, None)
