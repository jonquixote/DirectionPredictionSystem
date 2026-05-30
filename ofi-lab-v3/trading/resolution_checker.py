"""Prediction + trade resolution path extracted from PaperTrader.

Owns the "did the prediction win?" logic — pulls ripe entries from the
pending queue, fetches resolution prices from feature_computer, computes
outcomes, feeds the calibrator (300s only), and writes resolution rows
via the sqlite_ledger.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger("resolution_checker")


class ResolutionChecker:
    """Resolves pending predictions and open paper trades.

    Constructor args mirror the PaperTrader attributes they were previously
    read from (``self.X`` → ``self._X`` here).
    """

    def __init__(self, db_conn, sqlite_ledger, feature_computer, calibrators, pending_queue):
        self._db_conn = db_conn
        self._sqlite_ledger = sqlite_ledger
        self._last_skip_warn_ts = 0.0  # rate-limit no_price warnings to once per 5 min
        self._feature_computer = feature_computer
        self._calibrators = calibrators
        self._pending_queue = pending_queue

    # ------------------------------------------------------------------
    # Public entry points (async — same signature as PaperTrader methods)
    # ------------------------------------------------------------------

    async def check_predictions(self, now_ms: int) -> None:
        """Walk the pending queue and resolve every ripe row.

        300s eval rows also feed calibration_outcomes and the
        per-model calibrator. Other eval rows are resolved without
        calibration feedback. Each resolved row is removed from the
        queue. The queue is persisted at the end so a crash mid-loop
        leaves the partially-resolved state recoverable.
        """
        ripe = list(self._pending_queue.iter_ripe(now_ms))
        if not ripe:
            return
        for entry in ripe:
            try:
                close_price = self._feature_computer.price_at(
                    entry.symbol, entry.ts_resolve_at_ms,
                )
            except KeyError:
                continue
            check = self._db_conn.execute(
                "SELECT 1 FROM predictions WHERE prediction_id = ?",
                (entry.prediction_id,),
            ).fetchone()
            if check is None:
                self._pending_queue.remove(entry.prediction_id)
                continue
            result, correct = self.compute_outcome(
                direction=self.direction_for(entry.prediction_id),
                price_open=entry.price_at_open,
                price_close=close_price,
            )
            feed_cal = entry.market_window_seconds == 300
            self._sqlite_ledger.record_resolution(
                prediction_id=entry.prediction_id,
                ts_resolved_ms=entry.ts_resolve_at_ms,
                price_at_open=entry.price_at_open,
                price_at_close=close_price,
                contract_result=result,
                prediction_correct=correct,
                feed_calibrator=feed_cal,
            )
            if feed_cal:
                row = self._db_conn.execute(
                    "SELECT pred_proba_raw, warmup, model_name, symbol,"
                    " market_window_seconds FROM predictions"
                    " WHERE prediction_id = ?",
                    (entry.prediction_id,),
                ).fetchone()
                if row and row["warmup"] == 0:
                    cal = self._calibrators.get(
                        row["model_name"], row["symbol"],
                        row["market_window_seconds"],
                    )
                    cal.record_outcome(float(row["pred_proba_raw"]), bool(correct))
            self._pending_queue.remove(entry.prediction_id)
        self._pending_queue.persist()

    async def check_trades(self, now_ms: int) -> None:
        """Resolve any open paper_trades whose ts_resolve_at_ms <= now_ms.

        Uses Polymarket-style binary option PnL math (fee coef 0.072).
        Plan B extends this to a per-platform fee model.
        """
        # Auto-abandon trades whose resolve_at is older than the in-memory
        # price buffer (~40 min) by a 20-min safety margin. price_at can
        # never recover those, so they otherwise pollute the per-second
        # SELECT scan with N hundred no-op rows forever.
        stale_cutoff_ms = now_ms - 60 * 60 * 1000  # 60 min
        cur = self._db_conn.execute(
            "UPDATE paper_trades"
            " SET resolved=1, net_pnl=0, gross_pnl=0, fee_paid=0,"
            "     contract_result='unresolved', trade_result='abandoned',"
            "     pnl_method='abandoned_stale_no_price', ts_resolved_ms=?"
            " WHERE resolved=0 AND ts_resolve_at_ms < ?",
            (now_ms, stale_cutoff_ms),
        )
        abandoned_n = cur.rowcount or 0
        if abandoned_n > 0:
            logger.warning(
                "check_trades: auto-abandoned %d trades older than 60min "
                "(price buffer can't recover them)",
                abandoned_n,
            )

        rows = self._db_conn.execute(
            "SELECT trade_id, prediction_id, symbol,"
            " ts_resolve_at_ms, pred_proba_calibrated, pred_direction,"
            " simulated_stake_usdc, market_window_seconds"
            " FROM paper_trades WHERE resolved = 0 AND ts_resolve_at_ms <= ?",
            (now_ms,),
        ).fetchall()
        skipped_no_price = 0
        resolved_n = 0
        for row in rows:
            try:
                close = self._feature_computer.price_at(
                    row["symbol"], row["ts_resolve_at_ms"]
                )
            except (KeyError, AttributeError):
                # price_at scans an in-memory ~40-min buffer; anything older
                # than that (e.g. trades opened before the most recent
                # trader restart) cannot be resolved here. Track + log so
                # silent skips don't accumulate invisibly into a 25k-row
                # backlog like we saw 2026-05-30.
                skipped_no_price += 1
                continue
            pred = self._db_conn.execute(
                "SELECT price_at_open FROM predictions WHERE prediction_id = ?",
                (row["prediction_id"],)
            ).fetchone()
            if pred is None or pred["price_at_open"] is None:
                continue
            gross, fee, net, result, correct = self.compute_paper_pnl(
                direction=row["pred_direction"],
                calibrated_p=row["pred_proba_calibrated"],
                stake=row["simulated_stake_usdc"] or 10.0,
                price_open=pred["price_at_open"],
                price_close=close,
            )
            self._sqlite_ledger.record_trade_resolution(
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
            resolved_n += 1

        if rows and (skipped_no_price >= 50 or (skipped_no_price > 0 and resolved_n == 0)):
            import time as _time
            _now = _time.time()
            if (_now - self._last_skip_warn_ts) >= 300:  # 5-min dedup
                logger.warning(
                    "check_trades: resolved=%d skipped_no_price=%d (in-memory price buffer missing)",
                    resolved_n, skipped_no_price,
                )
                self._last_skip_warn_ts = _now

    # ------------------------------------------------------------------
    # Helpers (kept as instance methods so check_predictions can call them
    # without passing self explicitly; direction_for needs db access)
    # ------------------------------------------------------------------

    def direction_for(self, prediction_id: str) -> str:
        row = self._db_conn.execute(
            "SELECT pred_direction FROM predictions WHERE prediction_id = ?",
            (prediction_id,),
        ).fetchone()
        return row["pred_direction"] if row else "up"

    # ------------------------------------------------------------------
    # Pure / static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def compute_outcome(direction, price_open, price_close):
        if price_close > price_open:
            result = "up"
        elif price_close < price_open:
            result = "down"
        else:
            result = "flat"
        correct = (result == direction)
        return result, correct

    @staticmethod
    def compute_paper_pnl(*, direction, calibrated_p, stake,
                          price_open, price_close):
        """Polymarket-style binary option PnL.

        Lifted from v2 paper_trader.py _resolve_trade lines ~1100-1141.
        Fee coef 0.072 matches Polymarket's published rate.
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
