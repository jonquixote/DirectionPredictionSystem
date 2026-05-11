"""
Tests for LogWriter.
Spec v2.6, Section 14.
"""

import sys
import os
import sqlite3
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trade_logging.writer import LogWriter


@pytest.fixture
def db_path():
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def _make_record(**overrides):
    """Create a minimal valid candidate_trades record."""
    record = {
        "timestamp_ms": 1700000000000,
        "contract_id": "test_contract",
        "market_open_time": "2024-01-01T00:00:00",
        "seconds_to_resolution": 300.0,
        "executed": 1,
    }
    record.update(overrides)
    return record


class TestLogWriter:

    def test_all_candidate_trades_written(self, db_path):
        """LogWriter: all candidate trades written (executed and suppressed)."""
        writer = LogWriter(db_path, batch_size=10)
        # Both records must have the same keys for batch insert
        writer.enqueue(_make_record(executed=1, contract_id="exec_1",
                                    suppression_reason=None))
        writer.enqueue(_make_record(executed=0, contract_id="supp_1",
                                    suppression_reason="test"))
        writer.shutdown()

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM candidate_trades").fetchone()[0]
        conn.close()
        assert count == 2

    def test_ne_t_logged_even_when_gate_fails(self, db_path):
        """LogWriter: ne_t_computed logged even when NE_t gate fails."""
        writer = LogWriter(db_path, batch_size=10)
        writer.enqueue(_make_record(
            executed=0,
            ne_t_computed=-0.05,
            suppression_reason="NE_t=-0.05",
        ))
        writer.shutdown()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT ne_t_computed FROM candidate_trades WHERE executed = 0"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == pytest.approx(-0.05)

    def test_resolution_fields_null_until_resolved(self, db_path):
        """LogWriter: resolution fields NULL until contract resolved."""
        writer = LogWriter(db_path, batch_size=10)
        writer.enqueue(_make_record())
        writer.shutdown()

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT resolved_direction, model_correct, ne_t_realised "
            "FROM candidate_trades"
        ).fetchone()
        conn.close()
        assert row[0] is None  # resolved_direction
        assert row[1] is None  # model_correct
        assert row[2] is None  # ne_t_realised

    def test_shutdown_flushes_queue(self, db_path):
        """LogWriter: shutdown() flushes queue before closing connection."""
        writer = LogWriter(db_path, batch_size=1000)  # Large batch = no auto-flush
        for i in range(5):
            writer.enqueue(_make_record(contract_id=f"c_{i}"))
        writer.shutdown()

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM candidate_trades").fetchone()[0]
        conn.close()
        assert count == 5

    def test_concurrent_enqueue_thread_safe(self, db_path):
        """LogWriter: concurrent enqueue() calls are thread-safe."""
        writer = LogWriter(db_path, batch_size=10)
        n_threads = 10
        n_per_thread = 20

        def enqueue_batch(thread_id):
            for i in range(n_per_thread):
                writer.enqueue(_make_record(contract_id=f"t{thread_id}_c{i}"))

        threads = [
            threading.Thread(target=enqueue_batch, args=(t,))
            for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        writer.shutdown()

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM candidate_trades").fetchone()[0]
        conn.close()
        assert count == n_threads * n_per_thread

    def test_all_three_indices_exist(self, db_path):
        """
        LogWriter: all three indices exist after init.
        Verifies executescript() not execute(); execute() silently
        drops every statement after the first semicolon.
        """
        writer = LogWriter(db_path, batch_size=10)

        conn = sqlite3.connect(db_path)
        indices = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = [row[0] for row in indices]
        conn.close()

        writer.shutdown()

        assert "idx_executed" in index_names
        assert "idx_suppression" in index_names
        assert "idx_resolved" in index_names
