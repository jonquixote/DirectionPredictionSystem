#!/usr/bin/env python3
"""Track B trainer — registered 16-config grid, embargoed walk-forward.

Train span: window_start < 2026-05-24 00:00 UTC. The 05-24+ span is the
single-touch test set and is NEVER read here (hard-filtered on load).

Pooling decision (registered in run log): one model per duration, pooled
across all 4 symbols. Symbol identity is NOT a feature (list closed).
Trees split on raw-scale differences; *_norm variants are scale-free.

Walk-forward: 4 chronological folds inside the train span. Embargo at every
split boundary: 2100 s (1800 s max window + 300 s longest feature lookback).
Selection: mean validation AUC across folds. Winner is refit on the full
train span and saved with its config for the single-touch evaluation.

Grid (16, registered): num_leaves {31,63} x learning_rate {0.05,0.1}
x min_child_samples {100,500} x bagging_fraction {0.8,1.0};
feature_fraction 0.8, n_estimators 300 fixed.

Usage: python3 train.py --dur 5 --ofi-dir /data/probe_ofi --out /data/probe_ofi/model_5m
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import polars as pl
import lightgbm as lgb
from sklearn.metrics import roc_auc_score

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("train")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
TEST_START_MS = int(datetime(2026, 5, 24, tzinfo=timezone.utc).timestamp() * 1000)
EMBARGO_MS = 2100 * 1000
N_FOLDS = 4

GRID = [dict(num_leaves=nl, learning_rate=lr, min_child_samples=mcs,
             bagging_fraction=bf, bagging_freq=(1 if bf < 1.0 else 0),
             feature_fraction=0.8, n_estimators=300)
        for nl, lr, mcs, bf in itertools.product(
            [31, 63], [0.05, 0.1], [100, 500], [0.8, 1.0])]
assert len(GRID) == 16


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dur", type=int, required=True, choices=[5, 15])
    ap.add_argument("--ofi-dir", default="/data/probe_ofi")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    frames = []
    for sym in SYMBOLS:
        f = Path(args.ofi_dir) / f"dataset_{sym}_{args.dur}m.parquet"
        if not f.exists():
            log.error("missing dataset %s — build all symbols first", f)
            return 1
        frames.append(pl.read_parquet(f))
    df = pl.concat(frames).sort("cts")
    # HARD test-span exclusion — the single-touch rule
    df = df.filter(pl.col("window_start_ms") < TEST_START_MS)
    log.info("pooled train-span rows: %d (label mean %.4f)",
             len(df), float(df["label"].mean()))

    feat_cols = [c for c in df.columns if c not in
                 ("cts", "window_start_ms", "label")]
    X = df.select(feat_cols).to_numpy().astype(np.float64)
    y = df["label"].to_numpy()
    cts = df["cts"].to_numpy()

    t0, t1 = cts.min(), cts.max()
    edges = np.linspace(t0, t1, N_FOLDS + 2).astype(np.int64)  # 5 segments: 4 trainable prefixes
    results = []
    for gi, cfg in enumerate(GRID):
        aucs = []
        for k in range(1, N_FOLDS + 1):
            tr = cts < (edges[k] - EMBARGO_MS)
            va = (cts >= edges[k]) & (cts < (edges[k + 1] - EMBARGO_MS))
            if tr.sum() < 10_000 or va.sum() < 10_000:
                continue
            m = lgb.LGBMClassifier(random_state=42, n_jobs=4, verbose=-1, **cfg)
            m.fit(X[tr], y[tr], feature_name=feat_cols)
            p = m.predict_proba(X[va])[:, 1]
            aucs.append(roc_auc_score(y[va], p))
        mean_auc = float(np.mean(aucs)) if aucs else float("nan")
        results.append((mean_auc, gi, cfg, aucs))
        log.info("config %2d/16 %s -> wf AUCs %s mean %.5f",
                 gi + 1, {k: v for k, v in cfg.items()
                          if k in ("num_leaves", "learning_rate",
                                   "min_child_samples", "bagging_fraction")},
                 [f"{a:.4f}" for a in aucs], mean_auc)

    results.sort(key=lambda r: -(r[0] if r[0] == r[0] else -1))
    best_auc, best_i, best_cfg, best_fold_aucs = results[0]
    log.info("WINNER config %d: mean wf AUC %.5f", best_i + 1, best_auc)

    final = lgb.LGBMClassifier(random_state=42, n_jobs=4, verbose=-1, **best_cfg)
    final.fit(X, y, feature_name=feat_cols)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    final.booster_.save_model(str(out / "model.lgb"))
    (out / "winner.json").write_text(json.dumps({
        "duration_min": args.dur, "config_index": best_i, "config": best_cfg,
        "wf_mean_auc": best_auc, "wf_fold_aucs": best_fold_aucs,
        "feature_names": feat_cols, "n_train_rows": int(len(df)),
        "test_start_ms": TEST_START_MS,
        "all_results": [(r[0], r[1]) for r in results],
    }, indent=1, default=float))
    log.info("saved %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
