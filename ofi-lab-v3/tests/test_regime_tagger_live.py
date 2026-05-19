"""Tests for the regime tagger end-to-end path including threshold_updater
DB writer and PaperTrader._reload_regime_thresholds DB fallback.

These tests use only stdlib + pyarrow (already a dependency) and don't
require a running paper trader.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from regime.tagger import compute_regime, RegimeTags
from regime.threshold_updater import (
    REGIME_SIGNALS,
    compute_thresholds_from_parquets,
    load_thresholds_from_db,
    refresh_thresholds_db,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_parquet(path: Path, n: int = 200) -> None:
    """Write a synthetic feature parquet with known quartile structure."""
    pq.write_table(
        pa.table({
            "vwap_dev_30s_std": [i * 0.0001 for i in range(n)],    # 0..0.0199
            "mlofi_60s_std":    [float(i) for i in range(n)],       # 0..199
            "relative_spread":  [i * 0.00001 for i in range(n)],    # 0..0.00199
            "spread_5m_pct":    [i / n for i in range(n)],          # 0..~1.0
            "mlofi_momentum":   [i - n // 2 for i in range(n)],     # -100..99
            "vwap_2m_deviation": [i * 0.0001 - 0.01 for i in range(n)],
        }),
        str(path),
    )


def _make_in_memory_db() -> sqlite3.Connection:
    """Create a minimal in-memory SQLite DB with regime tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE regime_thresholds (
            symbol          TEXT PRIMARY KEY,
            thresholds_json TEXT NOT NULL,
            updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Test 1: valid thresholds → non-unknown labels
# ---------------------------------------------------------------------------

def test_compute_regime_with_valid_thresholds_returns_non_unknown():
    """With realistic quartile thresholds, compute_regime() should return
    labelled (non-unknown) regime tags for every axis."""
    thresholds = {
        "BTCUSDT": {
            "vwap_dev_30s_std": {"p25": 0.00007, "p50": 0.00012, "p75": 0.00018},
            "mlofi_60s_std":    {"p25": 1.0,     "p50": 1.2,     "p75": 1.4},
            "relative_spread":  {"p25": 0.000001, "p50": 0.000002, "p75": 0.000004},
            "spread_5m_pct":    {"p25": 0.0,     "p50": 0.0,     "p75": 0.0},
            "mlofi_momentum":   {"p25": -0.15,   "p50": 0.0,     "p75": 0.15},
            "vwap_2m_deviation":{"p25": -0.0002, "p50": 0.0,     "p75": 0.0002},
        }
    }
    features = {
        "vwap_dev_30s_std": 0.00010,
        "mlofi_60s_std":    1.2,
        "relative_spread":  0.000002,
        "spread_5m_pct":    0.0,
        "mlofi_momentum":   0.0,
        "vwap_2m_deviation": 0.0,
    }
    tags = compute_regime("BTCUSDT", features, thresholds)
    assert tags.volatility != "unknown", f"Expected non-unknown volatility, got {tags.volatility}"
    assert tags.liquidity  != "unknown", f"Expected non-unknown liquidity, got {tags.liquidity}"
    assert tags.trend      != "unknown", f"Expected non-unknown trend, got {tags.trend}"
    assert tags.volatility in ("low", "medium", "high")
    assert tags.liquidity  in ("thin", "normal", "deep")
    assert tags.trend      in ("trending", "choppy", "mean_reverting")


# ---------------------------------------------------------------------------
# Test 2: empty thresholds → unknown (graceful fallback)
# ---------------------------------------------------------------------------

def test_compute_regime_empty_thresholds_returns_unknown():
    """When thresholds dict is empty (file absent, DB empty), every tag
    should be 'unknown' — no exception raised."""
    tags = compute_regime("BTCUSDT", {}, {})
    assert tags == RegimeTags(volatility="unknown", liquidity="unknown", trend="unknown")


def test_compute_regime_missing_symbol_returns_unknown():
    """Symbol not in thresholds dict → unknown, even with populated dict."""
    thresholds = {"ETHUSDT": {sig: {"p25": 0.0, "p50": 0.0, "p75": 0.0}
                               for sig in REGIME_SIGNALS}}
    tags = compute_regime("BTCUSDT", {}, thresholds)
    assert tags == RegimeTags(volatility="unknown", liquidity="unknown", trend="unknown")


# ---------------------------------------------------------------------------
# Test 3: threshold_updater DB writer
# ---------------------------------------------------------------------------

def test_threshold_updater_writes_quartiles_to_db(tmp_path):
    """refresh_thresholds_db() should upsert sensible p25/p50/p75 per symbol
    and load_thresholds_from_db() should return them."""
    # Write synthetic parquets for BTCUSDT
    _make_parquet(tmp_path / "2026-05-01_BTCUSDT_features.parquet")
    _make_parquet(tmp_path / "2026-05-02_BTCUSDT_features.parquet")

    conn = _make_in_memory_db()
    result = refresh_thresholds_db(
        feature_dir=str(tmp_path),
        symbols=["BTCUSDT"],
        db_conn=conn,
        days=30,
    )

    # Return value has the right shape
    assert "BTCUSDT" in result
    for sig in REGIME_SIGNALS:
        assert sig in result["BTCUSDT"], f"Missing signal {sig} in result"
        q = result["BTCUSDT"][sig]
        assert q["p25"] <= q["p50"] <= q["p75"], \
            f"{sig}: quartiles not ordered: {q}"

    # DB table was populated
    loaded = load_thresholds_from_db(conn)
    assert "BTCUSDT" in loaded
    vwap = loaded["BTCUSDT"]["vwap_dev_30s_std"]
    assert vwap["p25"] < vwap["p75"], "p25 should be less than p75"


def test_threshold_updater_upsert_is_idempotent(tmp_path):
    """Running refresh_thresholds_db() twice should not duplicate rows."""
    _make_parquet(tmp_path / "2026-05-01_BTCUSDT_features.parquet")
    conn = _make_in_memory_db()
    refresh_thresholds_db(str(tmp_path), ["BTCUSDT"], conn, days=30)
    refresh_thresholds_db(str(tmp_path), ["BTCUSDT"], conn, days=30)
    count = conn.execute("SELECT COUNT(*) FROM regime_thresholds").fetchone()[0]
    assert count == 1, f"Expected 1 row after double upsert, got {count}"


# ---------------------------------------------------------------------------
# Test 4: subdir parquet layout (VPS uses /data/features_v3/BTCUSDT/)
# ---------------------------------------------------------------------------

def test_compute_thresholds_from_subdir_layout(tmp_path):
    """Parquets stored in <feature_dir>/<SYMBOL>/ subdir should be found."""
    sym_dir = tmp_path / "BTCUSDT"
    sym_dir.mkdir()
    _make_parquet(sym_dir / "2026-05-01_BTCUSDT_features.parquet")

    out = compute_thresholds_from_parquets(
        feature_dir=str(tmp_path), symbol="BTCUSDT", days=30
    )
    assert out["vwap_dev_30s_std"]["p25"] > 0, "Should find data from subdir"


# ---------------------------------------------------------------------------
# Test 5: end-to-end: parquets → DB → compute_regime gives labelled tags
# ---------------------------------------------------------------------------

def test_full_pipeline_parquet_to_labelled_tags(tmp_path):
    """Full pipeline: parquet → DB thresholds → compute_regime → non-unknown."""
    _make_parquet(tmp_path / "2026-05-01_BTCUSDT_features.parquet")

    conn = _make_in_memory_db()
    refresh_thresholds_db(str(tmp_path), ["BTCUSDT"], conn, days=30)
    thresholds = load_thresholds_from_db(conn)

    # Use median-ish feature values that should land in 'medium' buckets
    features = {
        "vwap_dev_30s_std": 0.001,   # middle of 0..0.0199
        "mlofi_60s_std":    100.0,   # middle of 0..199
        "relative_spread":  0.001,   # middle of 0..0.00199
        "spread_5m_pct":    0.5,     # middle of 0..1
        "mlofi_momentum":   0.0,     # middle of -100..99
        "vwap_2m_deviation": 0.0,    # middle
    }
    tags = compute_regime("BTCUSDT", features, thresholds)
    assert tags.volatility != "unknown"
    assert tags.liquidity  != "unknown"
    assert tags.trend      != "unknown"
