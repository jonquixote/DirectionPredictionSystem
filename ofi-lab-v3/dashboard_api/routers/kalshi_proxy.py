"""Kalshi proxy — forward /api/kalshi/* and /api/config to the internal api_server.

The dashboard_api (FastAPI, port 8765) does not have direct access to the
KalshiLiveTrader or PaperTrader singletons. Those live inside the
api_server.py process on port 8080. Rather than duplicate state, we proxy
the handful of Kalshi-specific and config endpoints transparently.

This keeps the React frontend talking to a single origin (dashboard_api)
while the actual runtime state is managed by the monolithic api_server.
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Request, Response

logger = logging.getLogger("dashboard.kalshi_proxy")

router = APIRouter(tags=["kalshi-proxy"])

# The api_server.py listens on this port inside the container.
_UPSTREAM = os.environ.get("API_SERVER_URL", "http://localhost:8080")


async def _proxy(method: str, path: str, request: Request) -> Response:
    """Forward request to the internal api_server and return its response."""
    url = f"{_UPSTREAM}{path}"
    headers = {}
    # Forward the Authorization header so auth works end-to-end
    auth = request.headers.get("authorization")
    if auth:
        headers["Authorization"] = auth

    body = None
    if method in ("POST", "PATCH", "PUT"):
        body = await request.body()
        headers["Content-Type"] = request.headers.get("content-type", "application/json")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.request(
                method, url, content=body, headers=headers,
            )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type", "application/json"),
        )
    except httpx.ConnectError:
        return Response(
            content='{"error":"api_server not reachable (port 8080)"}',
            status_code=503,
            media_type="application/json",
        )
    except Exception as e:
        logger.exception("kalshi proxy error")
        return Response(
            content=f'{{"error":"proxy error: {e}"}}',
            status_code=502,
            media_type="application/json",
        )


# ── Kalshi endpoints ───────────────────────────────────────────

@router.get("/kalshi/status")
async def kalshi_status(request: Request):
    return await _proxy("GET", "/kalshi/status", request)


@router.get("/kalshi/balance")
async def kalshi_balance(request: Request):
    return await _proxy("GET", "/kalshi/balance", request)


@router.get("/kalshi/orders")
async def kalshi_orders(request: Request):
    qs = str(request.query_params)
    path = f"/kalshi/orders?{qs}" if qs else "/kalshi/orders"
    return await _proxy("GET", path, request)


@router.post("/kalshi/enable")
async def kalshi_enable(request: Request):
    return await _proxy("POST", "/kalshi/enable", request)


@router.post("/kalshi/disable")
async def kalshi_disable(request: Request):
    return await _proxy("POST", "/kalshi/disable", request)


@router.patch("/kalshi/config")
async def kalshi_config(request: Request):
    return await _proxy("PATCH", "/kalshi/config", request)


# ── Paper trader config endpoints ──────────────────────────────

@router.get("/config")
async def get_config(request: Request):
    return await _proxy("GET", "/config", request)


@router.patch("/config")
async def patch_config(request: Request):
    return await _proxy("PATCH", "/config", request)
