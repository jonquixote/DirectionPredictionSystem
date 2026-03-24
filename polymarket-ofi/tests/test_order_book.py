"""
Tests for BinanceOrderBookManager and Polymarket parse_book.
Spec v2.6, Section 14.
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from api.binance import BinanceOrderBookManager
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
