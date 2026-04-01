#!/usr/bin/env python3
"""Quick sanity check on features_v2 data before training."""
import polars as pl
import numpy as np
import os, glob, sys

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
BASE = sys.argv[1] if len(sys.argv) > 1 else "/data/features_v2"

ROLLING_COLS = [
    "mlofi_30s_mean", "mlofi_60s_mean", "mlofi_120s_mean",
    "ofi_30s_mean", "ofi_60s_mean",
    "mlofi_30s_std", "mlofi_60s_std", "ofi_60s_std",
    "spread_5m_pct",
]

print("=" * 60)
print(f"SANITY CHECK SUITE — {BASE}")
print("=" * 60)

all_pass = True

for sym in SYMBOLS:
    files = sorted(glob.glob(f"{BASE}/{sym}/*.parquet"))
    print(f"\n{'='*50}\n[{sym}] {len(files)} files")

    all_rows = 0
    low_count_days = []

    for f in files:
        df = pl.read_parquet(f)
        n = len(df)
        all_rows += n
        day = os.path.basename(f).split("_")[0]
        if n < 50000:
            low_count_days.append((day, n))

    print(f"  Total rows: {all_rows:,}")
    if low_count_days:
        print(f"  WARNING: {len(low_count_days)} days below 50K rows:")
        for d, n in low_count_days[:10]:
            print(f"    {d}: {n:,}")
    else:
        print("  OK: All days >= 50K rows")

    # Detailed checks on first, middle, last days
    sample_files = [files[0], files[len(files)//2], files[-1]]

    for f in sample_files:
        df = pl.read_parquet(f)
        day = os.path.basename(f).split("_")[0]

        float_cols = [c for c in df.columns if df[c].dtype in [pl.Float64, pl.Float32]]
        null_count = sum(df[c].null_count() for c in float_cols)
        nan_count = sum(df[c].is_nan().sum() for c in float_cols)

        mlofi = df["mlofi"].to_numpy()
        mlofi_nz = mlofi[mlofi != 0]
        spread = df["spread"].to_numpy()
        vpin = df["vpin"].to_numpy()
        vwap = df["vwap_deviation"].to_numpy()
        vwap_nz = vwap[vwap != 0]

        # Rolling
        r_null = sum(df[c].null_count() for c in ROLLING_COLS)
        r_nan = sum(df[c].is_nan().sum() for c in ROLLING_COLS)
        spm = df["spread_5m_pct"].to_numpy()

        ok1 = nan_count == 0 and null_count == 0
        ok2 = len(mlofi_nz) > 0 and abs(np.mean(mlofi_nz)) < 0.5
        ok3 = np.all(spread > 0)
        ok4 = np.all((vpin >= 0) & (vpin <= 1))
        ok5 = len(vwap_nz) == 0 or abs(np.mean(vwap_nz)) < 0.001
        okr = r_nan == 0 and r_null == 0 and np.all((spm >= 0) & (spm <= 1))

        s1 = "PASS" if ok1 else "FAIL"
        s2 = "PASS" if ok2 else "FAIL"
        s3 = "PASS" if ok3 else "FAIL"
        s4 = "PASS" if ok4 else "FAIL"
        s5 = "PASS" if ok5 else "FAIL"
        sr = "PASS" if okr else "FAIL"

        if not all([ok1, ok2, ok3, ok4, ok5, okr]):
            all_pass = False

        print(f"  [{day}] {len(df):,} rows")
        print(f"    1. NaN/Null: {nan_count}/{null_count}  [{s1}]")
        mlofi_mean = np.mean(mlofi_nz) if len(mlofi_nz) > 0 else 0
        print(f"    2. MLOFI mean={mlofi_mean:.4f}  [{s2}]")
        print(f"    3. Spread min={np.min(spread):.6f} all>0  [{s3}]")
        print(f"    4. VPIN [{np.min(vpin):.4f}, {np.max(vpin):.4f}]  [{s4}]")
        vwap_mean = np.mean(vwap_nz) if len(vwap_nz) > 0 else 0
        print(f"    5. VWAP dev mean={vwap_mean:.8f}  [{s5}]")
        print(f"    R. Rolling null/nan={r_null}/{r_nan} spread_pct=[{np.min(spm):.3f},{np.max(spm):.3f}]  [{sr}]")

    # Show columns from first file
    df0 = pl.read_parquet(files[0])
    print(f"  Columns ({len(df0.columns)}): {df0.columns}")

print("\n" + "=" * 60)
if all_pass:
    print("ALL SANITY CHECKS PASSED")
else:
    print("SOME CHECKS FAILED — review above")
print("=" * 60)
