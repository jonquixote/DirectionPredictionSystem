"""System capacity router.

GET /api/system/capacity
  Returns boundary timing percentiles derived from boundary_metrics table
  (preferred) or journalctl grep (fallback).
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from datetime import datetime, timedelta, timezone
from statistics import median, quantiles

logger = logging.getLogger("dashboard.system")

router_prefix = "/system"

from fastapi import APIRouter

router = APIRouter(prefix="/system", tags=["system"])


def _get_db():
    try:
        from services.db import get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore
    return get_db()


def _has_boundary_metrics_table(conn) -> bool:
    """Return True if the boundary_metrics table exists in the DB."""
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='boundary_metrics'"
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _count_active_models(conn) -> int:
    """COUNT non-baseline paper-active models."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM model_registry "
            "WHERE paper_active=1 AND COALESCE(is_baseline,0)=0"
        ).fetchone()
        return int(row["n"]) if row else 0
    except Exception:
        return 0


def _percentile(values: list[float], p: float) -> float | None:
    """Return the p-th percentile (0–100) of a sorted list, or None if empty."""
    if not values:
        return None
    values = sorted(values)
    n = len(values)
    if n == 1:
        return values[0]
    idx = (p / 100) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    return values[lo] + (values[hi] - values[lo]) * (idx - lo)


def _fetch_from_boundary_metrics_table(conn, window_minutes: int) -> dict:
    """Read from the boundary_metrics table (preferred path)."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        rows = conn.execute(
            """
            SELECT elapsed_ms, rss_end_mb, n_models, ts
              FROM boundary_metrics
             WHERE ts >= ?
             ORDER BY ts DESC
            """,
            (cutoff,),
        ).fetchall()
    except Exception as exc:
        logger.warning("boundary_metrics query failed: %s", exc)
        rows = []

    if not rows:
        return {}

    elapsed_values = [r["elapsed_ms"] for r in rows if r["elapsed_ms"] is not None]
    latest = rows[0]  # ordered DESC, so first = most recent
    return {
        "boundary_p50_ms": _percentile(elapsed_values, 50),
        "boundary_p95_ms": _percentile(elapsed_values, 95),
        "boundary_max_ms": max(elapsed_values) if elapsed_values else None,
        "n_boundaries_sampled": len(elapsed_values),
        "rss_mb_latest": latest["rss_end_mb"] if latest else None,
        "last_boundary_ts": latest["ts"] if latest else None,
        "source": "boundary_metrics_table",
    }


_BOUNDARY_ELAPSED_RE = re.compile(r"elapsed_ms=(\d+(?:\.\d+)?)")
_BOUNDARY_RSS_RE = re.compile(r"rss_end_mb=(\d+(?:\.\d+)?)")
_BOUNDARY_N_MODELS_RE = re.compile(r"n_models=(\d+)")
_BOUNDARY_DONE_RE = re.compile(r"boundary_done")
# Try to grab a timestamp from journal lines: "May 28 02:00:00" or ISO prefix
_BOUNDARY_TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})|([A-Z][a-z]{2}\s+\d+\s+\d{2}:\d{2}:\d{2})"
)


def _fetch_from_journal(window_minutes: int) -> dict:
    """Fallback: grep journalctl for boundary_done lines."""
    try:
        result = subprocess.run(
            [
                "journalctl",
                "-u", "v3-paper-trader",
                "--since", f"{window_minutes} minutes ago",
                "--no-pager",
                "-o", "cat",
            ],
            capture_output=True,
            timeout=5,
            text=True,
        )
        output = result.stdout or ""
    except Exception as exc:
        logger.info("journalctl unavailable: %s", exc)
        return {}

    elapsed_values: list[float] = []
    rss_latest: float | None = None
    n_models_latest: int | None = None
    last_ts: str | None = None

    for line in output.splitlines():
        if "boundary_done" not in line:
            continue
        m_elapsed = _BOUNDARY_ELAPSED_RE.search(line)
        if m_elapsed:
            elapsed_values.append(float(m_elapsed.group(1)))
        m_rss = _BOUNDARY_RSS_RE.search(line)
        if m_rss:
            rss_latest = float(m_rss.group(1))
        m_n = _BOUNDARY_N_MODELS_RE.search(line)
        if m_n:
            n_models_latest = int(m_n.group(1))
        m_ts = _BOUNDARY_TS_RE.search(line)
        if m_ts:
            last_ts = m_ts.group(0)

    if not elapsed_values:
        return {}

    return {
        "boundary_p50_ms": _percentile(elapsed_values, 50),
        "boundary_p95_ms": _percentile(elapsed_values, 95),
        "boundary_max_ms": max(elapsed_values),
        "n_boundaries_sampled": len(elapsed_values),
        "rss_mb_latest": rss_latest,
        "last_boundary_ts": last_ts,
        "source": "journal_grep",
    }


def _fetch_capacity() -> dict:
    """Main logic: try boundary_metrics table, fall back to journal, then unavailable."""
    window_minutes = 60
    conn = _get_db()
    n_models_active = _count_active_models(conn)

    data: dict = {}
    if _has_boundary_metrics_table(conn):
        data = _fetch_from_boundary_metrics_table(conn, window_minutes)

    if not data:
        data = _fetch_from_journal(window_minutes)

    if not data:
        return {
            "boundary_p50_ms": None,
            "boundary_p95_ms": None,
            "boundary_max_ms": None,
            "n_boundaries_sampled": 0,
            "rss_mb_latest": None,
            "n_models_active": n_models_active,
            "window_minutes": window_minutes,
            "last_boundary_ts": None,
            "source": "unavailable",
        }

    return {
        "boundary_p50_ms": data.get("boundary_p50_ms"),
        "boundary_p95_ms": data.get("boundary_p95_ms"),
        "boundary_max_ms": data.get("boundary_max_ms"),
        "n_boundaries_sampled": data.get("n_boundaries_sampled", 0),
        "rss_mb_latest": data.get("rss_mb_latest"),
        "n_models_active": n_models_active,
        "window_minutes": window_minutes,
        "last_boundary_ts": data.get("last_boundary_ts"),
        "source": data.get("source", "unavailable"),
    }


@router.get("/capacity")
async def get_system_capacity():
    """Return boundary timing percentiles and active model count."""
    return await asyncio.to_thread(_fetch_capacity)
