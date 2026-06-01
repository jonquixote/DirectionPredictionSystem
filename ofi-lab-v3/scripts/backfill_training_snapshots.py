#!/usr/bin/env python3
"""Backfill training_data_snapshots for all model_registry rows that don't
have a snapshot yet.

Usage:
  python3 scripts/backfill_training_snapshots.py [--dry-run] [--limit N]

Options:
  --dry-run   Print what would be computed without writing to the DB.
  --limit N   Process at most N models (default: all).

VPS usage (from /home/johnny/ofi-lab-v3, with venv active):
  source .venv/bin/activate
  PYTHONPATH=/home/johnny/ofi-lab-v3 python3 scripts/backfill_training_snapshots.py --dry-run
  PYTHONPATH=/home/johnny/ofi-lab-v3 python3 scripts/backfill_training_snapshots.py --limit 5
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

# Allow running from repo root or from /home/johnny/ofi-lab-v3
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_snapshots")


def _get_db():
    try:
        from services.db import get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore[assignment]
    return get_db()


def main():
    parser = argparse.ArgumentParser(description="Backfill training_data_snapshots table")
    parser.add_argument("--dry-run", action="store_true", help="Print plan without writing")
    parser.add_argument("--limit", type=int, default=None, help="Max models to process")
    args = parser.parse_args()

    db = _get_db()

    # All models not yet in training_data_snapshots
    rows = db.execute(
        """
        SELECT r.name, r.symbol, r.train_window_start, r.train_window_end,
               r.artifact_path, r.fleet_version
        FROM model_registry r
        LEFT JOIN training_data_snapshots s ON r.name = s.model_name
        WHERE s.model_name IS NULL
          AND r.train_window_start IS NOT NULL
          AND r.train_window_end IS NOT NULL
          AND r.symbol IS NOT NULL
        ORDER BY r.fleet_version DESC, r.name
        """
    ).fetchall()

    total_pending = len(rows)
    logger.info("Models pending snapshot: %d", total_pending)

    if args.limit:
        rows = rows[: args.limit]
        logger.info("Limiting to first %d models", len(rows))

    if args.dry_run:
        logger.info("-- DRY RUN: no writes will be made --")
        for r in rows:
            logger.info(
                "  WOULD COMPUTE: %s  symbol=%s  window=[%s, %s]  artifact=%s",
                r["name"], r["symbol"], r["train_window_start"],
                r["train_window_end"], r["artifact_path"],
            )
        logger.info("Dry-run complete. %d models would be processed.", len(rows))
        return

    try:
        from dashboard_api.services.training_drift import compute_feature_fingerprint
    except ImportError:
        from services.training_drift import compute_feature_fingerprint  # type: ignore

    n_ok = 0
    n_err = 0
    import hashlib

    for i, r in enumerate(rows):
        model_name = r["name"]
        symbol = r["symbol"]
        tws = r["train_window_start"]
        twe = r["train_window_end"]
        artifact_path = r["artifact_path"]

        logger.info(
            "[%d/%d] %s  symbol=%s  window=[%s, %s]",
            i + 1, len(rows), model_name, symbol, tws, twe,
        )

        try:
            t0 = time.time()
            fp = compute_feature_fingerprint(symbol=symbol, train_window_start=tws, train_window_end=twe)
            elapsed = time.time() - t0
            logger.info("  fingerprint computed in %.1fs  n_obs=%d  n_features=%d",
                        elapsed, fp["feature_dist"]["n_train_obs"],
                        len(fp["feature_dist"]["features"]))
        except Exception as exc:
            logger.warning("  SKIP (fingerprint failed): %s", exc)
            n_err += 1
            continue

        # Try metrics.json
        brier = log_loss = auc = n_obs = None
        if artifact_path:
            artifact_dir = os.path.dirname(artifact_path)
            metrics_path = os.path.join(artifact_dir, "metrics.json")
            if os.path.exists(metrics_path):
                try:
                    with open(metrics_path) as f:
                        m = json.load(f)
                    brier = m.get("brier_score")
                    log_loss = m.get("log_loss")
                    auc = m.get("auc_at_contract_times") or m.get("auc_full")
                    n_obs = m.get("train_size")
                    logger.info("  metrics.json: brier=%.4f  auc=%.4f  n_obs=%s",
                                brier or 0, auc or 0, n_obs)
                except Exception as exc:
                    logger.warning("  metrics.json read failed: %s", exc)
            else:
                logger.info("  metrics.json not found at %s", metrics_path)

        dist_json = json.dumps(fp["feature_dist"])
        computed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        try:
            db.execute(
                """
                INSERT OR REPLACE INTO training_data_snapshots
                  (model_name, feature_names_hash, feature_dist_json,
                   training_brier, training_log_loss, training_auc, n_train_obs, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_name,
                    fp["feature_names_hash"],
                    dist_json,
                    brier,
                    log_loss,
                    auc,
                    fp["feature_dist"].get("n_train_obs") or n_obs,
                    computed_at,
                ),
            )
            db.commit()
            logger.info("  INSERTED snapshot for %s", model_name)
            n_ok += 1
        except Exception as exc:
            logger.warning("  SKIP (DB insert failed): %s", exc)
            n_err += 1

    logger.info(
        "Backfill complete: %d inserted, %d errors, %d total pending (of %d in registry)",
        n_ok, n_err, total_pending, total_pending,
    )


if __name__ == "__main__":
    main()
