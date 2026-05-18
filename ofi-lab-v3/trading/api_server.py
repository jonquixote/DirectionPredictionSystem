"""
Runtime monitoring API server for paper trading.

Provides comprehensive read-only endpoints for a frontend dashboard.
All data comes from JSONL ledger files (disk) and in-memory state
from the PaperTrader instance. No writes to model/training artifacts.

Designed for efficiency:
  - Aggregates computed server-side (no large payloads)
  - Pagination on feed endpoints
  - Model/symbol filtering to avoid loading unneeded files
  - Live market state from in-memory feature computer
"""

from __future__ import annotations

import json
import os
import time
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from trading.ledger import (
    read_records,
    merge_predictions_with_resolutions,
    merge_trades_with_resolutions,
)

if TYPE_CHECKING:
    from trading.paper_trader import PaperTrader

logger = logging.getLogger("api_server")

# ── Bankroll baselines (v1: hardcoded; can be overridden via /baseline POST)
INITIAL_PAPER_USD = 100.0
INITIAL_KALSHI_CENTS = 10000

# ── Kalshi ledger path (matches kalshi_live_trader writer)
KALSHI_ORDERS_LEDGER = Path("/data/kalshi_orders.jsonl")

# ── Dashboard cutover baseline (set via POST /baseline) ────────
# Persists "go-live" reset point: dashboard hides everything before cutover_ts
# and computes deltas from snapshotted balance/count at that moment.
BASELINE_PATH = Path("/data/dashboard_baseline.json")
KALSHI_ENV_PATH = Path("/data/kalshi.env")


def _iso_to_epoch_ms(iso: str) -> int:
    """ISO 8601 (with optional Z) → epoch ms."""
    if not iso:
        return 0
    from datetime import datetime
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


def _load_baseline() -> dict:
    """Read baseline from disk, fall back to defaults."""
    default = {
        "cutover_ts_ms": 0,
        "baseline_kalshi_cents": INITIAL_KALSHI_CENTS,
        "baseline_paper_trade_count": 0,
        "set_at_iso": None,
    }
    if not BASELINE_PATH.exists():
        return default
    try:
        with BASELINE_PATH.open("r") as f:
            data = json.load(f)
        return {**default, **data}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("baseline read failed: %s — falling back to defaults", e)
        return default


def _save_baseline(baseline: dict) -> None:
    BASELINE_PATH.write_text(json.dumps(baseline, indent=2))


def _persist_kalshi_env(updates: dict) -> None:
    """Update keys in /data/kalshi.env in place. Adds new keys at the end.

    Best-effort: failure is logged but does not raise. Runtime trader state
    has already been updated by the caller; this just makes it survive a
    container restart.
    """
    if not KALSHI_ENV_PATH.exists():
        logger.warning("KALSHI_ENV_PATH missing, skipping persist")
        return
    try:
        lines = KALSHI_ENV_PATH.read_text().splitlines()
        seen = set()
        out = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                out.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                out.append(line)
        for key, value in updates.items():
            if key not in seen:
                out.append(f"{key}={value}")
        KALSHI_ENV_PATH.write_text("\n".join(out) + "\n")
    except OSError as e:
        logger.warning("kalshi env persist failed: %s", e)


def _ms_to_iso(ms: int | None) -> str | None:
    """Convert ms timestamp to ISO 8601 string."""
    if ms is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _get_trader(request) -> "PaperTrader":
    return request.app["trader"]


# ── Auth (bearer token in Authorization header) ────────────────
#
# Single-secret design: DASHBOARD_PASSWORD is both the login password
# AND the bearer token. POST /auth/login {password} returns the token;
# the client sends it as `Authorization: Bearer <token>` on mutating
# requests. Rotate by changing the env var and restarting.

def _expected_token() -> str | None:
    return os.environ.get("DASHBOARD_PASSWORD") or None


def require_auth(handler):
    """Decorator that 401s any request lacking a valid bearer token.

    Auth is bypassed entirely if DASHBOARD_PASSWORD is unset (local dev).
    """
    async def wrapped(request):
        expected = _expected_token()
        if expected is None:
            return await handler(request)
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return web.json_response({"error": "missing bearer token"}, status=401)
        if header[len("Bearer "):].strip() != expected:
            return web.json_response({"error": "invalid token"}, status=401)
        return await handler(request)
    return wrapped


async def post_auth_login(request):
    """Body: {"password": "..."}. Returns {"token": "..."} on match, 401 otherwise.

    If DASHBOARD_PASSWORD is unset, returns 503 to avoid silent open access.
    """
    expected = _expected_token()
    if expected is None:
        return web.json_response(
            {"error": "DASHBOARD_PASSWORD not configured server-side"}, status=503,
        )
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if body.get("password") != expected:
        return web.json_response({"error": "invalid password"}, status=401)
    return web.json_response({"token": expected})


def _get_kalshi(request):
    """Return KalshiLiveTrader instance or None if exchange != kalshi or not connected."""
    trader = _get_trader(request)
    return getattr(trader, "_kalshi_trader", None)


# ── GET /baseline, POST /baseline ──────────────────────────────

async def get_baseline(_request):
    """Return current dashboard cutover baseline."""
    return web.json_response(_load_baseline())


