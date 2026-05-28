"""WebSocket broadcaster — pushes live state every 5s."""
from __future__ import annotations
import asyncio
import logging
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
try:
    from services.auth import verify_ws_token
    from services.live_state import LiveState
except ModuleNotFoundError:
    from dashboard_api.services.auth import verify_ws_token  # type: ignore
    from dashboard_api.services.live_state import LiveState  # type: ignore

logger = logging.getLogger("dashboard.ws")

ws_router = APIRouter()


@ws_router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket, token: str | None = Query(None)):
    """Server-push WebSocket. Sends status, predictions, alerts every 5s."""
    # Auth
    if not verify_ws_token(token):
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await websocket.accept()
    logger.info("WebSocket client connected")

    try:
        while True:
            # Refresh data
            LiveState.refresh()

            # Push status
            snapshot = LiveState.snapshot()
            await websocket.send_json({
                "type": "status",
                "ts": int(time.time() * 1000),
                "payload": snapshot,
            })

            # Push new predictions
            new_preds = LiveState.pop_new_predictions()
            if new_preds:
                # Limit payload size
                await websocket.send_json({
                    "type": "predictions",
                    "ts": int(time.time() * 1000),
                    "payload": new_preds[-50:],  # last 50 max
                })

            # Push new alerts
            new_alerts = LiveState.pop_new_alerts()
            if new_alerts:
                await websocket.send_json({
                    "type": "alerts",
                    "ts": int(time.time() * 1000),
                    "payload": new_alerts,
                })

            # Check for client messages (ping)
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=5.0)
                if data.get("type") == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "ts": int(time.time() * 1000),
                    })
            except asyncio.TimeoutError:
                pass  # Normal — no client message within 5s

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error("WebSocket error: %s", e)
