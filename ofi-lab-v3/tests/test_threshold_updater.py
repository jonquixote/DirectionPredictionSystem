import json
from pathlib import Path

import pytest

from regime.threshold_updater import (
    compute_thresholds_from_parquets,
    refresh_thresholds_file,
)


def test_compute_thresholds_returns_quartiles_per_signal(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp_path / "20260501_BTCUSDT_features.parquet"
    n = 100
    pq.write_table(pa.table({
        "vwap_dev_30s_std": list(range(n)),
        "mlofi_60s_std":    [i * 2 for i in range(n)],
        "relative_spread":  [i / 1000 for i in range(n)],
        "spread_5m_pct":    [i / 100 for i in range(n)],
        "mlofi_momentum":   [i - 50 for i in range(n)],
        "vwap_2m_deviation": [i - 50 for i in range(n)],
    }), str(p))
    out = compute_thresholds_from_parquets(
        feature_dir=str(tmp_path),
        symbol="BTCUSDT",
        days=1,
    )
    assert "vwap_dev_30s_std" in out
    assert "mlofi_60s_std" in out
    assert out["vwap_dev_30s_std"]["p25"] < out["vwap_dev_30s_std"]["p50"]
    assert out["vwap_dev_30s_std"]["p50"] < out["vwap_dev_30s_std"]["p75"]


def test_refresh_thresholds_file_writes_per_symbol(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    for d, sym in [("20260501", "BTCUSDT"), ("20260501", "ETHUSDT")]:
        pq.write_table(pa.table({
            "vwap_dev_30s_std": list(range(50)),
            "mlofi_60s_std":    list(range(50)),
            "relative_spread":  [i/100 for i in range(50)],
            "spread_5m_pct":    [i/100 for i in range(50)],
            "mlofi_momentum":   list(range(-25, 25)),
            "vwap_2m_deviation": list(range(-25, 25)),
        }), str(tmp_path / f"{d}_{sym}_features.parquet"))

    out_path = tmp_path / "regime_thresholds.json"
    refresh_thresholds_file(
        feature_dir=str(tmp_path),
        symbols=["BTCUSDT", "ETHUSDT"],
        out_path=str(out_path),
        days=1,
    )
    body = json.loads(out_path.read_text())
    assert "updated_at" in body
    assert "BTCUSDT" in body
    assert "ETHUSDT" in body
    assert "vwap_dev_30s_std" in body["BTCUSDT"]
