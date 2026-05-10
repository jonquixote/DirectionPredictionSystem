from datetime import date
from pathlib import Path

import pytest

from validation.completeness import (
    enumerate_dates, check_feature_coverage, CoverageReport,
)


def test_enumerate_dates_inclusive():
    out = enumerate_dates(date(2026, 5, 1), date(2026, 5, 3))
    assert [d.isoformat() for d in out] == [
        "2026-05-01", "2026-05-02", "2026-05-03"
    ]


def test_full_coverage(tmp_path):
    # Create feature parquet for every day in range
    for d in ("20260501", "20260502", "20260503"):
        (tmp_path / f"{d}_BTCUSDT_features.parquet").write_text("x")
    rep = check_feature_coverage(
        feature_dir=str(tmp_path),
        symbol="BTCUSDT",
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )
    assert rep.total_days == 3
    assert rep.missing_days == []
    assert rep.coverage_pct == 100.0


def test_partial_coverage_with_one_missing_day(tmp_path):
    (tmp_path / "20260501_BTCUSDT_features.parquet").write_text("x")
    (tmp_path / "20260503_BTCUSDT_features.parquet").write_text("x")
    rep = check_feature_coverage(
        feature_dir=str(tmp_path),
        symbol="BTCUSDT",
        start=date(2026, 5, 1),
        end=date(2026, 5, 3),
    )
    assert rep.missing_days == [date(2026, 5, 2)]
    assert rep.max_consecutive_missing == 1
    assert abs(rep.coverage_pct - 66.67) < 0.05


def test_three_consecutive_missing_days_exceeds_max(tmp_path):
    (tmp_path / "20260501_BTCUSDT_features.parquet").write_text("x")
    (tmp_path / "20260505_BTCUSDT_features.parquet").write_text("x")
    rep = check_feature_coverage(
        feature_dir=str(tmp_path),
        symbol="BTCUSDT",
        start=date(2026, 5, 1),
        end=date(2026, 5, 5),
    )
    assert rep.max_consecutive_missing == 3
    assert rep.passes(max_consecutive_missing=2) is False
    assert rep.passes(max_consecutive_missing=3) is True
