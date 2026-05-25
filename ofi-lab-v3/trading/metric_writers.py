"""Background metric writers extracted from PaperTrader.

Owns the periodic bookkeeping that runs on a cadence (every 4 boundaries,
every 16 boundaries) but is not on the prediction critical path. Decoupling
keeps PaperTrader focused on the prediction → trade pipeline.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Dict, Optional, Tuple

if TYPE_CHECKING:
    from storage.registry_state import RegistryState

logger = logging.getLogger("metric_writers")

# Constants shared with PaperTrader
PREDICTION_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


class MetricWriters:
    """Holds the periodic bookkeeping writers extracted from PaperTrader.

    Parameters
    ----------
    db_conn:
        Open SQLite connection (same one used by PaperTrader).
    registry_state:
        RegistryState instance for reading the current generation counter.
    """

    def __init__(
        self,
        db_conn: sqlite3.Connection,
        registry_state: "RegistryState",
    ) -> None:
        self._db_conn = db_conn
        self.registry_state = registry_state
        # Lazy caches for the underlying writer objects
        self._decay_writer_cache: Optional[object] = None
        self._overlap_writer_cache: Optional[object] = None

    # ------------------------------------------------------------------
    # Internal writer accessors (lazy-init mirrors of PaperTrader props)
    # ------------------------------------------------------------------

    @property
    def _decay_writer(self):
        if self._decay_writer_cache is None:
            from storage.decay_writer import DecayWriter
            self._decay_writer_cache = DecayWriter(self._db_conn)
        return self._decay_writer_cache

    @property
    def _overlap_writer(self):
        if self._overlap_writer_cache is None:
            from trading.overlap_writer import OverlapWriter
            self._overlap_writer_cache = OverlapWriter(self._db_conn)
        return self._overlap_writer_cache

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def refresh_decay_metrics(self, *, window_size: int = 100) -> None:
        """Compute rolling decay metrics for every (model, symbol, window) and
        write a snapshot row to ``decay_metrics``.
        """
        from storage.decay_metrics import (
            compute_brier_score, compute_calibration_error,
            compute_rolling_ev, compute_recency_weighted_ev,
        )
        writer = self._decay_writer
        # Find every distinct (model, symbol, market_window_seconds)
        triples = self._db_conn.execute(
            "SELECT DISTINCT model_name, symbol, market_window_seconds"
            " FROM paper_trades"
            " WHERE resolution_type = 'evaluation' AND resolved = 1"
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
        " AND resolution_type = 'evaluation' AND resolved = 1 AND warmup = 0"
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
            rwev = compute_recency_weighted_ev(ev_values, alpha=0.05)
            brier = compute_brier_score(cal_rows)
            calib_err = compute_calibration_error(cal_rows)
            writer.write_snapshot(
                model_name=model_name, symbol=symbol,
                market_window_seconds=window,
                window_size=window_size,
                rolling_ev=compute_rolling_ev(ev_values),
                recency_weighted_ev=rwev,
                rolling_win_rate=win_rate,
                brier_score=brier,
                calibration_error=calib_err,
                sample_count=len(rows),
            )
            self._evaluate_decay_triggers(
                model_name=model_name,
                symbol=symbol,
                market_window_seconds=window,
                current_rwev=rwev,
                current_brier=brier,
                current_calib_err=calib_err,
                sample_count=len(rows),
            )

    def _evaluate_decay_triggers(
        self, *, model_name: str, symbol: str,
        market_window_seconds: int,
        current_rwev: float | None,
        current_brier: float | None,
        current_calib_err: float | None,
        sample_count: int,
    ) -> None:
        """Evaluate 3 decay triggers and write to decay_evaluations table.

        rwev_drop:         current_rwev < (7d_max_rwev - 0.02), n >= 30
        brier_rise:        current_brier > (7d_baseline_brier + 0.02), n >= 30
        calibration_drift: current_calib_err > 0.08, n >= 30
        """
        if sample_count < 30:
            return
        writer = self._decay_writer
        # Query 7-day baselines
        row = self._db_conn.execute(
            "SELECT MAX(recency_weighted_ev) AS max_rwev,"
            "       AVG(brier_score) AS avg_brier"
            "  FROM decay_metrics"
            " WHERE model_name = ? AND symbol = ?"
            "   AND market_window_seconds = ?"
            "   AND ts >= datetime('now','-7 day')",
            (model_name, symbol, market_window_seconds),
        ).fetchone()
        max_rwev_7d = row["max_rwev"] if row and row["max_rwev"] is not None else None
        avg_brier_7d = row["avg_brier"] if row and row["avg_brier"] is not None else None

        # rwev_drop
        if current_rwev is not None and max_rwev_7d is not None:
            threshold = max_rwev_7d - 0.02
            triggered = current_rwev < threshold
            writer.write_evaluation(
                model_name=model_name, symbol=symbol,
                market_window_seconds=market_window_seconds,
                eval_type="rwev_drop",
                metric_value=current_rwev,
                threshold=threshold,
                triggered=triggered,
                detail={"max_rwev_7d": max_rwev_7d, "sample_count": sample_count},
            )
        # brier_rise
        if current_brier is not None and avg_brier_7d is not None:
            threshold = avg_brier_7d + 0.02
            triggered = current_brier > threshold
            writer.write_evaluation(
                model_name=model_name, symbol=symbol,
                market_window_seconds=market_window_seconds,
                eval_type="brier_rise",
                metric_value=current_brier,
                threshold=threshold,
                triggered=triggered,
                detail={"avg_brier_7d": avg_brier_7d, "sample_count": sample_count},
            )
        # calibration_drift
        if current_calib_err is not None:
            threshold = 0.08
            triggered = current_calib_err > threshold
            writer.write_evaluation(
                model_name=model_name, symbol=symbol,
                market_window_seconds=market_window_seconds,
                eval_type="calibration_drift",
                metric_value=current_calib_err,
                threshold=threshold,
                triggered=triggered,
                detail={"sample_count": sample_count},
            )

    def refresh_price_ranges(
        self, tracked_symbols: Optional[list] = None
    ) -> Dict[str, Tuple[float, float]]:
        """Refresh dynamic price ranges for tracked symbols from klines.

        Returns
        -------
        dict mapping symbol -> (low, high) price range tuple.
        The caller (PaperTrader wrapper) assigns the result to
        ``self._price_ranges``.
        """
        from data.range_computer import compute_mid_price_range
        price_ranges: Dict[str, Tuple[float, float]] = {}
        tracked = tracked_symbols if tracked_symbols is not None else PREDICTION_SYMBOLS
        for symbol in tracked:
            try:
                price_range = compute_mid_price_range(
                    symbol, self._db_conn, lookback_days=7
                )
                if price_range:
                    price_ranges[symbol] = price_range
            except Exception as e:
                logger.exception("range_compute_failed",
                                extra={"symbol": symbol, "err": str(e)})
        return price_ranges

    def record_overlap_for_boundary(
        self, *, ts_contract_open_ms: int, symbol: str,
        market_window_seconds: int, scores,
    ) -> None:
        gen = self.registry_state.current_generation()
        self._overlap_writer.record_boundary(
            ts_contract_open_ms=ts_contract_open_ms,
            symbol=symbol,
            market_window_seconds=market_window_seconds,
            registry_load_generation=gen,
            scores=scores,
        )
