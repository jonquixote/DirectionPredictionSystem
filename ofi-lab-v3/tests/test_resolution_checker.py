"""Tests for trading/resolution_checker.py.

Verifies ResolutionChecker can be constructed with mocks, that it
correctly resolves predictions and trades, and that the pure static
helpers (compute_outcome, compute_paper_pnl) behave correctly.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from trading.resolution_checker import ResolutionChecker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coro in a fresh event loop.

    Python 3.10+ asyncio.run() closes and discards the loop without restoring
    the thread's current-loop reference. Subsequent tests that use
    asyncio.get_event_loop() raise RuntimeError. Mirrors the helper in
    test_kalshi_dispatcher.py.
    """
    try:
        prev = asyncio.get_event_loop()
    except RuntimeError:
        prev = None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(prev)

def _make_checker(
    *,
    db_conn=None,
    sqlite_ledger=None,
    feature_computer=None,
    calibrators=None,
    pending_queue=None,
) -> ResolutionChecker:
    return ResolutionChecker(
        db_conn=db_conn or MagicMock(),
        sqlite_ledger=sqlite_ledger or MagicMock(),
        feature_computer=feature_computer or MagicMock(),
        calibrators=calibrators or MagicMock(),
        pending_queue=pending_queue or MagicMock(),
    )


def _make_pending_entry(
    *,
    prediction_id="pred_btc_300e",
    symbol="BTCUSDT",
    market_window_seconds=300,
    ts_resolve_at_ms=1_700_000_300_000,
    price_at_open=100_000.0,
):
    entry = MagicMock()
    entry.prediction_id = prediction_id
    entry.symbol = symbol
    entry.market_window_seconds = market_window_seconds
    entry.ts_resolve_at_ms = ts_resolve_at_ms
    entry.price_at_open = price_at_open
    return entry


# ---------------------------------------------------------------------------
# 1. Instantiation
# ---------------------------------------------------------------------------

class TestResolutionCheckerInstantiation:
    def test_resolution_checker_instantiation(self):
        checker = _make_checker()
        assert checker is not None
        assert hasattr(checker, "check_predictions")
        assert hasattr(checker, "check_trades")
        assert hasattr(checker, "direction_for")
        assert hasattr(checker, "compute_outcome")
        assert hasattr(checker, "compute_paper_pnl")


# ---------------------------------------------------------------------------
# 2. check_predictions — outcome recorded
# ---------------------------------------------------------------------------

