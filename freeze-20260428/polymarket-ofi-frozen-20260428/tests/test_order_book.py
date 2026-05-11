"""
Tests for order book managers (Binance legacy, Bybit, Polymarket).
Spec v2.6, Section 14.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.binance import BinanceOrderBookManager
from api.bybit import BybitOrderBookManager
from api.polymarket import parse_book


class TestBinanceOrderBookManager:

    def test_keys_stored_as_float(self):
        """BinanceOrderBookManager: keys stored as float after apply_update."""
        manager = BinanceOrderBookManager("BTCUSDT", levels=5)
        event = {
            "b": [["50000.00", "1.5"], ["49999.00", "0.5"]],
            "a": [["50001.00", "2.0"], ["50002.00", "0.3"]],
            "u": 100,
        }
        manager.apply_update(event)

        for key in manager.bids:
            assert isinstance(key, float), f"Bid key {key} is {type(key)}, expected float"
        for key in manager.asks:
            assert isinstance(key, float), f"Ask key {key} is {type(key)}, expected float"

    def test_gap_detection_triggers_resync(self):
        """BinanceOrderBookManager: gap detection triggers resync signal."""
        manager = BinanceOrderBookManager("BTCUSDT")
        manager.last_update_id = 100

        # Event with gap: U > last_update_id + 1
        event_gap = {"U": 105, "u": 110, "b": [], "a": []}
        assert manager.detect_gap(event_gap) is True

        # Event without gap: U = last_update_id + 1
        event_no_gap = {"U": 101, "u": 105, "b": [], "a": []}
        assert manager.detect_gap(event_no_gap) is False

    def test_apply_update_removes_zero_qty(self):
        """BinanceOrderBookManager: apply_update removes zero-qty levels."""
        manager = BinanceOrderBookManager("BTCUSDT")

        # First add some levels
        event_add = {
            "b": [["50000.00", "1.5"], ["49999.00", "0.5"]],
            "a": [["50001.00", "2.0"]],
            "u": 100,
        }
        manager.apply_update(event_add)
        assert 50000.0 in manager.bids
        assert 50001.0 in manager.asks

        # Remove via zero qty
        event_remove = {
            "b": [["50000.00", "0.0"]],
            "a": [["50001.00", "0.0"]],
            "u": 101,
        }
        manager.apply_update(event_remove)
        assert 50000.0 not in manager.bids
        assert 50001.0 not in manager.asks

    def test_get_top_levels_sorted(self):
        """BinanceOrderBookManager: get_top_levels returns sorted results."""
        manager = BinanceOrderBookManager("BTCUSDT", levels=3)
        event = {
            "b": [["50000", "1"], ["49998", "2"], ["49999", "3"], ["49997", "4"]],
            "a": [["50001", "1"], ["50003", "2"], ["50002", "3"], ["50004", "4"]],
            "u": 100,
        }
        manager.apply_update(event)
        bids, asks = manager.get_top_levels()

        # Bids descending
        assert len(bids) == 3
        assert bids[0][0] > bids[1][0] > bids[2][0]

        # Asks ascending
        assert len(asks) == 3
        assert asks[0][0] < asks[1][0] < asks[2][0]


class TestPolymarketParseBook:

    def test_handles_empty_book(self):
        """Polymarket parse_book: handles empty book gracefully (returns None)."""
        assert parse_book({"asks": [], "bids": []}) is None
        assert parse_book({"asks": [{"price": "0.5", "size": "10"}], "bids": []}) is None
        assert parse_book({"asks": [], "bids": [{"price": "0.4", "size": "10"}]}) is None
        assert parse_book({}) is None

    def test_parses_normal_book(self):
        """parse_book: correctly parses a normal book."""
        book = {
            "asks": [
                {"price": "0.55", "size": "100"},
                {"price": "0.60", "size": "50"},
            ],
            "bids": [
                {"price": "0.45", "size": "200"},
                {"price": "0.40", "size": "80"},
            ],
        }
        result = parse_book(book)
        assert result is not None
        assert result["best_ask_price"] == 0.55
        assert result["best_bid_price"] == 0.45
        assert result["best_ask_size"] == 100.0
        assert result["best_bid_size"] == 200.0
        assert result["mid"] == pytest.approx(0.50)
        assert result["spread_bps"] > 0


class TestBybitOrderBookManager:

    def test_snapshot_replaces_book(self):
        """BybitOrderBookManager: snapshot replaces entire book."""
        mgr = BybitOrderBookManager(symbols=["BTCUSDT"], levels=5)
        # Apply initial snapshot
        mgr.apply_snapshot("BTCUSDT", {
            "b": [["50000", "1.5"], ["49999", "0.5"]],
            "a": [["50001", "2.0"], ["50002", "0.3"]],
            "u": 100,
        })
        assert 50000.0 in mgr.books["BTCUSDT"]["bids"]
        assert 50001.0 in mgr.books["BTCUSDT"]["asks"]

        # New snapshot replaces entirely
        mgr.apply_snapshot("BTCUSDT", {
            "b": [["60000", "3.0"]],
            "a": [["60001", "4.0"]],
            "u": 200,
        })
        assert 50000.0 not in mgr.books["BTCUSDT"]["bids"]
        assert 60000.0 in mgr.books["BTCUSDT"]["bids"]
        assert mgr.books["BTCUSDT"]["last_u"] == 200

    def test_delta_insert_update_delete(self):
        """BybitOrderBookManager: delta protocol — size>0 upsert, size==0 delete."""
        mgr = BybitOrderBookManager(symbols=["ETHUSDT"], levels=10)
        mgr.apply_snapshot("ETHUSDT", {
            "b": [["3000", "5.0"]],
            "a": [["3001", "8.0"]],
            "u": 1,
        })

        # Delta: update existing, add new, delete one
        mgr.apply_delta("ETHUSDT", {
            "b": [["3000", "7.0"], ["2999", "2.0"]],  # update + insert
            "a": [["3001", "0"]],                       # delete
            "u": 2,
        })
        assert mgr.books["ETHUSDT"]["bids"][3000.0] == 7.0  # updated
        assert mgr.books["ETHUSDT"]["bids"][2999.0] == 2.0  # inserted
        assert 3001.0 not in mgr.books["ETHUSDT"]["asks"]   # deleted

    def test_keys_stored_as_float(self):
        """BybitOrderBookManager: keys normalised to float at insert."""
        mgr = BybitOrderBookManager(symbols=["SOLUSDT"])
        mgr.apply_snapshot("SOLUSDT", {
            "b": [["150.50", "10"]], "a": [["151.00", "5"]], "u": 1,
        })
        for key in mgr.books["SOLUSDT"]["bids"]:
            assert isinstance(key, float)
        for key in mgr.books["SOLUSDT"]["asks"]:
            assert isinstance(key, float)

    def test_gap_detection(self):
        """BybitOrderBookManager: gap detection triggers resync signal."""
        mgr = BybitOrderBookManager(symbols=["BTCUSDT"])
        mgr.books["BTCUSDT"]["last_u"] = 100

        # Gap: update_id > last_u + 1
        assert mgr.detect_gap("BTCUSDT", 105) is True
        # No gap: sequential
        assert mgr.detect_gap("BTCUSDT", 101) is False

    def test_restart_handling_u1(self):
        """BybitOrderBookManager: u==1 service restart discards book."""
        mgr = BybitOrderBookManager(symbols=["BTCUSDT"])
        mgr.apply_snapshot("BTCUSDT", {
            "b": [["50000", "1"]], "a": [["50001", "1"]], "u": 100,
        })
        assert mgr.books["BTCUSDT"]["synced"] is True

        # u == 1 restart
        restart = mgr.handle_restart("BTCUSDT", 1)
        assert restart is True
        assert mgr.books["BTCUSDT"]["synced"] is False
        assert len(mgr.books["BTCUSDT"]["bids"]) == 0
        assert len(mgr.books["BTCUSDT"]["asks"]) == 0

    def test_get_top_levels_sorted(self):
        """BybitOrderBookManager: top levels returned sorted correctly."""
        mgr = BybitOrderBookManager(symbols=["BTCUSDT"], levels=3)
        mgr.apply_snapshot("BTCUSDT", {
            "b": [["50000", "1"], ["49998", "2"], ["49999", "3"], ["49997", "4"]],
            "a": [["50001", "1"], ["50003", "2"], ["50002", "3"], ["50004", "4"]],
            "u": 1,
        })
        bids, asks = mgr.get_top_levels("BTCUSDT")

        assert len(bids) == 3
        assert bids[0][0] > bids[1][0] > bids[2][0]  # descending
        assert len(asks) == 3
        assert asks[0][0] < asks[1][0] < asks[2][0]  # ascending

    def test_process_message_snapshot_then_delta(self):
        """BybitOrderBookManager: process_message handles full flow."""
        mgr = BybitOrderBookManager(symbols=["XRPUSDT"])

        # Snapshot message
        mgr.process_message({
            "topic": "orderbook.200.XRPUSDT",
            "type": "snapshot",
            "ts": 1000,
            "cts": 1000,
            "data": {
                "s": "XRPUSDT",
                "b": [["0.50", "1000"]],
                "a": [["0.51", "500"]],
                "u": 1,
                "seq": 1,
            },
        })
        assert mgr.books["XRPUSDT"]["synced"] is True
        assert 0.50 in mgr.books["XRPUSDT"]["bids"]

        # Delta message (sequential u)
        mgr.process_message({
            "topic": "orderbook.200.XRPUSDT",
            "type": "delta",
            "ts": 1001,
            "cts": 1001,
            "data": {
                "s": "XRPUSDT",
                "b": [["0.49", "200"]],
                "a": [["0.51", "0"]],  # delete
                "u": 2,
                "seq": 2,
            },
        })
        assert 0.49 in mgr.books["XRPUSDT"]["bids"]
        assert 0.51 not in mgr.books["XRPUSDT"]["asks"]

    def test_process_message_skips_delta_before_snapshot(self):
        """BybitOrderBookManager: deltas before first snapshot are skipped."""
        mgr = BybitOrderBookManager(symbols=["BTCUSDT"])

        # Delta without prior snapshot — should be skipped
        mgr.process_message({
            "topic": "orderbook.200.BTCUSDT",
            "type": "delta",
            "ts": 1000,
            "cts": 1000,
            "data": {
                "s": "BTCUSDT",
                "b": [["50000", "1"]],
                "a": [],
                "u": 5,
                "seq": 5,
            },
        })
        assert len(mgr.books["BTCUSDT"]["bids"]) == 0  # not applied
        assert mgr.books["BTCUSDT"]["synced"] is False

