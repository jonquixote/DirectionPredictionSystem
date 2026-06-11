#!/usr/bin/env python3
"""Track B dataset assembly — join OFI features to market-window context + label.

Per (symbol, duration) builds rows sampled every SAMPLE_S seconds:
  features: registered OFI set (from probe_ofi parquets) +
            dist_open_bps, time_remaining, ewma_vol (already in parquets)
  label:    window close >= open  -> 1 (flat=UP, contract-exact, registered)

Window semantics (mirror resolution): open = mid of first 1s row at/after
window start; close = mid of first 1s row at/after window end. Rows inside
a window carry that window's open for dist_open_bps and the fraction of
window remaining. Rows in windows whose close cannot be resolved (data gap
> 5s at either edge) are dropped.

Output: /data/probe_ofi/dataset_{symbol}_{dur}m.parquet

Usage: python3 build_dataset.py --symbol BTCUSDT --dur 5 \
    --start 2026-03-01 --end 2026-06-10
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("dataset")

SAMPLE_S = 10          # emit one example every 10 s
EDGE_TOL_MS = 5_000    # max gap when locating window open/close prices

FEATURES = [
    "ofi_l1", "ofi_l5w",
    "ofi_l1_10s", "ofi_l1_30s", "ofi_l1_60s", "ofi_l1_300s",
    "ofi_l5w_10s", "ofi_l5w_30s", "ofi_l5w_60s", "ofi_l5w_300s",
    "ofi_l1_10s_norm", "ofi_l1_30s_norm", "ofi_l1_60s_norm", "ofi_l1_300s_norm",
    "ofi_l5w_10s_norm", "ofi_l5w_30s_norm", "ofi_l5w_60s_norm", "ofi_l5w_300s_norm",
    "ewma_vol", "dist_open_bps", "time_remaining",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--dur", type=int, required=True, choices=[5, 15])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--ofi-dir", default="/data/probe_ofi")
    args = ap.parse_args()

    src = Path(args.ofi_dir) / args.symbol
    d0 = datetime.strptime(args.start, "%Y-%m-%d").date()
    d1 = datetime.strptime(args.end, "%Y-%m-%d").date()
    files = []
    d = d0
    while d <= d1:
        f = src / f"{d}_{args.symbol}_ofi.parquet"
        if f.exists():
            files.append(f)
        d += timedelta(days=1)
    if not files:
        log.error("no OFI parquets in range")
        return 1
    df = pl.concat([pl.read_parquet(f) for f in files]).sort("cts")
    log.info("%s: %d 1s rows from %d day files", args.symbol, len(df), len(files))

    cts = df["cts"].to_numpy()
    mid = df["mid_price"].to_numpy()
    win_ms = args.dur * 60 * 1000

    # window open/close per boundary
    b0 = (int(cts[0]) // win_ms + 1) * win_ms
    b1 = (int(cts[-1]) // win_ms) * win_ms
    bounds = np.arange(b0, b1 + 1, win_ms)
    idx = np.searchsorted(cts, bounds)              # first row at/after boundary
    valid_edge = (idx < len(cts)) & ((cts[np.clip(idx, 0, len(cts)-1)] - bounds) <= EDGE_TOL_MS)
    edge_price = np.where(valid_edge, mid[np.clip(idx, 0, len(cts)-1)], np.nan)

    # label per window i: close(i+1 edge) >= open(i edge)
    w_open = edge_price[:-1]
    w_close = edge_price[1:]
    w_start = bounds[:-1]
    w_label = (w_close >= w_open).astype(float)
    w_ok = ~np.isnan(w_open) & ~np.isnan(w_close)
    log.info("windows: %d total, %d resolvable (%.2f%%)",
             len(w_start), int(w_ok.sum()), 100 * w_ok.mean())

    # sample rows every SAMPLE_S within resolvable windows
    win_i = np.searchsorted(bounds, cts, side="right") - 1   # window index per row
    in_range = (win_i >= 0) & (win_i < len(w_start))
    sample_mask = in_range & ((cts // 1000) % SAMPLE_S == 0)
    wi = win_i[sample_mask]
    ok = w_ok[wi]
    rows = df.filter(pl.Series(sample_mask)).filter(pl.Series(ok))
    wi = wi[ok]

    opens = w_open[wi]
    starts = w_start[wi]
    labels = w_label[wi]
    rcts = rows["cts"].to_numpy()
    rmid = rows["mid_price"].to_numpy()

    out = rows.with_columns([
        pl.Series("dist_open_bps", 1e4 * (rmid - opens) / opens),
        pl.Series("time_remaining", 1.0 - (rcts - starts) / win_ms),
        pl.Series("label", labels.astype(np.int8)),
        pl.Series("window_start_ms", starts),
    ]).select(["cts", "window_start_ms", "label"] + FEATURES)

    dest = Path(args.ofi_dir) / f"dataset_{args.symbol}_{args.dur}m.parquet"
    out.write_parquet(dest, compression="zstd", compression_level=6)
    log.info("wrote %s: %d rows, label mean %.4f", dest, len(out),
             float(out["label"].mean()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