async def _post_baseline_inner(request):
    """Snapshot current state as the new dashboard baseline.

    Body: {"cutover_ts_ms": int}  — use 0 to clear (show all history).
    Snapshots Kalshi balance + paper trade count at this moment.
    """
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    cutover = int(body.get("cutover_ts_ms", 0))

    # Snapshot Kalshi balance (best effort)
    kalshi_cents = INITIAL_KALSHI_CENTS
    kt = _get_kalshi(request)
    if kt is not None and kt._rest is not None:
        try:
            resp = await kt._rest.get_balance()
            kalshi_cents = int(resp.get("balance", INITIAL_KALSHI_CENTS))
        except Exception as e:
            logger.warning("baseline kalshi snapshot failed: %s", e)

    # Snapshot paper trade count (resolved trades only, BTC h300 by default scope)
    trader = _get_trader(request)
    paper_count = trader._trade_count

    from datetime import datetime, timezone
    baseline = {
        "cutover_ts_ms": cutover,
        "baseline_kalshi_cents": kalshi_cents,
        "baseline_paper_trade_count": paper_count,
        "set_at_iso": datetime.now(timezone.utc).isoformat(),
    }
    _save_baseline(baseline)
    logger.warning("DASHBOARD BASELINE RESET: %s", baseline)
    return web.json_response(baseline)


# ── GET /kalshi/status ─────────────────────────────────────────

async def get_kalshi_status(request):
    """Wrap KalshiLiveTrader.status(). Returns 503 if Kalshi not initialized."""
    kt = _get_kalshi(request)
    if kt is None:
        return web.json_response(
            {"error": "kalshi trader not initialized (EXCHANGE != kalshi)"},
            status=503,
        )
    return web.json_response(kt.status())


# ── GET /kalshi/balance ────────────────────────────────────────

async def get_kalshi_balance(request):
    """
    Live USDC balance from Kalshi REST + initial baseline + delta.
    Returns 503 if client not ready, 502 if upstream call fails.
    """
    kt = _get_kalshi(request)
    if kt is None or kt._rest is None:
        return web.json_response(
            {"error": "kalshi client not ready"}, status=503,
        )
    try:
        resp = await kt._rest.get_balance()
    except Exception as e:
        logger.warning("kalshi balance upstream error: %s", e)
        return web.json_response({"error": f"upstream: {e}"}, status=502)

    cents = int(resp.get("balance", 0))
    initial_cents = INITIAL_KALSHI_CENTS
    return web.json_response({
        "balance_usd": cents / 100.0,
        "balance_cents": cents,
        "initial_usd": initial_cents / 100.0,
        "initial_cents": initial_cents,
        "pnl_usd": (cents - initial_cents) / 100.0,
        "pnl_pct": ((cents - initial_cents) / initial_cents * 100.0) if initial_cents else 0.0,
        "portfolio_value": resp.get("portfolio_value"),
        "updated_ts": resp.get("updated_ts"),
    })


# ── POST /kalshi/enable, /kalshi/disable (auth-gated) ─────────

async def _post_kalshi_enable_inner(request):
    kt = _get_kalshi(request)
    if kt is None:
        return web.json_response({"error": "kalshi trader not initialized"}, status=503)
    kt.enable()
    logger.warning("KALSHI ENABLED via API")
    return web.json_response({"enabled": True, "status": kt.status()})


async def _post_kalshi_disable_inner(request):
    kt = _get_kalshi(request)
    if kt is None:
        return web.json_response({"error": "kalshi trader not initialized"}, status=503)
    kt.disable()
    logger.warning("KALSHI DISABLED via API")
    return web.json_response({"enabled": False, "status": kt.status()})


# ── PATCH /kalshi/config (auth-gated) ──────────────────────────

async def _patch_kalshi_config_inner(request):
    """Body: any subset of {kelly_fraction, confidence_gate, bankroll_fraction,
    default_order_type, per_trade_usd_cap}. Validation lives in update_config."""
    kt = _get_kalshi(request)
    if kt is None:
        return web.json_response({"error": "kalshi trader not initialized"}, status=503)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict) or not body:
        return web.json_response({"error": "body must be non-empty object"}, status=400)
    try:
        snapshot = kt.update_config(**body)
    except (ValueError, TypeError) as e:
        return web.json_response({"error": str(e)}, status=400)
    logger.info("KALSHI CONFIG UPDATE via API: %s", body)

    # ── Sync paper trader with Kalshi parameters ───────────────
    trader = _get_trader(request)
    if "confidence_gate" in body:
        trader.filters["confidence_threshold"] = body["confidence_gate"]
    if "kelly_fraction" in body:
        trader.filters["kelly_fraction"] = body["kelly_fraction"]
    if "per_trade_usd_cap" in body:
        cap = body["per_trade_usd_cap"]
        trader.filters["kelly_max_bet_usdc"] = cap if cap is not None else 50.0

    # Persist scalar runtime knobs back to /data/kalshi.env so they survive
    # container restarts. Complex types (allow_list, suppress_hours_utc) are
    # serialized in their env-var format below.
    env_updates: dict[str, str] = {}
    for k, v in body.items():
        if k in ("kelly_fraction", "confidence_gate", "bankroll_fraction",
                 "default_order_type", "per_trade_usd_cap"):
            env_updates[f"KALSHI_{k.upper()}"] = "" if v is None else str(v)
        elif k == "allow_list" and isinstance(v, dict):
            # env format: SYMBOL:DUR,SYMBOL:DUR
            pairs = []
            for sym, durs in v.items():
                for d in durs:
                    pairs.append(f"{sym}:{d}")
            env_updates["KALSHI_LIVE_ALLOW_LIST"] = ",".join(pairs)
        elif k == "suppress_hours_utc" and isinstance(v, list):
            env_updates["KALSHI_SUPPRESS_HOURS_UTC"] = ",".join(str(h) for h in v)
        elif k == "apfs_enabled":
            env_updates["APFS_ENABLED"] = str(v).lower()
        elif k == "apfs_threshold":
            env_updates["APFS_TRADE_THRESHOLD"] = str(v)
    # Also persist synced paper-trader threshold
    if "confidence_gate" in body:
        env_updates["PAPER_CONFIDENCE_THRESHOLD"] = str(body["confidence_gate"])
    if env_updates:
        _persist_kalshi_env(env_updates)

    return web.json_response({"updated": body, "config": snapshot})


