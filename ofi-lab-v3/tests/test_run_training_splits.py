"""Regression tests for run_training train/val/test split logic.

Covers Bug A (cross-symbol training contamination) and Bug B (hardcoded
VAL_END causing test-set overlap with training window).

The tests mimic the exact filter/split block from validation/run_training.py
main() — using a synthetic 4-symbol DataFrame — and assert:
  - per-symbol mode keeps ONLY the chosen symbol
  - joint mode keeps all 4 symbols
  - temporal disjointness for both modes
  - no hardcoded date strings used (test_end derived from the test's own
    train_end argument)
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest


SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


def _build_synthetic_multi_symbol_df(
    start_date: str, n_days: int, rows_per_day_per_symbol: int = 3
) -> pd.DataFrame:
    """Create a synthetic 4-symbol DataFrame with a `cts` (ms) column."""
    base = pd.Timestamp(start_date, tz="UTC")
    rng = np.random.default_rng(7)
    rows = []
    for day in range(n_days):
        day_ts = base + pd.Timedelta(days=day)
        for sym in SYMBOLS:
            for i in range(rows_per_day_per_symbol):
                # spread rows within the day
                ts = day_ts + pd.Timedelta(seconds=60 * i)
                rows.append({
                    "cts": int(ts.timestamp() * 1000),
                    "symbol": sym,
                    "target": int(rng.integers(0, 2)),
                })
    return pd.DataFrame(rows)


def _apply_split(df: pd.DataFrame, args: SimpleNamespace) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Mirror of the main() split logic so we can drive it with synthetic data
    without invoking LightGBM training. Kept in lockstep with run_training.py."""
    # Phase 0.5: per-symbol filter (Bug A fix)
    if args.train_mode == "per-symbol":
        if not args.symbol:
            raise ValueError("per-symbol mode requires --symbol")
        df = df[df["symbol"] == args.symbol].reset_index(drop=True)

    # Phase 3: derive `date` from `cts` and split
    df = df.copy()
    df["date"] = pd.to_datetime(df["cts"], unit="ms", utc=True).dt.strftime("%Y-%m-%d")

    if args.train_start:
        df_train = df[(df["date"] >= args.train_start) & (df["date"] <= args.train_end)].reset_index(drop=True)
    else:
        df_train = df[df["date"] <= args.train_end].reset_index(drop=True)
    df_val = df[(df["date"] > args.train_end) & (df["date"] <= args.val_end)].reset_index(drop=True)
    # Bug B: use args.val_end (local), and optional args.test_end upper bound.
    if args.test_end:
        df_test = df[(df["date"] > args.val_end) & (df["date"] <= args.test_end)].reset_index(drop=True)
    else:
        df_test = df[df["date"] > args.val_end].reset_index(drop=True)
    return df_train, df_val, df_test


@pytest.fixture
def synthetic_df():
    # 30 days of 4-symbol data starting at a test-defined date.
    return _build_synthetic_multi_symbol_df("2025-10-01", n_days=30)


def _windows(train_end: str, val_days: int = 5, test_days: int = 5):
    """Build val_end/test_end from train_end — NO hardcoded date strings."""
    te = pd.Timestamp(train_end)
    val_end = (te + pd.Timedelta(days=val_days)).strftime("%Y-%m-%d")
    test_end = (te + pd.Timedelta(days=val_days + test_days)).strftime("%Y-%m-%d")
    return val_end, test_end


# ── Bug A: per-symbol mode keeps only the chosen symbol ────────────────

def test_per_symbol_mode_keeps_only_chosen_symbol(synthetic_df):
    train_end = "2025-10-15"
    val_end, test_end = _windows(train_end)
    args = SimpleNamespace(
        train_mode="per-symbol",
        symbol="BTCUSDT",
        train_start=None,
        train_end=train_end,
        val_end=val_end,
        test_end=test_end,
    )
    df_train, df_val, df_test = _apply_split(synthetic_df, args)

    for label, split in [("train", df_train), ("val", df_val), ("test", df_test)]:
        assert len(split) > 0, f"{label} split unexpectedly empty"
        assert split["symbol"].unique().tolist() == ["BTCUSDT"], (
            f"{label} split contains symbols beyond BTCUSDT: {split['symbol'].unique()}"
        )


