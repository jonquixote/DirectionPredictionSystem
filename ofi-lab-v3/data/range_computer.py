"""Compute mid_price min/max/median per symbol from feature parquets.

Replaces v2's hardcoded MID_PRICE_TRAINING_RANGE which omitted XRP.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


def compute_mid_price_range(feature_dir: str, symbol: str) -> Optional[dict]:
    import pyarrow.parquet as pq

    fdir = Path(feature_dir)
    files = sorted(fdir.glob(f"*_{symbol}_features.parquet"))
    if not files:
        return None

    mins = []
    maxs = []
    all_vals = []
    for f in files:
        try:
            table = pq.read_table(str(f), columns=["mid_price"])
        except Exception:
            continue
        col = table.column("mid_price").to_pylist()
        if not col:
            continue
        mins.append(min(col))
        maxs.append(max(col))
        all_vals.extend(col)
    if not all_vals:
        return None
    all_vals.sort()
    n = len(all_vals)
    median = all_vals[n // 2] if n % 2 == 1 else \
        0.5 * (all_vals[n // 2 - 1] + all_vals[n // 2])
    return {"min": min(mins), "max": max(maxs), "median": median}