class TestCheckPredictions:
    def _make_db_conn(self, *, warmup=0, direction="up", has_prediction=True):
        db_conn = MagicMock()

        def execute_side_effect(sql, params=None):
            cursor = MagicMock()
            if "FROM predictions WHERE prediction_id" in sql and "pred_direction" not in sql and "pred_proba_raw" not in sql:
                # The existence check: SELECT 1 FROM predictions WHERE prediction_id = ?
                cursor.fetchone.return_value = (1,) if has_prediction else None
            elif "pred_proba_raw" in sql:
                # calibration row fetch
                row = MagicMock()
                row.__getitem__ = lambda self, key: {
                    "pred_proba_raw": 0.6,
                    "warmup": warmup,
                    "model_name": "h300",
                    "symbol": "BTCUSDT",
                    "market_window_seconds": 300,
                }[key]
                row.__bool__ = lambda self: True
                cursor.fetchone.return_value = row
            elif "pred_direction" in sql:
                row = MagicMock()
                row.__getitem__ = lambda self, key: direction if key == "pred_direction" else None
                cursor.fetchone.return_value = row
            else:
                cursor.fetchone.return_value = None
            return cursor

        db_conn.execute = execute_side_effect
        return db_conn

    def test_check_predictions_records_outcome(self):
        """A ripe pending entry should produce a record_resolution call."""
        entry = _make_pending_entry(market_window_seconds=300)

        pending_queue = MagicMock()
        pending_queue.iter_ripe.return_value = [entry]

        feature_computer = MagicMock()
        feature_computer.price_at.return_value = 100_500.0  # price went up

        sqlite_ledger = MagicMock()
        calibrators = MagicMock()
        cal_mock = MagicMock()
        calibrators.get.return_value = cal_mock

        db_conn = self._make_db_conn(warmup=0, direction="up")

        checker = _make_checker(
            db_conn=db_conn,
            sqlite_ledger=sqlite_ledger,
            feature_computer=feature_computer,
            calibrators=calibrators,
            pending_queue=pending_queue,
        )

        _run(checker.check_predictions(1_700_000_600_000))

        sqlite_ledger.record_resolution.assert_called_once()
        call_kwargs = sqlite_ledger.record_resolution.call_args.kwargs
        assert call_kwargs["prediction_id"] == "pred_btc_300e"
        assert call_kwargs["contract_result"] == "up"
        assert call_kwargs["prediction_correct"] is True

    def test_check_predictions_feed_calibrator_only_for_300s(self):
        """calibrators.get() must be called for 300s entries, NOT for 900s/1800s."""
        entry_300 = _make_pending_entry(
            prediction_id="pred_btc_300e", market_window_seconds=300
        )
        entry_900 = _make_pending_entry(
            prediction_id="pred_btc_900e", market_window_seconds=900
        )
        entry_1800 = _make_pending_entry(
            prediction_id="pred_btc_1800e", market_window_seconds=1800
        )

        pending_queue = MagicMock()
        pending_queue.iter_ripe.return_value = [entry_300, entry_900, entry_1800]

        feature_computer = MagicMock()
        feature_computer.price_at.return_value = 100_500.0

        sqlite_ledger = MagicMock()
        calibrators = MagicMock()
        cal_mock = MagicMock()
        calibrators.get.return_value = cal_mock

        db_conn = self._make_db_conn(warmup=0, direction="up")

        checker = _make_checker(
            db_conn=db_conn,
            sqlite_ledger=sqlite_ledger,
            feature_computer=feature_computer,
            calibrators=calibrators,
            pending_queue=pending_queue,
        )

        _run(checker.check_predictions(1_700_000_600_000))

        # calibrators.get() should only be called once (for 300s entry)
        calibrators.get.assert_called_once()
        cal_mock.record_outcome.assert_called_once()
        # 3 predictions resolved, 3 record_resolution calls
        assert sqlite_ledger.record_resolution.call_count == 3

    def test_check_predictions_no_ripe_entries_is_noop(self):
        """When iter_ripe returns empty list, nothing should be called."""
        pending_queue = MagicMock()
        pending_queue.iter_ripe.return_value = []
        sqlite_ledger = MagicMock()

        checker = _make_checker(
            pending_queue=pending_queue,
            sqlite_ledger=sqlite_ledger,
        )

        _run(checker.check_predictions(1_700_000_600_000))

        sqlite_ledger.record_resolution.assert_not_called()

    def test_check_predictions_skips_missing_prediction_row(self):
        """If prediction is not in DB, entry is removed and resolution is NOT written."""
        entry = _make_pending_entry()

        pending_queue = MagicMock()
        pending_queue.iter_ripe.return_value = [entry]

        feature_computer = MagicMock()
        feature_computer.price_at.return_value = 100_500.0

        sqlite_ledger = MagicMock()

        db_conn = self._make_db_conn(has_prediction=False)

        checker = _make_checker(
            db_conn=db_conn,
            sqlite_ledger=sqlite_ledger,
            feature_computer=feature_computer,
            pending_queue=pending_queue,
        )

        _run(checker.check_predictions(1_700_000_600_000))

        sqlite_ledger.record_resolution.assert_not_called()
        pending_queue.remove.assert_called_once_with("pred_btc_300e")


# ---------------------------------------------------------------------------
# 3. check_trades — resolution row written
# ---------------------------------------------------------------------------

class TestCheckTrades:
    def _make_trade_row(
        self,
        *,
        trade_id="trade_001",
        prediction_id="pred_btc_300e",
        symbol="BTCUSDT",
        ts_resolve_at_ms=1_700_000_300_000,
        pred_proba_calibrated=0.65,
        pred_direction="up",
        simulated_stake_usdc=10.0,
        market_window_seconds=300,
    ):
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "trade_id": trade_id,
            "prediction_id": prediction_id,
            "symbol": symbol,
            "ts_resolve_at_ms": ts_resolve_at_ms,
            "pred_proba_calibrated": pred_proba_calibrated,
            "pred_direction": pred_direction,
            "simulated_stake_usdc": simulated_stake_usdc,
            "market_window_seconds": market_window_seconds,
        }[key]
        return row

    def test_check_trades_writes_resolution_row(self):
        """A ripe open trade should produce a record_trade_resolution call."""
        trade_row = self._make_trade_row(pred_direction="up")

        db_conn = MagicMock()
        trades_cursor = MagicMock()
        trades_cursor.fetchall.return_value = [trade_row]

        pred_cursor = MagicMock()
        pred_row = MagicMock()
        pred_row.__getitem__ = lambda self, key: 100_000.0 if key == "price_at_open" else None
        pred_cursor.fetchone.return_value = pred_row

        call_count = [0]

        def execute_side_effect(sql, params=None):
            call_count[0] += 1
            if "paper_trades" in sql:
                return trades_cursor
            elif "price_at_open" in sql:
                return pred_cursor
            return MagicMock()

        db_conn.execute = execute_side_effect

        feature_computer = MagicMock()
        feature_computer.price_at.return_value = 100_500.0  # price rose → "up" wins

        sqlite_ledger = MagicMock()

        checker = _make_checker(
            db_conn=db_conn,
            feature_computer=feature_computer,
            sqlite_ledger=sqlite_ledger,
        )

        _run(checker.check_trades(1_700_000_600_000))

        sqlite_ledger.record_trade_resolution.assert_called_once()
        call_kwargs = sqlite_ledger.record_trade_resolution.call_args.kwargs
        assert call_kwargs["trade_id"] == "trade_001"
        assert call_kwargs["trade_result"] == "win"
        assert call_kwargs["pnl_method"] == "binary_polymarket"
        assert call_kwargs["gross_pnl"] > 0


