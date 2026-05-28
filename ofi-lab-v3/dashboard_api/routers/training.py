"""Training dispatch router — launch fleet training from the dashboard."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, field_validator

logger = logging.getLogger("training")
router = APIRouter(tags=["training"])

# ── Singleton job state ────────────────────────────────────────
_job_lock = threading.Lock()
_current_job: dict | None = None

FEATURE_DIR = os.environ.get("V3_FEATURE_DIR", "/data/features_v3")
OUTPUT_ROOT = os.environ.get("V3_OUTPUT_ROOT", "/data/models/fleet")
DB_PATH = os.environ.get("V3_DB_PATH", "/data/v3.db")
PROJECT_ROOT = os.environ.get("V3_PROJECT_ROOT", "/home/johnny/ofi-lab-v3")
PYTHON = os.environ.get("V3_PYTHON", "/home/johnny/ofi-lab-v3/.venv/bin/python3.12")
LOG_DIR = Path(os.environ.get("V3_LOG_DIR", "/data/logs"))

ALL_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
ALL_HORIZONS = [60, 180, 300, 600, 900, 1200, 1800]
DEFAULT_TRAIN_DAYS = [90, 180, 330]


# ── Request / Response models ─────────────────────────────────

class TrainingRequest(BaseModel):
    symbols: list[str]
    horizons: list[int]
    train_days: list[int]
    train_end: str  # YYYY-MM-DD
    walk_forward: bool = False
    parallel: int = 2

    @field_validator("symbols")
    @classmethod
    def check_symbols(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("symbols must not be empty")
        for s in v:
            if s not in ALL_SYMBOLS:
                raise ValueError(f"unknown symbol: {s}")
        return v

    @field_validator("horizons")
    @classmethod
    def check_horizons(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("horizons must not be empty")
        return v

    @field_validator("train_days")
    @classmethod
    def check_train_days(cls, v: list[int]) -> list[int]:
        if not v:
            raise ValueError("train_days must not be empty")
        for d in v:
            if d < 1:
                raise ValueError(f"train_days must be positive: {d}")
        return v


class TrainingStatus(BaseModel):
    state: str  # idle | running | done | failed
    job_id: str | None = None
    config: dict | None = None
    started_at: str | None = None
    finished_at: str | None = None
    log_tail: list[str] = []
    exit_code: int | None = None


# ── Background runner ──────────────────────────────────────────

def _run_training(job_id: str, req: TrainingRequest):
    """Run train_fleet.py in a subprocess. Updates _current_job in place."""
    global _current_job
    log_file = LOG_DIR / f"fleet_train_{job_id}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        PYTHON,
        "-m", "scripts.train_fleet",
        "--symbols", ",".join(req.symbols),
        "--horizons", ",".join(str(h) for h in req.horizons),
        "--train-days", ",".join(str(d) for d in req.train_days),
        "--train-end", req.train_end,
        "--feature-dir", FEATURE_DIR,
        "--output-root", OUTPUT_ROOT,
        "--db", DB_PATH,
        "--parallel", str(req.parallel),
        "--state", str(Path(OUTPUT_ROOT) / f"state_{job_id}.json"),
    ]
    if req.walk_forward:
        cmd.append("--walk-forward")

    logger.info("Training dispatch: %s", " ".join(cmd))

    try:
        with open(log_file, "w") as lf:
            proc = subprocess.Popen(
                cmd,
                cwd=PROJECT_ROOT,
                stdout=lf,
                stderr=subprocess.STDOUT,
            )
        with _job_lock:
            if _current_job:
                _current_job["pid"] = proc.pid
                _current_job["log_file"] = str(log_file)

        exit_code = proc.wait()

        with _job_lock:
            if _current_job and _current_job["job_id"] == job_id:
                _current_job["state"] = "done" if exit_code == 0 else "failed"
                _current_job["exit_code"] = exit_code
                _current_job["finished_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        logger.exception("Training job %s failed: %s", job_id, e)
        with _job_lock:
            if _current_job and _current_job["job_id"] == job_id:
                _current_job["state"] = "failed"
                _current_job["exit_code"] = -1
                _current_job["finished_at"] = datetime.now(timezone.utc).isoformat()


# ── Endpoints ──────────────────────────────────────────────────

@router.post("/training/dispatch", status_code=202)
async def dispatch_training(req: TrainingRequest):
    """Launch a fleet training job in the background."""
    global _current_job

    with _job_lock:
        if _current_job and _current_job["state"] == "running":
            raise HTTPException(409, "A training job is already running")

    job_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    with _job_lock:
        _current_job = {
            "job_id": job_id,
            "state": "running",
            "config": req.model_dump(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "exit_code": None,
            "pid": None,
            "log_file": None,
        }

    # Launch in a thread so we don't block the event loop
    t = threading.Thread(target=_run_training, args=(job_id, req), daemon=True)
    t.start()

    return {"job_id": job_id, "state": "running"}


@router.get("/training/status")
async def training_status():
    """Get current training job status + last 50 lines of log."""
    with _job_lock:
        job = _current_job

    if not job:
        return TrainingStatus(state="idle").model_dump()

    # Read log tail
    log_tail: list[str] = []
    log_file = job.get("log_file")
    if log_file and Path(log_file).exists():
        try:
            lines = Path(log_file).read_text().splitlines()
            log_tail = lines[-50:]
        except Exception:
            pass

    return TrainingStatus(
        state=job["state"],
        job_id=job["job_id"],
        config=job.get("config"),
        started_at=job.get("started_at"),
        finished_at=job.get("finished_at"),
        log_tail=log_tail,
        exit_code=job.get("exit_code"),
    ).model_dump()


# ── Retrain queue endpoints (T2-be-surfaces) ───────────────────


def _get_db():
    try:
        from services.db import get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore
    return get_db()


def _fetch_queue(status_filter: str | None, limit: int) -> dict:
    """Read retrain_queue rows from DB. Columns per schema:
       id, cell_key, symbol, horizon_seconds, training_days,
       requested_at, triggered_by, picked_up_at, picked_up_by, notes
    retrain_queue has no status column — derive it from picked_up_at:
       picked_up_at IS NULL  → "pending"
       picked_up_at NOT NULL → "in_flight"  (no completed sentinel in schema)
    """
    conn = _get_db()

    # Build status WHERE clause based on derived status
    where_clause = ""
    params: list = []
    if status_filter == "pending":
        where_clause = " WHERE picked_up_at IS NULL"
    elif status_filter == "in_flight":
        where_clause = " WHERE picked_up_at IS NOT NULL"
    # "completed" is not stored in retrain_queue (rows are consumed/removed upstream),
    # so we just return empty for that filter.

    params.append(min(limit, 500))

    try:
        rows = conn.execute(
            f"""
            SELECT id, cell_key, symbol, horizon_seconds, training_days,
                   requested_at, triggered_by, picked_up_at, picked_up_by, notes
              FROM retrain_queue{where_clause}
             ORDER BY requested_at DESC
             LIMIT ?
            """,
            params,
        ).fetchall()
    except Exception as exc:
        logger.warning("retrain_queue query failed: %s", exc)
        return {
            "queue": [],
            "count": 0,
            "summary": {"pending": 0, "in_flight": 0, "completed_24h": 0},
        }

    queue = []
    for r in rows:
        derived_status = "in_flight" if r["picked_up_at"] else "pending"
        queue.append({
            "id": r["id"],
            "ts_added": r["requested_at"],
            "cell_key": r["cell_key"],
            "symbol": r["symbol"],
            "training_horizon_seconds": r["horizon_seconds"],
            "train_days": r["training_days"],
            "reason": r["triggered_by"],
            "status": derived_status,
            "ts_started": r["picked_up_at"],
            "ts_completed": None,  # not tracked in schema
            "picked_up_by": r["picked_up_by"],
            "notes": r["notes"],
        })

    # Summary counts (always full table, regardless of filter)
    try:
        total_rows = conn.execute(
            "SELECT picked_up_at FROM retrain_queue"
        ).fetchall()
        n_pending = sum(1 for r in total_rows if r["picked_up_at"] is None)
        n_in_flight = sum(1 for r in total_rows if r["picked_up_at"] is not None)
        summary = {
            "pending": n_pending,
            "in_flight": n_in_flight,
            "completed_24h": 0,  # not tracked in schema
        }
    except Exception as exc:
        logger.warning("retrain_queue summary query failed: %s", exc)
        summary = {"pending": 0, "in_flight": 0, "completed_24h": 0}

    return {"queue": queue, "count": len(queue), "summary": summary}


@router.get("/training/queue")
async def get_training_queue(
    status: str | None = Query(
        default=None,
        description="Filter by status: pending|in_flight|completed",
    ),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Return pending retrain jobs from the retrain_queue table."""
    import asyncio as _asyncio
    return await _asyncio.to_thread(_fetch_queue, status, limit)


@router.get("/training/data-status")
async def data_status():
    """Return the date range of available feature data per symbol."""
    result = {}
    feature_dir = Path(FEATURE_DIR)
    for sym in ALL_SYMBOLS:
        sym_dir = feature_dir / sym
        if not sym_dir.exists():
            result[sym] = {"files": 0, "start": None, "end": None}
            continue
        files = sorted(sym_dir.glob("*.parquet"))
        if not files:
            result[sym] = {"files": 0, "start": None, "end": None}
            continue
        start_date = files[0].name.split("_")[0]
        end_date = files[-1].name.split("_")[0]
        result[sym] = {
            "files": len(files),
            "start": start_date,
            "end": end_date,
        }
    return {"symbols": result}
