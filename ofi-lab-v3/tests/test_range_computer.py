from pathlib import Path

import pytest

from data.range_computer import compute_mid_price_range


def test_range_from_synthetic_parquet(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp_path / "20260501_XRPUSDT_features.parquet"
    pq.write_table(pa.table({"mid_price": [0.50, 0.55, 0.60, 0.62, 0.58]}), str(p))
    rng = compute_mid_price_range(str(tmp_path), "XRPUSDT")
    assert rng["min"] == 0.50
    assert rng["max"] == 0.62
    assert abs(rng["median"] - 0.58) < 1e-9


def test_range_aggregates_multiple_files(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({"mid_price": [60_000.0, 61_000.0]}),
                   str(tmp_path / "20260501_BTCUSDT_features.parquet"))
    pq.write_table(pa.table({"mid_price": [59_000.0, 62_000.0]}),
                   str(tmp_path / "20260502_BTCUSDT_features.parquet"))
    rng = compute_mid_price_range(str(tmp_path), "BTCUSDT")
    assert rng["min"] == 59_000.0
    assert rng["max"] == 62_000.0


def test_missing_symbol_returns_none(tmp_path):
    rng = compute_mid_price_range(str(tmp_path), "ZZZUSDT")
    assert rng is None
