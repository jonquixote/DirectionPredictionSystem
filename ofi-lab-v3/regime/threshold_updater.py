"""Auto-refresh regime_thresholds.json from a rolling N-day window of
feature parquets.

Run daily by a cron job and at the end of every retrain pipeline.
On-demand reload happens through ModelRegistry.reload — the live
container's regime_tagger reads the file each prediction.

Also provides refresh_thresholds_db() which writes per-symbol
thresholds_json into the regime_thresholds SQLite table. PaperTrader
uses the JSON file as primary source and falls back to the DB table if
the file is absent — so either path works.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

REGIME_SIGNALS = [
    "vwap_dev_30s_std",
    "mlofi_60s_std",
    "relative_spread",
    "spread_5m_pct",
    "mlofi_momentum",
    "vwap_2m_deviation",
]


def _quantiles(values, qs=(0.25, 0.5, 0.75)):
    if not values:
        return {f"p{int(q*100)}": 0.0 for q in qs}
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    out = {}
    for q in qs:
        idx = int(q * (n - 1))
        out[f"p{int(q*100)}"] = float(sorted_vals[idx])
    return out


def compute_thresholds_from_parquets(
    feature_dir: str, symbol: str, days: int,
) -> dict:
    import pyarrow.parquet as pq

    fdir = Path(feature_dir)
    # Support both flat dir (old layout) and per-symbol subdirs (new layout):
    #   flat:    <feature_dir>/*_BTCUSDT_features.parquet
    #   subdir:  <feature_dir>/BTCUSDT/*_BTCUSDT_features.parquet
    candidates = sorted(fdir.glob(f"*_{symbol}_features.parquet"))
    if not candidates:
        candidates = sorted((fdir / symbol).glob(f"*_{symbol}_features.parquet"))
    if not candidates:
        return {sig: {"p25": 0.0, "p50": 0.0, "p75": 0.0} for sig in REGIME_SIGNALS}
    files = candidates[-days:] if days > 0 else candidates
    accum = {sig: [] for sig in REGIME_SIGNALS}
    for f in files:
        try:
            table = pq.read_table(str(f), columns=REGIME_SIGNALS)
        except Exception:
            continue
        for sig in REGIME_SIGNALS:
            try:
                accum[sig].extend(
                    [v for v in table.column(sig).to_pylist() if v is not None]
                )
            except KeyError:
                pass
    return {sig: _quantiles(vals) for sig, vals in accum.items()}


def refresh_thresholds_file(
    feature_dir: str, symbols: Iterable[str], out_path: str, days: int = 30,
) -> None:
    body = {
        "updated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
    }
    for sym in symbols:
        body[sym] = compute_thresholds_from_parquets(
            feature_dir=feature_dir, symbol=sym, days=days,
        )
    Path(out_path).write_text(json.dumps(body, indent=2))


def refresh_thresholds_db(
    feature_dir: str, symbols: Iterable[str], db_conn, days: int = 30,
) -> dict:
    """Compute quartile thresholds from parquets and upsert into
    regime_thresholds SQLite table.

    Returns the thresholds dict keyed by symbol (same shape as the JSON
    file body, minus the 'updated_at' key) so callers can load it
    directly into memory without a second DB read.
    """
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    result: dict = {}
    for sym in symbols:
        t = compute_thresholds_from_parquets(
            feature_dir=feature_dir, symbol=sym, days=days,
        )
        result[sym] = t
        thresholds_json = json.dumps(t)
        db_conn.execute(
            """
            INSERT INTO regime_thresholds (symbol, thresholds_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                thresholds_json = excluded.thresholds_json,
                updated_at      = excluded.updated_at
            """,
            (sym, thresholds_json, now_str),
        )
        logger.info("regime_thresholds upserted for %s (%d-day window)", sym, days)
    db_conn.commit()
    return result


def load_thresholds_from_db(db_conn) -> dict:
    """Load all rows from regime_thresholds and return a dict keyed by
    symbol suitable for passing to compute_regime()."""
    out: dict = {}
    for row in db_conn.execute("SELECT symbol, thresholds_json FROM regime_thresholds"):
        try:
            out[row[0]] = json.loads(row[1])
        except Exception:
            pass
    return out
