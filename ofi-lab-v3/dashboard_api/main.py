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
    kalshi_proxy,
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
    logger.info("Dashboard API ready — background refresh started")
    yield
    task.cancel()
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
