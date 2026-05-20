"""
polymarket-ofi Dashboard API

FastAPI read-only API serving trading system data to the frontend dashboard.
Reads from JSONL log files and model metadata — never writes to trading data.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from services.auth import verify_credentials
from services.admin_auth import validate_admin_secret
from services.live_state import LiveState
from services.alerts_engine import start_alert_worker
from ws.broadcaster import ws_router
from routers import (
    status, predictions, trades, performance,
    parquet, features, models_registry, alerts, logs,
    models_admin, overlap, kill_switch, audit, admin,
    regime, calibration, baseline, dashboard_settings,
    kalshi_proxy, analysis,
)
try:
    from routers import training as training_router
    _has_training = True
except ImportError:
    _has_training = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-30s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dashboard")


async def _refresh_loop():
    """Background task: refresh LiveState every 5 seconds."""
    while True:
        try:
            LiveState.refresh()
        except Exception as e:
            logger.error("LiveState refresh error: %s", e)
        await asyncio.sleep(5)


# T1.2 — Background pre-compute of slow analysis endpoints. Pre-populates
# the persistent analysis_cache so /api/analysis/full-report warm reads stay
# <50ms. Multi-worker safe: writes go through INSERT OR REPLACE under WAL.
_ANALYSIS_PRECOMPUTE_INTERVAL_S = 300
_ANALYSIS_PRECOMPUTE_BOOT_DELAY_S = 30
_ANALYSIS_FALLBACK_PAIRS = [
    (s, w)
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    for w in (300, 900, 1800)
]


def _analysis_discover_pairs() -> list[tuple[str, int]]:
    """Return distinct (symbol, market_window_seconds) seen in predictions.

    Falls back to the hardcoded 4×3 grid if the query returns empty (fresh DB).
    Best-effort — any failure returns the fallback set.
    """
    try:
        from services.analysis import _get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.analysis import _get_db  # type: ignore
    try:
        db = _get_db()
        rows = db.execute(
            "SELECT DISTINCT symbol, market_window_seconds FROM predictions"
        ).fetchall()
        pairs = [(r[0], int(r[1])) for r in rows if r[0] and r[1] is not None]
        if pairs:
            return pairs
    except Exception as exc:
        logger.warning("analysis precompute: pair discovery failed: %s", exc)
    return list(_ANALYSIS_FALLBACK_PAIRS)


async def _analysis_precompute_loop():
    """Periodically repopulate the analysis_cache so warm reads stay <50ms.

    Runs every ~5 min. Catches per-pair exceptions so a single bad pair
    cannot kill the loop. First run is delayed ~30s after startup to avoid
    competing with cold-boot traffic.
    """
    try:
        await asyncio.sleep(_ANALYSIS_PRECOMPUTE_BOOT_DELAY_S)
    except asyncio.CancelledError:
        return
    try:
        from services.analysis import compute_full_report  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.analysis import compute_full_report  # type: ignore

    while True:
        pairs = _analysis_discover_pairs()
        logger.info(
            "analysis precompute: starting cycle (%d pairs + unfiltered)",
            len(pairs),
        )
        # Run each compute in a worker thread so we don't block the event loop.
        for sym, win in pairs:
            try:
                await asyncio.to_thread(
                    compute_full_report, symbol=sym, market_window=win
                )
            except Exception as exc:
                logger.warning(
                    "analysis precompute: %s/%ds failed: %s", sym, win, exc
                )
        # Unfiltered ALL/ALL view — the most expensive single call.
        try:
            await asyncio.to_thread(
                compute_full_report, symbol=None, market_window=None
            )
        except Exception as exc:
            logger.warning("analysis precompute: unfiltered failed: %s", exc)
        logger.info("analysis precompute: cycle done")
        try:
            await asyncio.sleep(_ANALYSIS_PRECOMPUTE_INTERVAL_S)
        except asyncio.CancelledError:
            return


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Dashboard API starting...")
    # Validate admin secret on startup
    env = os.environ.get("V3_ENV", "dev")
    validate_admin_secret(env=env)
    LiveState.initialize()
    start_alert_worker()
    task = asyncio.create_task(_refresh_loop())
    precompute_task = asyncio.create_task(_analysis_precompute_loop())
    logger.info(
        "Dashboard API ready — background refresh + analysis precompute started"
    )
    yield
    task.cancel()
    precompute_task.cancel()
    logger.info("Dashboard API shutting down")


app = FastAPI(
    title="polymarket-ofi Dashboard API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow frontend dev servers
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:4173",
        "http://localhost:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register REST routers
_routers = [
    status.router,
    predictions.router,
    trades.router,
    performance.router,
    parquet.router,
    features.router,
    models_registry.router,
    alerts.router,
    logs.router,
    models_admin.router,
    overlap.router,
    regime.router,
    calibration.router,
    kill_switch.router,
    audit.router,
    admin.router,
    baseline.router,  # cutover baseline — gates predictions page
    dashboard_settings.router,
    kalshi_proxy.router,  # /kalshi/* → 8080 runtime API with Basic→Bearer translation
    analysis.router,
]
if _has_training:
    _routers.append(training_router.router)
for router in _routers:
    app.include_router(
        router,
        prefix="/api",
        dependencies=[Depends(verify_credentials)],
    )

# WebSocket (auth handled internally via token param)
app.include_router(ws_router)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Never return 500 to frontend — return 503 with sanitized message."""
    import time
    logger.exception("Unhandled exception: %s", exc)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Internal error — please retry",
            "code": "internal_error",
            "timestamp_ms": int(time.time() * 1000),
        },
    )