# ── GET /kalshi/orders ─────────────────────────────────────────

async def get_kalshi_orders(request):
    """
    Paginated tail of /data/kalshi_orders.jsonl.

    Query params:
      gate_result — filter (PLACED, GATED_KILL_SWITCH, GATED_ALLOW_LIST, etc.)
      side        — "yes" | "no"
      since       — epoch ms; only orders with ts >= since are returned
      limit       — max records (default 50, max 500)
      offset      — pagination offset
    """
    q = request.query
    gate_filter = q.get("gate_result")
    side_filter = q.get("side")
    since_ms = int(q.get("since", 0))
    limit = min(int(q.get("limit", 50)), 500)
    offset = int(q.get("offset", 0))

    if not KALSHI_ORDERS_LEDGER.exists():
        return web.json_response({
            "total": 0, "offset": offset, "limit": limit, "count": 0,
            "orders": [], "ledger_exists": False,
        })

    rows = []
    try:
        with KALSHI_ORDERS_LEDGER.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning("kalshi ledger read failed: %s", e)
        return web.json_response({"error": f"ledger read: {e}"}, status=500)

    filtered = []
    for r in rows:
        if gate_filter and r.get("gate_result") != gate_filter:
            continue
        if side_filter and r.get("side") != side_filter:
            continue
        if since_ms:
            ts_iso = r.get("ts", "")
            try:
                ts_ms = int(_iso_to_epoch_ms(ts_iso))
            except Exception:
                ts_ms = 0
            if ts_ms < since_ms:
                continue
        filtered.append(r)

    # Newest first (rows are appended chronologically)
    filtered.reverse()
    total = len(filtered)
    page = filtered[offset:offset + limit]

    return web.json_response({
        "total": total,
        "offset": offset,
        "limit": limit,
        "count": len(page),
        "orders": page,
        "ledger_exists": True,
    })


# ── GET /config ────────────────────────────────────────────────

async def get_config(request):
    """Return current filter configuration."""
    trader = _get_trader(request)
    return web.json_response(trader.filters)


# ── PATCH /config ──────────────────────────────────────────────

async def patch_config(request):
    """Merge partial JSON into filter config. Only known keys accepted.

    Also persists scalar paper-trader knobs back to /data/kalshi.env so that
    runtime changes survive a container restart. Currently persisted:
      confidence_threshold → PAPER_CONFIDENCE_THRESHOLD

    Confidence gate is kept in sync: when paper confidence_threshold changes,
    the Kalshi confidence_gate is also updated (and vice versa via
    PATCH /kalshi/config).  Both persisted to /data/kalshi.env.
    """
    trader = _get_trader(request)
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    valid_keys = set(trader.filters.keys())
    updated = {}
    for k, v in payload.items():
        if k in valid_keys:
            trader.filters[k] = v
            updated[k] = v
        else:
            return web.json_response({"error": f"unknown key: {k}"}, status=400)

    logger.info("API config update: %s", updated)

    env_updates: dict[str, str] = {}
    if "confidence_threshold" in updated:
        env_updates["PAPER_CONFIDENCE_THRESHOLD"] = str(updated["confidence_threshold"])
        # Sync to Kalshi confidence_gate so both systems use the same threshold
        kt = _get_kalshi(request)
        if kt is not None:
            kt.update_config(confidence_gate=updated["confidence_threshold"])
            env_updates["KALSHI_CONFIDENCE_GATE"] = str(updated["confidence_threshold"])
    if env_updates:
        _persist_kalshi_env(env_updates)

    return web.json_response({"updated": updated, "config": trader.filters})


# ── POST /pause, /resume ──────────────────────────────────────

async def pause(request):
    trader = _get_trader(request)
    trader.filters["pause_trading"] = True
    logger.warning("⚠️  TRADING PAUSED via API")
    return web.json_response({"pause_trading": True})


async def resume(request):
    trader = _get_trader(request)
    trader.filters["pause_trading"] = False
    logger.info("✅ Trading RESUMED via API")
    return web.json_response({"pause_trading": False})


async def _post_reload_meta_inner(request):
    """POST /reload_meta — re-read filter_config_json for all registered models.

    Triggers an immediate reload without waiting for the next 16-boundary cycle.
    Auth-gated (same bearer token as other mutating endpoints).
    """
    trader = _get_trader(request)
    try:
        trader._reload_model_meta()
        return web.json_response({"status": "reloaded"})
    except Exception as e:
        logger.exception("reload_meta_failed: %s", e)
        return web.json_response({"status": "error", "detail": str(e)}, status=500)


async def _post_reload_fleet_inner(request):
    """POST /reload_fleet — schedule a full fleet hot-reload at the next boundary tick.

    Sets trader._sighup_requested=True; the reload runs at the top of the next
    _contract_boundary_loop iteration (deferred, not mid-cycle). This matches the
    SIGHUP handler behaviour and is the safer choice over an immediate reload.

    Auth-gated (same bearer token as other mutating endpoints).
    Returns: {"status": "scheduled"} immediately. The actual reload is async.
    """
    trader = _get_trader(request)
    trader._sighup_requested = True
    logger.info("POST /reload_fleet — fleet hot-reload scheduled via API")
    return web.json_response({"status": "scheduled"})


# ── GET /status ────────────────────────────────────────────────

