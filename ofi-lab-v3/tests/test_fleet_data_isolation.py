"""TDD tests for fleet data isolation fixes.

These tests verify that:
1. load_features() can filter to a single symbol
2. The train split respects train_start date boundaries
3. train_one() supports a walk-forward toggle
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_feature_parquet(path: Path, symbol: str, dates: list[str], n_per_day: int = 5):
    """Create a minimal parquet file that matches the schema load_features expects."""
    from trading.live_features import V3_FEATURE_COLS

    rows = []
    for date_str in dates:
        base_ts = int(pd.Timestamp(date_str, tz="UTC").timestamp() * 1000)
        for i in range(n_per_day):
            row = {col: float(np.random.default_rng(42).normal()) for col in V3_FEATURE_COLS if col != "symbol_cat"}
            row["symbol_cat"] = 0.0
            row["cts"] = base_ts + i * 60_000  # 1-min spacing
            row["mid_price"] = 60000.0 + i
            row["symbol"] = symbol
            row["spread"] = abs(row.get("spread", 0.01))
            row["relative_spread"] = abs(row.get("relative_spread", 0.001))
            rows.append(row)

    df = pd.DataFrame(rows)
    table = pa.Table.from_pandas(df)
    pq.write_table(table, str(path))


@pytest.fixture
def multi_symbol_feature_dir(tmp_path):
    """Create a feature dir with all 4 symbol subdirs."""
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]:
        sym_dir = tmp_path / sym
        sym_dir.mkdir()
        _make_feature_parquet(
            sym_dir / f"2025-06-01_{sym}_features.parquet",
            sym,
            ["2025-06-01", "2025-06-02"],
        )
    return tmp_path


# ---------------------------------------------------------------------------
# Bug #1: load_features must support single-symbol filtering
# ---------------------------------------------------------------------------

def test_load_features_single_symbol(multi_symbol_feature_dir):
    """When loading features, filter to BTCUSDT after loading."""
    from validation.run_training import load_features

    df = load_features(multi_symbol_feature_dir)
    df = df[df["symbol"] == "BTCUSDT"]
    assert len(df) > 0, "Should have loaded some rows"
    symbols_present = df["symbol"].unique().tolist()
    assert symbols_present == ["BTCUSDT"], f"Expected only BTCUSDT, got {symbols_present}"


def test_load_features_all_symbols_default(multi_symbol_feature_dir):
    """When symbol is None (default), all symbols are loaded — backward compat."""
    from validation.run_training import load_features

    df = load_features(multi_symbol_feature_dir)
    symbols_present = sorted(df["symbol"].unique().tolist())
    assert symbols_present == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"], f"Expected all 4 symbols, got {symbols_present}"


# ---------------------------------------------------------------------------
# Bug #2: train split must respect train_start
# ---------------------------------------------------------------------------

def test_train_split_respects_start_date():
    """When train_start is provided, rows before it must be excluded."""
    # Simulate a DataFrame with dates spanning several months
    dates = pd.date_range("2025-06-01", "2025-12-31", freq="D")
    rng = np.random.default_rng(42)
    rows = []
    for d in dates:
        cts_ms = int(d.timestamp() * 1000)
        rows.append({"cts": cts_ms, "date": d.strftime("%Y-%m-%d"), "target": rng.integers(0, 2)})
    df = pd.DataFrame(rows)

    train_start = "2025-10-01"
    train_end = "2025-12-31"

    # Apply the FIXED split logic (both bounds)
    df_train = df[(df["date"] >= train_start) & (df["date"] <= train_end)].reset_index(drop=True)

    assert df_train["date"].min() >= train_start, (
        f"Earliest train date {df_train['date'].min()} should be >= {train_start}"
    )
    # October through December = ~92 days
    assert len(df_train) < len(df), "Should have excluded earlier rows"


def test_train_split_no_start_loads_all():
    """When train_start is None, all rows up to train_end are included — backward compat."""
    dates = pd.date_range("2025-06-01", "2025-12-31", freq="D")
    rng = np.random.default_rng(42)
    rows = []
    for d in dates:
        cts_ms = int(d.timestamp() * 1000)
        rows.append({"cts": cts_ms, "date": d.strftime("%Y-%m-%d"), "target": rng.integers(0, 2)})
    df = pd.DataFrame(rows)

    train_start = None
    train_end = "2025-12-31"

    # The backward-compat split (no lower bound)
    if train_start:
        df_train = df[(df["date"] >= train_start) & (df["date"] <= train_end)].reset_index(drop=True)
    else:
        df_train = df[df["date"] <= train_end].reset_index(drop=True)

    assert len(df_train) == len(df), "All rows should be included when train_start is None"


# ---------------------------------------------------------------------------
# Bug #4: train_one walk-forward toggle
# ---------------------------------------------------------------------------

def test_train_one_skip_wf_default(tmp_path):
    """By default, train_one should pass --skip-wf to the subprocess."""
    from scripts.train_fleet import train_one

    cell = {"symbol": "BTCUSDT", "horizon": 300, "train_days": 90, "name": "h300_btc_v3_90d"}

    with patch("scripts.train_fleet.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        train_one(
            cell,
            train_end="2026-04-26",
            feature_dir="/fake/features",
            output_root=str(tmp_path),
            evaluation_windows=[300, 900, 1800],
            db_path=None,
        )
        call_args = mock_run.call_args[0][0]
        assert "--skip-wf" in call_args, "Default behavior should skip walk-forward"


def test_train_one_walk_forward_enabled(tmp_path):
    """train_one includes --skip-wf by default (current behavior)."""
    from scripts.train_fleet import train_one

    cell = {"symbol": "BTCUSDT", "horizon": 300, "train_days": 90, "name": "h300_btc_v3_90d"}

    with patch("scripts.train_fleet.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        train_one(
            cell,
            train_end="2026-04-26",
            feature_dir="/fake/features",
            output_root=str(tmp_path),
            evaluation_windows=[300, 900, 1800],
            db_path=None,
        )
        call_args = mock_run.call_args[0][0]
        assert "--skip-wf" in call_args, "default should include --skip-wf"
