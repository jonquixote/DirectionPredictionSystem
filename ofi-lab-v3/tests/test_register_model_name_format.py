"""Bug C regression: model_name must include _YYYYMMDD suffix from train_window_end.

Without the suffix, INSERT OR REPLACE in register_model() would clobber an old
fleet's row whenever a new fleet shared (horizon, symbol, train_days).
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.register_model import build_model_name, register_model
from storage.db import open_database, init_schema


def test_build_model_name_includes_train_end_suffix():
    """Canonical format: h{horizon}_{sym}_v3_{train_days}d_{YYYYMMDD}."""
    name = build_model_name(
        horizon=60,
        symbol="BTCUSDT",
        train_days=90,
        train_window_end="2026-05-19",
    )
    assert name == "h60_btc_v3_90d_20260519"


def test_build_model_name_strips_usdt_and_lowercases():
    assert build_model_name(
        horizon=300, symbol="ETHUSDT", train_days=180, train_window_end="2026-04-27",
    ) == "h300_eth_v3_180d_20260427"


def test_build_model_name_different_train_end_yields_different_name():
    """Two fleets with the SAME (horizon, symbol, train_days) but DIFFERENT
    train_window_end must produce DIFFERENT names — that's the entire point
    of the Bug C fix."""
    old = build_model_name(
        horizon=60, symbol="BTCUSDT", train_days=90, train_window_end="2026-04-27",
    )
    new = build_model_name(
        horizon=60, symbol="BTCUSDT", train_days=90, train_window_end="2026-05-19",
    )
    assert old != new
    assert old == "h60_btc_v3_90d_20260427"
    assert new == "h60_btc_v3_90d_20260519"


@pytest.fixture
def db_conn():
    conn = open_database(":memory:")
    init_schema(conn)
    return conn


def test_register_model_uses_dated_name(db_conn, tmp_path):
    """register_model() writes a row with the dated name; no collision with a
    differently-dated fleet for the same (horizon, symbol, train_days)."""
    def _make_artifact(train_window_end: str, hash_marker: bytes) -> str:
        out = tmp_path / f"art_{train_window_end}"
        out.mkdir()
        (out / "model.lgb").write_bytes(b"LGB_DATA_" + hash_marker)
        (out / "feature_names.json").write_text(json.dumps(["mlofi"]))
        (out / "metrics.json").write_text(json.dumps({
            "horizon_seconds": 60,
            "symbol": "BTCUSDT",
            "train_days": 90,
            "train_window_start": "2025-01-01",
            "train_window_end": train_window_end,
            "feature_version": "v3",
        }))
        return str(out)

    name_old = register_model(
        conn=db_conn,
        artifact_dir=_make_artifact("2026-04-27", b"old"),
        evaluation_windows=[300, 900, 1800],
    )
    name_new = register_model(
        conn=db_conn,
        artifact_dir=_make_artifact("2026-05-19", b"new"),
        evaluation_windows=[300, 900, 1800],
    )
    assert name_old == "h60_btc_v3_90d_20260427"
    assert name_new == "h60_btc_v3_90d_20260519"
    # Both rows must coexist — no INSERT OR REPLACE collision.
    rows = db_conn.execute(
        "SELECT name FROM model_registry WHERE training_horizon_seconds = 60 AND symbol = 'BTCUSDT'"
    ).fetchall()
    assert sorted(r["name"] for r in rows) == [name_old, name_new]