async def get_status(request):
    """System overview — lightweight, no file reads."""
    trader = _get_trader(request)
    now_ms = int(time.time() * 1000)
    status = {
        "running": trader._running,
        "paused": trader.filters.get("pause_trading", False),
        "predictions_total": trader._prediction_count,
        "trades_total": trader._trade_count,
        "pending_resolutions": len(trader.pending_queue._entries) if hasattr(trader, "pending_queue") else 0,
        "running_pnl": {k: round(v, 2) for k, v in trader._running_pnl.items()},
        "models": list(trader.models.keys()),
        "symbols": {
            "prediction": list(trader.feature_computer.symbols),
            "trade": list(trader.feature_computer.symbols),
        },
        "uptime_seconds": (now_ms - trader._start_time_ms) // 1000,
        "warmup_complete": not trader._is_in_warmup(),
        "filters": trader.filters,
    }
    return web.json_response(status)


# ── GET /market ────────────────────────────────────────────────

async def get_market(request):
    """Live market state from in-memory feature computer."""
    trader = _get_trader(request)
    market = {}
    for symbol, state in trader.feature_computer.states.items():
        mid = state.mid_price_history[-1] if state.mid_price_history else None
        spread = None
        if hasattr(state, "spread_history") and state.spread_history:
            spread = state.spread_history[-1]
        market[symbol] = {
            "mid_price": mid,
            "spread": spread,
            "update_count": state.update_count if hasattr(state, "update_count") else None,
            "mid_price_history_len": len(state.mid_price_history),
        }
    return web.json_response(market)


# ── GET /predictions ───────────────────────────────────────────

