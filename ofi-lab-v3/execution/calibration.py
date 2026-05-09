"""Probability calibration for live trading.

Tree-based models (LightGBM, XGBoost) output uncalibrated probabilities.
Raw scores near 0.5 may not correspond to true 50% win rates. This module
maps raw model output to empirically observed win rates so Kelly sizing
matches the actual statistical edge.

Default behavior is identity (no calibration). To enable, write a JSON map
to KALSHI_CALIBRATION_PATH (default: /data/calibration.json):

  {
    "method": "binmap",
    "bins": [
      {"raw": 0.45, "calibrated": 0.42},
      {"raw": 0.50, "calibrated": 0.50},
      {"raw": 0.53, "calibrated": 0.55},
      {"raw": 0.55, "calibrated": 0.58},
      {"raw": 0.60, "calibrated": 0.65}
    ]
  }

Linear interpolation between adjacent raw values; clipped to the nearest
calibrated value for inputs outside the defined range.

The map can be fit from historical (model_p, outcome) pairs in the
predictions ledger via scripts/fit_calibration.py (not required at boot).

Auto-recalibration: call record_outcome(raw_p, won) after each prediction
resolution. Once ≥ MIN_REFIT_SAMPLES outcomes have been collected the
calibrator automatically refits the bin map and atomically overwrites the
calibration file. Outcomes are durably persisted to a JSONL file so they
survive container restarts.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import List

logger = logging.getLogger("calibration")

MIN_REFIT_SAMPLES = 30     # Don't refit until we have this many outcomes
BIN_WIDTH = 0.01           # Same as fit_calibration.py
MIN_SAMPLES_PER_BIN = 5    # Lower than offline script for faster adaptation


class ProbabilityCalibrator:
    """Loads a bin-map from disk and maps raw → calibrated probabilities.

    Thread-safe for read; load_map() should not be called concurrently with
    calibrate(). In practice the map is loaded once at startup and refreshed
    via reload() when the file changes on disk.
    """

    def __init__(self, path: str = "") -> None:
        self._path = path or os.environ.get("KALSHI_CALIBRATION_PATH", "/data/calibration.json")
        self._raw_pts: List[float] = []
        self._cal_pts: List[float] = []
        self._enabled = False
        self._last_mtime: float = 0.0
        self._lock = threading.Lock()

        # Auto-recalibration state
        self._outcomes: List[tuple[float, bool]] = []  # (side_conf, won)
        outcomes_dir = os.path.dirname(self._path) if self._path else "/data"
        self._outcomes_path = os.path.join(outcomes_dir, "calibration_outcomes.jsonl")
        self._load()
        self._load_outcomes()

    def _load(self) -> None:
        if not self._path or not os.path.exists(self._path):
            logger.info(
                "calibration: no map at %r — using identity (raw → raw)",
                self._path,
            )
            return
        try:
            mtime = os.path.getmtime(self._path)
            with open(self._path) as f:
                data = json.load(f)
            bins = data.get("bins") or []
            if not bins:
                logger.warning("calibration: empty bins in %s — using identity", self._path)
                return
            sorted_bins = sorted(bins, key=lambda b: float(b["raw"]))
            self._raw_pts = [float(b["raw"]) for b in sorted_bins]
            self._cal_pts = [float(b["calibrated"]) for b in sorted_bins]
            for c in self._cal_pts:
                if not 0.0 <= c <= 1.0:
                    logger.warning(
                        "calibration: out-of-range calibrated value %.4f in %s",
                        c, self._path,
                    )
            self._enabled = True
            self._last_mtime = mtime
            logger.info(
                "calibration: loaded %d bins from %s — sample 0.50 → %.4f",
                len(self._raw_pts), self._path, self.calibrate(0.50),
            )
        except Exception as e:
            logger.warning("calibration: load failed (%s) — using identity", e)

    def _load_outcomes(self) -> None:
        """Reload durable outcomes from disk on startup."""
        if not os.path.exists(self._outcomes_path):
            return
        try:
            loaded = 0
            with open(self._outcomes_path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        self._outcomes.append((float(d["side_conf"]), bool(d["won"])))
                        loaded += 1
                    except (json.JSONDecodeError, KeyError):
                        continue
            if loaded:
                logger.info("calibration: restored %d outcomes from %s", loaded, self._outcomes_path)
        except Exception as e:
            logger.warning("calibration: failed to load outcomes: %s", e)

    def reload(self) -> bool:
        """Re-read calibration map from disk if the file has changed.

        Returns True if the map was actually reloaded, False if unchanged
        or missing. Safe to call frequently (stat-only when unchanged).
        """
        if not self._path or not os.path.exists(self._path):
            return False
        try:
            mtime = os.path.getmtime(self._path)
            if mtime <= self._last_mtime:
                return False
            logger.info("calibration: file changed (mtime %.0f → %.0f), reloading",
                        self._last_mtime, mtime)
            self._load()
            return True
        except Exception as e:
            logger.warning("calibration: reload check failed: %s", e)
            return False

    def is_enabled(self) -> bool:
        return self._enabled

    def calibrate(self, raw_p: float) -> float:
        """Map raw model probability to empirically calibrated probability.

        Linear interpolation between adjacent bin points. Inputs outside
        the defined range are clipped to the nearest endpoint's calibrated
        value (NOT extrapolated, to avoid unsafe sizing on unfit regions).
        """
        if not self._enabled:
            return raw_p
        if raw_p <= self._raw_pts[0]:
            return self._cal_pts[0]
        if raw_p >= self._raw_pts[-1]:
            return self._cal_pts[-1]
        for i in range(len(self._raw_pts) - 1):
            x0, x1 = self._raw_pts[i], self._raw_pts[i + 1]
            if x0 <= raw_p <= x1:
                y0, y1 = self._cal_pts[i], self._cal_pts[i + 1]
                if x1 == x0:
                    return y0
                t = (raw_p - x0) / (x1 - x0)
                return y0 + t * (y1 - y0)
        # Unreachable — bin search is exhaustive — but defensive return.
        return raw_p

    # ─── Auto-recalibration ─────────────────────────────────────────

    def record_outcome(self, raw_p: float, won: bool) -> bool:
        """Record a (raw_p, won) outcome and refit if enough data.

        raw_p is the model's raw output (0–1). For "down" predictions
        the caller should pass (1 - model_p) so that side_conf is
        always the confidence on the chosen side.

        Returns True if a refit was triggered.
        """
        side_conf = raw_p
        with self._lock:
            self._outcomes.append((side_conf, won))
            # Persist to disk
            self._append_outcome_to_disk(side_conf, won)

            if len(self._outcomes) >= MIN_REFIT_SAMPLES:
                self._refit()
                return True
        return False

    def _append_outcome_to_disk(self, side_conf: float, won: bool) -> None:
        """Append a single outcome to the durable JSONL file."""
        try:
            os.makedirs(os.path.dirname(self._outcomes_path), exist_ok=True)
            with open(self._outcomes_path, "a") as f:
                row = {
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "side_conf": round(side_conf, 6),
                    "won": won,
                }
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.warning("calibration: outcome write failed: %s", e)

    def _refit(self) -> None:
        """Refit the calibration bins from all accumulated outcomes and
        atomically overwrite the calibration.json file.

        Must be called under self._lock.
        """
        bin_data: dict[float, list[int]] = collections.defaultdict(lambda: [0, 0])
        for conf, won in self._outcomes:
            if conf < 0.50:
                continue
            bin_center = round(round(conf / BIN_WIDTH) * BIN_WIDTH, 4)
            bin_data[bin_center][0] += int(won)
            bin_data[bin_center][1] += 1

        fitted = []
        for bin_center in sorted(bin_data.keys()):
            wins, total = bin_data[bin_center]
            if total < MIN_SAMPLES_PER_BIN:
                continue
            cal = round(wins / total, 4)
            fitted.append({"raw": bin_center, "calibrated": cal, "n": total, "wins": wins})

        if not fitted:
            logger.info("calibration: refit skipped — no bins with >= %d samples", MIN_SAMPLES_PER_BIN)
            return

        # Update in-memory state
        self._raw_pts = [b["raw"] for b in fitted]
        self._cal_pts = [b["calibrated"] for b in fitted]
        self._enabled = True

        # Atomically write to disk (write temp, then rename)
        out = {
            "method": "binmap",
            "fit_at": datetime.now(timezone.utc).isoformat(),
            "source": "auto_refit",
            "n_total_outcomes": len(self._outcomes),
            "min_samples_per_bin": MIN_SAMPLES_PER_BIN,
            "bin_width": BIN_WIDTH,
            "bins": [{"raw": b["raw"], "calibrated": b["calibrated"]} for b in fitted],
            "_diagnostics": {"fitted_bins_with_counts": fitted},
        }
        try:
            tmp_path = self._path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(out, f, indent=2)
            os.replace(tmp_path, self._path)
            self._last_mtime = os.path.getmtime(self._path)
            logger.info(
                "calibration: auto-refit wrote %d bins from %d outcomes to %s",
                len(fitted), len(self._outcomes), self._path,
            )
        except Exception as e:
            logger.warning("calibration: auto-refit write failed: %s", e)

    @property
    def outcome_count(self) -> int:
        """Number of recorded outcomes (for diagnostics)."""
        return len(self._outcomes)
