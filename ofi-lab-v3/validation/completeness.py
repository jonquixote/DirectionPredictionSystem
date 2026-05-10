"""Data completeness validator.

Before a retrain begins, this module verifies that every day in the
requested date range has a corresponding feature parquet. The retrain
pipeline aborts if more than N consecutive days are missing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import List


def enumerate_dates(start: date, end: date) -> List[date]:
    out = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


@dataclass
class CoverageReport:
    total_days: int
    present_days: List[date]
    missing_days: List[date]
    max_consecutive_missing: int

    @property
    def coverage_pct(self) -> float:
        if self.total_days == 0:
            return 100.0
        return round(100.0 * len(self.present_days) / self.total_days, 2)

    def passes(self, max_consecutive_missing: int) -> bool:
        return self.max_consecutive_missing <= max_consecutive_missing


def check_feature_coverage(
    feature_dir: str, symbol: str, start: date, end: date,
) -> CoverageReport:
    days = enumerate_dates(start, end)
    fdir = Path(feature_dir)
    present: List[date] = []
    missing: List[date] = []
    for d in days:
        slug = d.strftime("%Y%m%d")
        candidate = fdir / f"{slug}_{symbol}_features.parquet"
        if candidate.exists():
            present.append(d)
        else:
            missing.append(d)
    # Compute longest run of consecutive missing days
    missing_set = set(missing)
    max_run = 0
    cur = 0
    for d in days:
        if d in missing_set:
            cur += 1
            max_run = max(max_run, cur)
        else:
            cur = 0
    return CoverageReport(
        total_days=len(days), present_days=present,
        missing_days=missing, max_consecutive_missing=max_run,
    )