# ---------------------------------------------------------------------------
# 4. compute_outcome (static)
# ---------------------------------------------------------------------------

class TestComputeOutcome:
    def test_compute_outcome_up_when_price_rises(self):
        result, correct = ResolutionChecker.compute_outcome("up", 100.0, 101.0)
        assert result == "up"
        assert correct is True

    def test_compute_outcome_down_when_price_falls(self):
        result, correct = ResolutionChecker.compute_outcome("down", 100.0, 99.0)
        assert result == "down"
        assert correct is True

    def test_compute_outcome_flat_when_price_unchanged(self):
        result, correct = ResolutionChecker.compute_outcome("up", 100.0, 100.0)
        assert result == "flat"
        assert correct is False

    def test_compute_outcome_correct_when_direction_matches_result(self):
        """Direction 'down' + price falling = correct."""
        result, correct = ResolutionChecker.compute_outcome("down", 50000.0, 49000.0)
        assert result == "down"
        assert correct is True

    def test_compute_outcome_incorrect_when_direction_wrong(self):
        result, correct = ResolutionChecker.compute_outcome("up", 100.0, 99.0)
        assert result == "down"
        assert correct is False


# ---------------------------------------------------------------------------
# 5. compute_paper_pnl (static)
# ---------------------------------------------------------------------------

class TestComputePaperPnl:
    def test_compute_paper_pnl_winning_trade_positive_gross(self):
        gross, fee, net, result, correct = ResolutionChecker.compute_paper_pnl(
            direction="up",
            calibrated_p=0.65,
            stake=10.0,
            price_open=100.0,
            price_close=101.0,
        )
        assert correct is True
        assert gross > 0
        assert fee >= 0
        assert net == pytest.approx(gross - fee)

    def test_compute_paper_pnl_losing_trade_negative_gross(self):
        gross, fee, net, result, correct = ResolutionChecker.compute_paper_pnl(
            direction="up",
            calibrated_p=0.65,
            stake=10.0,
            price_open=100.0,
            price_close=99.0,
        )
        assert correct is False
        assert gross == -10.0
        assert net < 0

    def test_compute_paper_pnl_fee_coef(self):
        """Fee = 0.072 * p * (1-p) * stake."""
        p = 0.7
        stake = 10.0
        _, fee, _, _, _ = ResolutionChecker.compute_paper_pnl(
            direction="up",
            calibrated_p=p,
            stake=stake,
            price_open=100.0,
            price_close=101.0,
        )
        expected_fee = 0.072 * p * (1 - p) * stake
        assert fee == pytest.approx(expected_fee)

    def test_compute_paper_pnl_zero_calibrated_p_no_crash(self):
        """calibrated_p=0 should not raise ZeroDivisionError."""
        gross, fee, net, result, correct = ResolutionChecker.compute_paper_pnl(
            direction="up",
            calibrated_p=0.0,
            stake=10.0,
            price_open=100.0,
            price_close=101.0,
        )
        assert gross == 0


# ---------------------------------------------------------------------------
# 6. direction_for
# ---------------------------------------------------------------------------

class TestDirectionFor:
    def test_direction_for_returns_up_when_row_missing(self):
        """Default behavior: returns 'up' when prediction_id not found."""
        db_conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        db_conn.execute.return_value = cursor

        checker = _make_checker(db_conn=db_conn)
        result = checker.direction_for("nonexistent_id")
        assert result == "up"

    def test_direction_for_returns_stored_direction(self):
        """Returns the stored pred_direction from DB."""
        db_conn = MagicMock()
        cursor = MagicMock()
        row = MagicMock()
        row.__getitem__ = lambda self, key: "down" if key == "pred_direction" else None
        cursor.fetchone.return_value = row
        db_conn.execute.return_value = cursor

        checker = _make_checker(db_conn=db_conn)
        result = checker.direction_for("pred_btc_300e")
        assert result == "down"