async def get_predictions(request):
    """
    Paginated, filterable prediction feed.

    Query params:
      model     — filter by model name (e.g. h300)
      symbol    — filter by symbol (e.g. BTCUSDT)
      status    — "resolved", "pending", or "all" (default: all)
      correct   — "true", "false", or omit for all
      source    — "kalshi" or "paper" (default: "kalshi")
      limit     — max records (default: 50, max: 500)
      offset    — pagination offset (default: 0)
    """
    trader = _get_trader(request)
    q = request.query

    source = q.get("source", "kalshi")
    model_filter = q.get("model")
    symbol_filter = q.get("symbol")
    status_filter = q.get("status", "all")
    correct_filter = q.get("correct")
    since_ms = int(q.get("since", 0))
    limit = min(int(q.get("limit", 50)), 500)
    offset = int(q.get("offset", 0))

    # Determine which models to load
    model_names = [model_filter] if model_filter and model_filter in trader.ledgers else list(trader.ledgers.keys())

    all_merged = []
    for mn in model_names:
        ledger = trader.ledgers[mn]
        raw = read_records(ledger.predictions_path)
        merged = merge_predictions_with_resolutions(raw)
        all_merged.extend(merged)

    # Apply filters
    results = []
    for r in all_merged:
        if since_ms and r.get("ts_model_ran_ms", 0) < since_ms:
            continue
        if symbol_filter and r.get("symbol") != symbol_filter:
            continue
        
        if source == "kalshi":
            # Only include predictions that align with Kalshi 15-minute boundaries
            bms = r.get("ts_contract_open_ms") or r.get("boundary_ms")
            if not bms or (bms // 1000) % 900 != 0:
                continue

        is_resolved = r.get("prediction_correct") is not None
        if status_filter == "resolved" and not is_resolved:
            continue
        if status_filter == "pending" and is_resolved:
            continue
        if correct_filter == "true" and r.get("prediction_correct") is not True:
            continue
        if correct_filter == "false" and r.get("prediction_correct") is not False:
            continue
        results.append(r)

    # Sort newest first by ts_model_ran_ms
    results.sort(key=lambda r: r.get("ts_model_ran_ms", 0), reverse=True)
    total = len(results)

    # Strip features dict to save bandwidth (huge per record)
    page = results[offset:offset + limit]
    for r in page:
        if "features" in r:
            del r["features"]

    return web.json_response({
        "total": total,
        "offset": offset,
        "limit": limit,
        "count": len(page),
        "predictions": page,
    })


# ── GET /trades ────────────────────────────────────────────────

async def get_trades(request):
    """
    Paginated, filterable trade feed.

    Query params:
      source     — "kalshi" or "paper" (default: "kalshi")
      model      — filter by model name
      symbol     — filter by symbol
      duration   — filter by contract_duration_seconds (300, 900)
      status     — "resolved", "pending", "suppressed", or "all"
      result     — "win", "loss", or omit for all
      limit      — max records (default: 50, max: 500)
      offset     — pagination offset
    """
    trader = _get_trader(request)
    q = request.query

    source = q.get("source", "kalshi")
    model_filter = q.get("model")
    symbol_filter = q.get("symbol")
    duration_filter = q.get("duration")
    status_filter = q.get("status", "all")
    result_filter = q.get("result")
    since_ms = int(q.get("since", 0))
    limit = min(int(q.get("limit", 50)), 500)
    offset = int(q.get("offset", 0))

    all_merged = []
    
    if source == "paper":
        model_names = [model_filter] if model_filter and model_filter in trader.ledgers else list(trader.ledgers.keys())
        for mn in model_names:
            ledger = trader.ledgers[mn]
            raw = read_records(ledger.trades_path)
            merged = merge_trades_with_resolutions(raw)
            all_merged.extend(merged)
    else:
        # source == "kalshi"
        # 1. Load resolutions + metadata from paper ledger to resolve Kalshi trades
        res_map = {}
        pred_meta = {}
        for mn in trader.ledgers:
            if model_filter and mn != model_filter: continue
            raw_preds = read_records(trader.ledgers[mn].predictions_path)
            for r in raw_preds:
                if r.get("record_type") == "resolution":
                    pid = r.get("prediction_id")
                    if pid:
                        res_map[pid] = r.get("prediction_correct")
                elif r.get("record_type") == "prediction":
                    sym = r.get("symbol")
                    bms = r.get("ts_contract_open_ms") or r.get("boundary_ms")
                    if sym and bms:
                        pred_meta[(sym, bms)] = {
                            "pid": r.get("prediction_id"),
                            "ts": r.get("ts_model_ran_ms", bms),
                            "model": mn
                        }

        # 2. Read kalshi orders
        if KALSHI_ORDERS_LEDGER.exists():
            try:
                with KALSHI_ORDERS_LEDGER.open("r") as f:
                    for line in f:
                        line = line.strip()
                        if not line: continue
                        try:
                            kr = json.loads(line)
                            if kr.get("gate_result") != "PLACED":
                                continue
                            
                            sym = kr.get("symbol")
                            dur = kr.get("duration_sec")
                            bms = kr.get("boundary_ts", 0) * 1000
                            
                            meta = pred_meta.get((sym, bms), {})
                            pid = meta.get("pid")
                            model_name = meta.get("model", "h300")
                            ts_model = meta.get("ts", bms)
                            
                            # Map schema
                            tr = {
                                "model": model_name,
                                "symbol": sym,
                                "contract_duration_seconds": dur,
                                "ts_model_ran_ms": ts_model,
                                "ts_contract_open_ms": bms,
                                "confidence": kr.get("model_p"),
                                "side": kr.get("side"),
                                "final_yes_price_cents": kr.get("final_yes_price_cents"),
                                "final_contracts": kr.get("final_contracts"),
                                "fee_estimate_usd": kr.get("fee_estimate_usd"),
                            }
                            
                            correct = res_map.get(pid) if pid else None
                            if correct is not None:
                                side = kr.get("side", "yes").lower()
                                # The model direction maps exactly to Kalshi side, so if model is correct, Kalshi wins
                                win = correct
                                tr["trade_result"] = "win" if win else "loss"
                                
                                # Cost depends on side
                                price_cents = kr.get("final_yes_price_cents", 0)
                                if side == "no":
                                    cost_per_contract = (100 - price_cents) / 100.0
                                else:
                                    cost_per_contract = price_cents / 100.0
                                    
                                cost = (kr.get("final_contracts", 0) * cost_per_contract) + kr.get("fee_estimate_usd", 0)
                                
                                if win:
                                    payout = kr.get("final_contracts", 0) * 1.00
                                    tr["net_pnl"] = payout - cost
                                    tr["gross_pnl"] = payout - (cost - kr.get("fee_estimate_usd", 0))
                                else:
                                    tr["net_pnl"] = -cost
                                    tr["gross_pnl"] = -cost + kr.get("fee_estimate_usd", 0)
                            
                            all_merged.append(tr)
                        except json.JSONDecodeError:
                            pass
            except OSError as e:
                logger.warning("kalshi ledger read failed: %s", e)

    results = []
    for r in all_merged:
        if since_ms and r.get("ts_model_ran_ms", 0) < since_ms:
            continue
        if symbol_filter and r.get("symbol") != symbol_filter:
            continue
        if duration_filter and str(r.get("contract_duration_seconds")) != duration_filter:
            continue

        is_suppressed = r.get("suppressed_reason") is not None
        is_resolved = r.get("trade_result") is not None

        if status_filter == "resolved" and not is_resolved:
            continue
        if status_filter == "pending" and (is_resolved or is_suppressed):
            continue
        if status_filter == "suppressed" and not is_suppressed:
            continue
        if result_filter and r.get("trade_result") != result_filter:
            continue
        results.append(r)

    results.sort(key=lambda r: r.get("ts_model_ran_ms", 0), reverse=True)
    total = len(results)
    page = results[offset:offset + limit]

    return web.json_response({
        "total": total,
        "offset": offset,
        "limit": limit,
        "count": len(page),
        "trades": page,
    })


# ── GET /pending ───────────────────────────────────────────────

async def get_pending(request):
    """
    All pending resolutions (trades + predictions) with countdown timers.

    Query params:
      type   — "trades", "predictions", or "all" (default: all)
      model  — filter by model name
    """
    trader = _get_trader(request)
    q = request.query
    type_filter = q.get("type", "all")
    model_filter = q.get("model")
    now_ms = int(time.time() * 1000)

    result = {}

    if type_filter in ("trades", "all"):
        pending_trades = []
        for resolve_at_ms, data in trader._pending_resolutions:
            if model_filter and data.get("model_name") != model_filter:
                continue
            pending_trades.append({
                "resolve_at": _ms_to_iso(resolve_at_ms),
                "resolve_in_seconds": max(0, (resolve_at_ms - now_ms) // 1000),
                "trade_id": data.get("trade_id"),
                "prediction_id": data.get("prediction_id"),
                "model": data.get("model_name"),
                "symbol": data.get("symbol"),
                "pred_direction": data.get("pred_direction"),
                "pred_proba": data.get("pred_proba"),
                "price_at_open": data.get("price_at_open"),
                "duration": data.get("contract_duration_seconds"),
                "stake": data.get("stake"),
                "p_market": data.get("p_market"),
            })
        result["pending_trades"] = pending_trades

    if type_filter in ("predictions", "all"):
        pending_preds = []
        for resolve_at_ms, data in trader._pending_pred_resolutions:
            if model_filter and data.get("model_name") != model_filter:
                continue
            pending_preds.append({
                "resolve_at": _ms_to_iso(resolve_at_ms),
                "resolve_in_seconds": max(0, (resolve_at_ms - now_ms) // 1000),
                "prediction_id": data.get("prediction_id"),
                "model": data.get("model_name"),
                "symbol": data.get("symbol"),
                "pred_direction": data.get("pred_direction"),
                "pred_proba": data.get("pred_proba"),
                "price_at_open": data.get("price_at_open"),
            })
        result["pending_predictions"] = pending_preds

    return web.json_response(result)


# ── GET /performance ───────────────────────────────────────────

async def get_performance(request):
    """
    Aggregated performance stats computed server-side.

    Query params:
      source   — "kalshi" or "paper" (default: "kalshi")
      model    — filter by model name (or 'all')
      hours    — lookback window in hours (default: all time)
    """
    trader = _get_trader(request)
    q = request.query
    source = q.get("source", "kalshi")
    model_filter = q.get("model")
    hours = q.get("hours")
    since_ms_q = q.get("since")

    cutoff_ms = 0
    if since_ms_q:
        cutoff_ms = int(since_ms_q)
    elif hours:
        cutoff_ms = int(time.time() * 1000) - int(float(hours) * 3600 * 1000)

    # Build Kalshi orders list if source is kalshi to map performance
    kalshi_orders_cache = []
    if source == "kalshi":
        if KALSHI_ORDERS_LEDGER.exists():
            try:
                with KALSHI_ORDERS_LEDGER.open("r") as f:
                    for line in f:
                        line = line.strip()
                        if not line: continue
                        try:
                            kr = json.loads(line)
                            if kr.get("gate_result") == "PLACED":
                                kalshi_orders_cache.append(kr)
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass

    model_names = [model_filter] if model_filter and model_filter in trader.ledgers else list(trader.ledgers.keys())

    performance = {}
    for mn in model_names:
        ledger = trader.ledgers[mn]

        # ── Prediction accuracy ───────────────────────
        pred_raw = read_records(ledger.predictions_path)
        pred_merged = merge_predictions_with_resolutions(pred_raw)

        pred_stats = {"total": 0, "resolved": 0, "correct": 0, "incorrect": 0}
        by_symbol_pred = {}

        for r in pred_merged:
            ts = r.get("ts_model_ran_ms", 0)
            if ts < cutoff_ms:
                continue
            if r.get("record_type") == "resolution":
                continue  # skip standalone resolution records
                
            if source == "kalshi":
                bms = r.get("ts_contract_open_ms") or r.get("boundary_ms")
                if not bms or (bms // 1000) % 900 != 0:
                    continue

            pred_stats["total"] += 1
            sym = r.get("symbol", "unknown")
            if sym not in by_symbol_pred:
                by_symbol_pred[sym] = {"total": 0, "resolved": 0, "correct": 0}
            by_symbol_pred[sym]["total"] += 1

            if r.get("prediction_correct") is not None:
                pred_stats["resolved"] += 1
                by_symbol_pred[sym]["resolved"] += 1
                if r["prediction_correct"]:
                    pred_stats["correct"] += 1
                    by_symbol_pred[sym]["correct"] += 1
                else:
                    pred_stats["incorrect"] += 1

        pred_stats["accuracy"] = (
            round(pred_stats["correct"] / pred_stats["resolved"], 4)
            if pred_stats["resolved"] > 0 else None
        )
        for sym in by_symbol_pred:
            s = by_symbol_pred[sym]
            s["accuracy"] = round(s["correct"] / s["resolved"], 4) if s["resolved"] > 0 else None

        # ── Trade performance ─────────────────────────
        # ── Trade performance ─────────────────────────
        trade_stats = {
            "total_entries": 0,
            "suppressed": 0,
            "active": 0,
            "resolved": 0,
            "wins": 0,
            "losses": 0,
            "gross_pnl": 0.0,
            "total_fees": 0.0,
            "net_pnl": 0.0,
            "best_trade": None,
            "worst_trade": None,
            "pnl_list": [],  # for avg/std calc
        }
        by_symbol_trade = {}
        by_duration_trade = {}

        if source == "paper":
            trade_raw = read_records(ledger.trades_path)
            trade_merged = merge_trades_with_resolutions(trade_raw)
            
            for r in trade_merged:
                ts = r.get("ts_model_ran_ms", 0)
                if ts < cutoff_ms: continue

                trade_stats["total_entries"] += 1
                sym = r.get("symbol", "unknown")
                dur = str(r.get("contract_duration_seconds", "?"))

                if r.get("suppressed_reason"):
                    trade_stats["suppressed"] += 1
                    continue

                trade_stats["active"] += 1

                if sym not in by_symbol_trade:
                    by_symbol_trade[sym] = {"active": 0, "resolved": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
                if dur not in by_duration_trade:
                    by_duration_trade[dur] = {"active": 0, "resolved": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}

                by_symbol_trade[sym]["active"] += 1
                by_duration_trade[dur]["active"] += 1

                if r.get("trade_result") is not None:
                    trade_stats["resolved"] += 1
                    net = r.get("net_pnl", 0.0)
                    trade_stats["gross_pnl"] += r.get("gross_pnl", 0.0)
                    trade_stats["total_fees"] += r.get("fee_usd", 0.0)
                    trade_stats["net_pnl"] += net
                    trade_stats["pnl_list"].append(net)
                    
                    if r["trade_result"] == "win":
                        trade_stats["wins"] += 1
                        by_symbol_trade[sym]["wins"] += 1
                        by_duration_trade[dur]["wins"] += 1
                    else:
                        trade_stats["losses"] += 1
                        by_symbol_trade[sym]["losses"] += 1
                        by_duration_trade[dur]["losses"] += 1
                        
                    by_symbol_trade[sym]["resolved"] += 1
                    by_symbol_trade[sym]["net_pnl"] += net
                    by_duration_trade[dur]["resolved"] += 1
                    by_duration_trade[dur]["net_pnl"] += net

                    if trade_stats["best_trade"] is None or net > trade_stats["best_trade"]:
                        trade_stats["best_trade"] = round(net, 4)
                    if trade_stats["worst_trade"] is None or net < trade_stats["worst_trade"]:
                        trade_stats["worst_trade"] = round(net, 4)

        else:
            # source == "kalshi"
            res_map = {}
            for r in pred_merged:
                if r.get("prediction_correct") is not None:
                    bms = r.get("ts_contract_open_ms") or r.get("boundary_ms")
                    if bms: res_map[bms] = r.get("prediction_correct")
            
            for kr in kalshi_orders_cache:
                ts_iso = kr.get("ts", "")
                try: ts_ms = int(_iso_to_epoch_ms(ts_iso))
                except Exception: ts_ms = 0
                if ts_ms < cutoff_ms: continue
                
                trade_stats["total_entries"] += 1
                trade_stats["active"] += 1
                sym = kr.get("symbol", "unknown")
                dur = str(kr.get("duration_sec", "?"))
                bms = kr.get("boundary_ts", 0) * 1000
                
                if sym not in by_symbol_trade:
                    by_symbol_trade[sym] = {"active": 0, "resolved": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
                if dur not in by_duration_trade:
                    by_duration_trade[dur] = {"active": 0, "resolved": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}

                by_symbol_trade[sym]["active"] += 1
                by_duration_trade[dur]["active"] += 1
                
                correct = res_map.get(bms)
                if correct is not None:
                    trade_stats["resolved"] += 1
                    side = kr.get("side", "yes").lower()
                    win = correct if side == "yes" else not correct
                    
                    cost = (kr.get("final_contracts", 0) * kr.get("final_yes_price_cents", 0) / 100.0) + kr.get("fee_estimate_usd", 0)
                    if win:
                        payout = kr.get("final_contracts", 0) * 1.00
                        net = payout - cost
                        trade_stats["wins"] += 1
                        by_symbol_trade[sym]["wins"] += 1
                        by_duration_trade[dur]["wins"] += 1
                    else:
                        net = -cost
                        trade_stats["losses"] += 1
                        by_symbol_trade[sym]["losses"] += 1
                        by_duration_trade[dur]["losses"] += 1
                    
                    trade_stats["net_pnl"] += net
                    trade_stats["gross_pnl"] += net + kr.get("fee_estimate_usd", 0.0)
                    trade_stats["total_fees"] += kr.get("fee_estimate_usd", 0.0)
                    trade_stats["pnl_list"].append(net)
                    
                    by_symbol_trade[sym]["resolved"] += 1
                    by_symbol_trade[sym]["net_pnl"] += net
                    by_duration_trade[dur]["resolved"] += 1
                    by_duration_trade[dur]["net_pnl"] += net

                    if trade_stats["best_trade"] is None or net > trade_stats["best_trade"]:
                        trade_stats["best_trade"] = round(net, 4)
                    if trade_stats["worst_trade"] is None or net < trade_stats["worst_trade"]:
                        trade_stats["worst_trade"] = round(net, 4)

        # Compute derived stats
        pnl_list = trade_stats.pop("pnl_list")
        trade_stats["win_rate"] = (
            round(trade_stats["wins"] / trade_stats["resolved"], 4)
            if trade_stats["resolved"] > 0 else None
        )
        trade_stats["avg_pnl"] = (
            round(sum(pnl_list) / len(pnl_list), 4)
            if pnl_list else None
        )
        trade_stats["gross_pnl"] = round(trade_stats["gross_pnl"], 4)
        trade_stats["total_fees"] = round(trade_stats["total_fees"], 4)
        trade_stats["net_pnl"] = round(trade_stats["net_pnl"], 4)
        trade_stats["pending"] = trade_stats["active"] - trade_stats["resolved"]

        # Round per-bucket P&L
        for d in list(by_symbol_trade.values()) + list(by_duration_trade.values()):
            d["net_pnl"] = round(d["net_pnl"], 4)
            d["win_rate"] = round(d["wins"] / d["resolved"], 4) if d["resolved"] > 0 else None

        performance[mn] = {
            "predictions": pred_stats,
            "predictions_by_symbol": by_symbol_pred,
            "trades": trade_stats,
            "trades_by_symbol": by_symbol_trade,
            "trades_by_duration": by_duration_trade,
        }

    return web.json_response(performance)


# ── GET /performance/pnl_series ────────────────────────────────

async def get_pnl_series(request):
    """
    Cumulative P&L time series for charting.

    Query params:
      model — filter by model name (required)
    """
    trader = _get_trader(request)
    q = request.query
    model_filter = q.get("model")

    if not model_filter or model_filter not in trader.ledgers:
        return web.json_response(
            {"error": "model param required", "available": list(trader.ledgers.keys())},
            status=400,
        )

    ledger = trader.ledgers[model_filter]
    raw = read_records(ledger.trades_path)

    # Extract trade resolutions in chronological order
    resolutions = [r for r in raw if r.get("record_type") == "trade_resolution"]
    resolutions.sort(key=lambda r: r.get("ts_contract_close_ms", 0))

    cum_pnl = 0.0
    series = []
    for r in resolutions:
        cum_pnl += r.get("net_pnl", 0.0)
        series.append({
            "ts": _ms_to_iso(r.get("ts_contract_close_ms")),
            "ts_ms": r.get("ts_contract_close_ms"),
            "net_pnl": round(r.get("net_pnl", 0.0), 4),
            "cumulative_pnl": round(cum_pnl, 4),
            "trade_result": r.get("trade_result"),
            "symbol": r.get("symbol"),
            "duration": r.get("contract_duration_seconds"),
        })

    return web.json_response({
        "model": model_filter,
        "total_resolved": len(series),
        "final_pnl": round(cum_pnl, 4),
        "series": series,
    })


# ── GET /performance/accuracy_series ───────────────────────────

async def get_accuracy_series(request):
    """
    Rolling prediction accuracy series for charting.

    Query params:
      model  — filter by model name (required)
      window — rolling window size (default: 20)
    """
    trader = _get_trader(request)
    q = request.query
    model_filter = q.get("model")
    window = int(q.get("window", 20))

    if not model_filter or model_filter not in trader.ledgers:
        return web.json_response(
            {"error": "model param required", "available": list(trader.ledgers.keys())},
            status=400,
        )

    ledger = trader.ledgers[model_filter]
    raw = read_records(ledger.predictions_path)

    # Get resolutions in chronological order
    resolutions = [r for r in raw if r.get("record_type") == "resolution"]
    resolutions.sort(key=lambda r: r.get("ts_contract_close_ms", 0))

    series = []
    correct_buf = []  # sliding window buffer
    total_correct = 0
    total_count = 0

    for r in resolutions:
        is_correct = r.get("prediction_correct", False)
        correct_buf.append(1 if is_correct else 0)
        total_count += 1
        if is_correct:
            total_correct += 1

        # Rolling window accuracy
        if len(correct_buf) > window:
            correct_buf.pop(0)
        rolling_acc = sum(correct_buf) / len(correct_buf) if correct_buf else None

        series.append({
            "ts": _ms_to_iso(r.get("ts_contract_close_ms")),
            "ts_ms": r.get("ts_contract_close_ms"),
            "prediction_correct": is_correct,
            "symbol": r.get("symbol"),
            "rolling_accuracy": round(rolling_acc, 4) if rolling_acc is not None else None,
            "cumulative_accuracy": round(total_correct / total_count, 4),
        })

    return web.json_response({
        "model": model_filter,
        "window": window,
        "total_resolved": total_count,
        "overall_accuracy": round(total_correct / total_count, 4) if total_count > 0 else None,
        "series": series,
    })


# ── GET /suppression_log ──────────────────────────────────────

async def get_suppression_log(request):
    """
    All suppressed trades grouped by reason. Useful for tuning filters.

    Query params:
      model  — filter by model name
      reason — filter by specific suppression reason
      limit  — max records (default: 100, max: 500)
    """
    trader = _get_trader(request)
    q = request.query
    model_filter = q.get("model")
    reason_filter = q.get("reason")
    limit = min(int(q.get("limit", 100)), 500)

    model_names = [model_filter] if model_filter and model_filter in trader.ledgers else list(trader.ledgers.keys())

    suppressions = []
    reason_counts = {}

    for mn in model_names:
        ledger = trader.ledgers[mn]
        raw = read_records(ledger.trades_path)
        for r in raw:
            if r.get("record_type") != "trade_entry":
                continue
            reason = r.get("suppressed_reason")
            if not reason:
                continue
            if reason_filter and reason != reason_filter:
                continue

            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            suppressions.append(r)

    # Newest first
    suppressions.sort(key=lambda r: r.get("ts_model_ran_ms", 0), reverse=True)

    return web.json_response({
        "total": len(suppressions),
        "reason_counts": reason_counts,
        "suppressions": suppressions[:limit],
    })


# ── Application Factory ───────────────────────────────────────

def create_api_app(trader: "PaperTrader") -> web.Application:
    """Create the aiohttp Application with all monitoring routes."""
    app = web.Application()
    app["trader"] = trader

    # Auth (login is public; mutating routes below are wrapped with require_auth)
    app.router.add_post("/auth/login", post_auth_login)

    # Dashboard cutover baseline
    app.router.add_get("/baseline", get_baseline)
    app.router.add_post("/baseline", require_auth(_post_baseline_inner))

    # Control endpoints (mutating routes auth-gated)
    app.router.add_get("/config", get_config)
    app.router.add_patch("/config", require_auth(patch_config))
    app.router.add_post("/pause", require_auth(pause))
    app.router.add_post("/resume", require_auth(resume))

    # Status & market
    app.router.add_get("/status", get_status)
    app.router.add_get("/market", get_market)

    # Data feeds (paginated)
    app.router.add_get("/predictions", get_predictions)
    app.router.add_get("/trades", get_trades)
    app.router.add_get("/pending", get_pending)

    # Performance (server-side aggregates)
    app.router.add_get("/performance", get_performance)
    app.router.add_get("/performance/pnl_series", get_pnl_series)
    app.router.add_get("/performance/accuracy_series", get_accuracy_series)

    # Filter analysis
    app.router.add_get("/suppression_log", get_suppression_log)

    # Kalshi
    app.router.add_get("/kalshi/status", get_kalshi_status)
    app.router.add_get("/kalshi/balance", get_kalshi_balance)
    app.router.add_get("/kalshi/orders", get_kalshi_orders)
    app.router.add_post("/kalshi/enable", require_auth(_post_kalshi_enable_inner))
    app.router.add_post("/kalshi/disable", require_auth(_post_kalshi_disable_inner))
    app.router.add_patch("/kalshi/config", require_auth(_patch_kalshi_config_inner))

    # Model meta reload (internal — triggers immediate filter_config refresh)
    app.router.add_post("/reload_meta", require_auth(_post_reload_meta_inner))

    # Fleet hot-reload (schedules full registry sync at next boundary tick)
    app.router.add_post("/reload_fleet", require_auth(_post_reload_fleet_inner))

    logger.info("API app created with %d routes", len(app.router.routes()))
    return app


async def start_api_server(trader: "PaperTrader", port: int) -> web.AppRunner:
    """Start the API server and return the runner for cleanup."""
    app = create_api_app(trader)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Runtime API server listening on 0.0.0.0:%d", port)
    return runner