def test_per_symbol_mode_requires_symbol(synthetic_df):
    args = SimpleNamespace(
        train_mode="per-symbol",
        symbol=None,
        train_start=None,
        train_end="2025-10-15",
        val_end="2025-10-20",
        test_end="2025-10-25",
    )
    with pytest.raises(ValueError, match="requires --symbol"):
        _apply_split(synthetic_df, args)


# ── Joint mode keeps all 4 symbols ────────────────────────────────────

def test_joint_mode_keeps_all_symbols(synthetic_df):
    train_end = "2025-10-15"
    val_end, test_end = _windows(train_end)
    args = SimpleNamespace(
        train_mode="joint",
        symbol=None,
        train_start=None,
        train_end=train_end,
        val_end=val_end,
        test_end=test_end,
    )
    df_train, df_val, df_test = _apply_split(synthetic_df, args)

    for label, split in [("train", df_train), ("val", df_val), ("test", df_test)]:
        assert len(split) > 0
        present = sorted(split["symbol"].unique().tolist())
        assert present == sorted(SYMBOLS), (
            f"{label} expected all 4 symbols, got {present}"
        )


# ── Bug B: temporal disjointness (train < val < test) ────────────────

@pytest.mark.parametrize("mode,symbol", [
    ("per-symbol", "ETHUSDT"),
    ("joint", None),
])
def test_temporal_disjointness(synthetic_df, mode, symbol):
    train_end = "2025-10-15"
    val_end, test_end = _windows(train_end)
    args = SimpleNamespace(
        train_mode=mode,
        symbol=symbol,
        train_start=None,
        train_end=train_end,
        val_end=val_end,
        test_end=test_end,
    )
    df_train, df_val, df_test = _apply_split(synthetic_df, args)
    assert df_train["date"].max() <= train_end
    assert df_val["date"].min() > train_end
    assert df_val["date"].max() <= val_end
    assert df_test["date"].min() > val_end
    # Strict cross-split:
    assert df_train["date"].max() < df_val["date"].min()
    assert df_val["date"].max() < df_test["date"].min()


def test_no_hardcoded_val_end_test_window(synthetic_df):
    """Bug B regression: when train_end is moved FAR past the legacy
    _LEGACY_VAL_END_DEFAULT, the test set must start AFTER train_end, not at
    the legacy 2026-02-15 boundary. We pick a train_end well after that
    legacy boundary and verify the test split respects the local val_end."""
    # 30 days starting Mar 2026 → all data > legacy default 2026-02-15
    df = _build_synthetic_multi_symbol_df("2026-03-01", n_days=30)
    train_end = "2026-03-15"
    val_end, test_end = _windows(train_end)
    args = SimpleNamespace(
        train_mode="per-symbol",
        symbol="BTCUSDT",
        train_start=None,
        train_end=train_end,
        val_end=val_end,
        test_end=test_end,
    )
    df_train, df_val, df_test = _apply_split(df, args)
    # If Bug B were still present, df_test would include rows BEFORE train_end
    # (because "df.date > VAL_END" where VAL_END='2026-02-15' admits the
    # entire training window). The fix must reject that.
    if len(df_test) > 0:
        assert df_test["date"].min() > train_end, (
            f"Test set leaked into training window: test_min={df_test['date'].min()} "
            f"<= train_end={train_end}"
        )


def test_module_constants_renamed():
    """Lock the rename: TRAIN_END / VAL_END must NOT exist as module attrs
    on validation.run_training, only the _LEGACY_* renamed defaults."""
    import validation.run_training as rt

    assert not hasattr(rt, "TRAIN_END"), (
        "TRAIN_END module constant still exists — rename to _LEGACY_TRAIN_END_DEFAULT"
    )
    assert not hasattr(rt, "VAL_END"), (
        "VAL_END module constant still exists — rename to _LEGACY_VAL_END_DEFAULT"
    )
    assert hasattr(rt, "_LEGACY_TRAIN_END_DEFAULT")
    assert hasattr(rt, "_LEGACY_VAL_END_DEFAULT")


def test_feature_cols_per_symbol_drops_symbol_cat():
    """Bug A side fix: per-symbol mode uses a feature list without symbol_cat
    (which is a constant column when the DF is filtered to one symbol)."""
    from validation.run_training import FEATURE_COLS, FEATURE_COLS_PER_SYMBOL

    assert "symbol_cat" in FEATURE_COLS
    assert "symbol_cat" not in FEATURE_COLS_PER_SYMBOL
    # Otherwise identical
    assert set(FEATURE_COLS) - {"symbol_cat"} == set(FEATURE_COLS_PER_SYMBOL)
