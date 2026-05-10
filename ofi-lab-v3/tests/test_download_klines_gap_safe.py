from pathlib import Path

import pytest

from data.download_klines import is_complete_day_file, EXPECTED_ROWS_PER_DAY


def test_expected_rows_per_day_is_1440():
    assert EXPECTED_ROWS_PER_DAY == 1440


def test_missing_file_is_not_complete(tmp_path):
    assert is_complete_day_file(tmp_path / "missing.parquet") is False


def test_empty_file_is_not_complete(tmp_path):
    p = tmp_path / "empty.parquet"
    p.write_bytes(b"")
    assert is_complete_day_file(p) is False


def test_full_day_parquet_is_complete(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp_path / "full.parquet"
    table = pa.table({"open_time": list(range(1440))})
    pq.write_table(table, str(p))
    assert is_complete_day_file(p) is True


def test_partial_day_parquet_is_not_complete(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    p = tmp_path / "partial.parquet"
    table = pa.table({"open_time": list(range(500))})
    pq.write_table(table, str(p))
    assert is_complete_day_file(p) is False
