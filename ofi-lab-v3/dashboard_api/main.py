"""
polymarket-ofi Dashboard API

FastAPI read-only API serving trading system data to the frontend dashboard.
Reads from JSONL log files and model metadata — never writes to trading data.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

# Imports tolerate two run contexts: production (cwd = ofi-lab-v3/) where
# `services.auth` is on the path, and test/local (cwd = repo root) where the
# same modules live under `dashboard_api.services.auth`. Mirror the same
# try/except pattern the routers use.
try:
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
    try:
        from routers import governance as governance_router
        _has_governance = True
    except ImportError:
        _has_governance = False
    try:
        from routers import system as system_router
        _has_system = True
    except ImportError:
        _has_system = False
    try:
        from routers import models_summary as models_summary_router
        _has_models_summary = True
    except ImportError:
        _has_models_summary = False
except ModuleNotFoundError:
    from dashboard_api.services.auth import verify_credentials  # type: ignore
    from dashboard_api.services.admin_auth import validate_admin_secret  # type: ignore
    from dashboard_api.services.live_state import LiveState  # type: ignore
    from dashboard_api.services.alerts_engine import start_alert_worker  # type: ignore
    from dashboard_api.ws.broadcaster import ws_router  # type: ignore
    from dashboard_api.routers import (  # type: ignore
        status, predictions, trades, performance,
        parquet, features, models_registry, alerts, logs,
        models_admin, overlap, kill_switch, audit, admin,
        regime, calibration, baseline, dashboard_settings,
        kalshi_proxy, analysis,
    )
    try:
        from dashboard_api.routers import training as training_router  # type: ignore
        _has_training = True
    except ImportError:
        _has_training = False
    try:
        from dashboard_api.routers import governance as governance_router  # type: ignore
        _has_governance = True
    except ImportError:
        _has_governance = False
    try:
        from dashboard_api.routers import system as system_router  # type: ignore
        _has_system = True
    except ImportError:
        _has_system = False
    try:
        from dashboard_api.routers import models_summary as models_summary_router  # type: ignore
        _has_models_summary = True
    except ImportError:
        _has_models_summary = False

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


# Phase 5 — scheduled cutover loop. Promotes models whose
# cutover_scheduled_at has arrived to paper_active=1 / cutover_state='cutover'.
# Sibling of the T1.2 analysis precompute loop.
_CUTOVER_SCHEDULER_INTERVAL_S = 60
_CUTOVER_SCHEDULER_BOOT_DELAY_S = 20


def _utc_now_iso_for_cutover() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cutover_get_db():
    """Indirection layer so tests can monkey-patch the connection source.

    Mirrors the same fallback pattern used by _analysis_precompute_loop.
    """
    try:
        from services.db import get_db  # type: ignore
    except ModuleNotFoundError:
        from dashboard_api.services.db import get_db  # type: ignore
    return get_db()


def _run_cutover_scheduler_tick() -> list[str]:
    """One iteration of the cutover scheduler.

    Finds all rows where cutover_state='scheduled' and cutover_scheduled_at
    is in the past (UTC), and promotes them to paper_active=1 +
    cutover_state='cutover'. Returns list of model names that flipped.

    Wrapped in try/except by the caller — but per-row failures are also
    contained here so one bad row cannot block the rest of the batch.
    """
    now_iso = _utc_now_iso_for_cutover()
    promoted: list[str] = []
    conn = _cutover_get_db()
    try:
        rows = conn.execute(
            "SELECT name FROM model_registry "
            "WHERE cutover_state = 'scheduled' "
            "  AND cutover_scheduled_at IS NOT NULL "
            "  AND cutover_scheduled_at <= ? "
            "  AND COALESCE(is_baseline, 0) = 0",
            (now_iso,),
        ).fetchall()
        for row in rows:
            name = row["name"] if hasattr(row, "keys") else row[0]
            try:
                conn.execute(
                    "UPDATE model_registry "
                    "   SET paper_active = 1, "
                    "       cutover_state = 'cutover', "
                    "       cutover_decided_by = 'auto', "
                    "       cutover_decided_at = ?, "
                    "       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    " WHERE name = ? AND cutover_state = 'scheduled'",
                    (now_iso, name),
                )
                conn.execute(
                    "INSERT INTO model_audit "
                    "(model_name, action, by_user, detail) "
                    "VALUES (?, 'cutover_auto', 'auto', ?)",
                    (name, f"auto-promoted at {now_iso}"),
                )
                conn.commit()
                promoted.append(name)
                logger.info(
                    "cutover scheduler: auto-promoted %s (paper_active=1)",
                    name,
                )
            except Exception as row_exc:
                logger.warning(
                    "cutover scheduler: failed to promote %s: %s",
                    name,
                    row_exc,
                )
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if promoted:
        try:
            from dashboard_api.routers.models_admin import _trigger_reload_fleet
        except ModuleNotFoundError:
            from routers.models_admin import _trigger_reload_fleet  # type: ignore
        ok = _trigger_reload_fleet()
        logger.info(
            "cutover scheduler: tick complete, promoted=%d, reload_signal_ok=%s",
            len(promoted), ok,
        )
    return promoted


async def _cutover_scheduler_loop():
    """Background loop: every 60s, scan for due cutovers and promote them.

    Boot delay of 20s ensures we don't race init_schema() on startup.
    """
    try:
        await asyncio.sleep(_CUTOVER_SCHEDULER_BOOT_DELAY_S)
    except asyncio.CancelledError:
        return
    while True:
        try:
            await asyncio.to_thread(_run_cutover_scheduler_tick)
        except Exception as exc:
            logger.warning("cutover scheduler tick failed: %s", exc)
        try:
            await asyncio.sleep(_CUTOVER_SCHEDULER_INTERVAL_S)
        except asyncio.CancelledError:
            return


# Phase 3 — adaptive governance scoring. Hourly tick computes composite
# tier scores for every paper_active model and writes to model_tier_score.
_TIER_SCORING_INTERVAL_S = 3600
_TIER_SCORING_BOOT_DELAY_S = 90


def _run_tier_scoring_tick() -> int:
    """One iteration: score all paper_active models, return row count written."""
    try:
        from services.tier_scorer import compute_all_tier_scores
    except ModuleNotFoundError:
        from dashboard_api.services.tier_scorer import compute_all_tier_scores  # type: ignore
    conn = _cutover_get_db()
    try:
        snaps = compute_all_tier_scores(conn)
        return len(snaps)
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def _tier_scoring_loop():
    """Background loop: every hour, compute + insert composite tier scores."""
    try:
        await asyncio.sleep(_TIER_SCORING_BOOT_DELAY_S)
    except asyncio.CancelledError:
        return
    while True:
        try:
            n = await asyncio.to_thread(_run_tier_scoring_tick)
            logger.info("tier scoring: wrote %d snapshots", n)
        except Exception as exc:
            logger.warning("tier scoring tick failed: %s", exc)
        try:
            await asyncio.sleep(_TIER_SCORING_INTERVAL_S)
        except asyncio.CancelledError:
            return


# Phase 4b — Governance probation evaluator. Every 15 min, check challengers
# whose probation window has closed and promote / extend / retire them.
_GOVERNANCE_PROBATION_INTERVAL_S = 900
_GOVERNANCE_PROBATION_BOOT_DELAY_S = 120

_TIER_KELLY: dict[str, float] = {
    "gold": 1.0,
    "silver": 0.3,
    "watch": 0.0,
    "retired": 0.0,
}
_TIER_RANK: dict[str, int] = {"watch": 0, "silver": 1, "gold": 2, "retired": -1}
_TIER_PROMOTE: dict[str, str] = {"watch": "silver", "silver": "gold"}
_TIER_DEMOTE: dict[str, str] = {"gold": "silver", "silver": "watch"}


def _run_governance_probation_tick() -> int:
    """Evaluate challengers whose probation has ended. Return number acted on.

    Pause kill-switch: V3_GOVERNANCE_ACTIONS_PAUSED=1 short-circuits this tick
    too — same reason as _run_governance_action_tick.
    """
    if os.environ.get("V3_GOVERNANCE_ACTIONS_PAUSED", "0") == "1":
        logger.info("governance probation tick: PAUSED via V3_GOVERNANCE_ACTIONS_PAUSED=1")
        return 0
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    conn = _cutover_get_db()

    try:
        from dashboard_api.routers.models_admin import _trigger_reload_fleet as _trf
    except ModuleNotFoundError:
        try:
            from routers.models_admin import _trigger_reload_fleet as _trf  # type: ignore
        except ModuleNotFoundError:
            _trf = None  # type: ignore

    now_iso = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    acted = 0
    reload_needed = False

    try:
        challengers = conn.execute(
            "SELECT name, symbol, training_horizon_seconds, train_days, "
            "parent_model_name, probation_start_at, probation_end_at, "
            "COALESCE(primary_market_window_seconds, training_horizon_seconds) AS primary_window "
            "FROM model_registry "
            "WHERE tier = 'watch' "
            "  AND probation_end_at IS NOT NULL "
            "  AND probation_end_at <= ? "
            "  AND COALESCE(is_baseline, 0) = 0",
            (now_iso,),
        ).fetchall()

        for ch in challengers:
            name = ch["name"]
            symbol = ch["symbol"]
            horizon = ch["training_horizon_seconds"]
            train_days = ch["train_days"]
            parent = ch["parent_model_name"]
            win_start = ch["probation_start_at"] or "1970-01-01T00:00:00Z"
            win_end = ch["probation_end_at"] or now_iso
            primary_window = int(ch["primary_window"]) if ch["primary_window"] else 900
            cell_key = f"{symbol}_{horizon}_{train_days}"

            # Challenger avg composite over probation window
            ch_row = conn.execute(
                "SELECT AVG(composite_score) AS avg_score, COUNT(*) AS n "
                "FROM model_tier_score "
                "WHERE model_name = ? AND ts BETWEEN ? AND ?",
                (name, win_start, win_end),
            ).fetchone()
            ch_avg = float(ch_row["avg_score"]) if ch_row and ch_row["avg_score"] is not None else None
            ch_n = int(ch_row["n"]) if ch_row else 0

            # Incumbent avg composite over same window
            inc_avg: float | None = None
            if parent:
                inc_row = conn.execute(
                    "SELECT AVG(composite_score) AS avg_score "
                    "FROM model_tier_score "
                    "WHERE model_name = ? AND ts BETWEEN ? AND ?",
                    (parent, win_start, win_end),
                ).fetchone()
                inc_avg = float(inc_row["avg_score"]) if inc_row and inc_row["avg_score"] is not None else None

            reason_data: dict = {
                "challenger_score": ch_avg,
                "challenger_n": ch_n,
                "incumbent_score": inc_avg,
                "window_start": win_start,
                "window_end": win_end,
            }

            def _write_gov_action(action: str, from_tier: str, to_tier: str, model: str) -> None:
                reason_data["ratio"] = (
                    (ch_avg / inc_avg) if inc_avg and inc_avg != 0 and ch_avg is not None else None
                )
                conn.execute(
                    "INSERT INTO governance_actions "
                    "(ts, model_name, action, from_tier, to_tier, triggered_by, reason_json, cell_key) "
                    "VALUES (?, ?, ?, ?, ?, 'auto:probation_evaluator', ?, ?)",
                    (now_iso, model, action, from_tier, to_tier, _json.dumps(reason_data), cell_key),
                )

            def _update_mwt_primary(model: str, model_primary_window: int, new_tier: str) -> None:
                """Phase 57: update model_window_tier for primary window only."""
                conn.execute(
                    "UPDATE model_window_tier SET tier = ?, kelly_multiplier = ?, "
                    "tier_assigned_at = ?, tier_assigned_by = 'auto:probation_evaluator' "
                    "WHERE model_name = ? AND market_window_seconds = ?",
                    (new_tier, _TIER_KELLY[new_tier], now_iso, model, model_primary_window),
                )
                # Also update model_registry.tier_assigned_at as breadcrumb (not tier/kelly)
                conn.execute(
                    "UPDATE model_registry SET tier_assigned_at = ? WHERE name = ?",
                    (now_iso, model),
                )

            # No scores at all — challenger never scored. Retire immediately.
            if ch_avg is None or ch_n == 0:
                _update_mwt_primary(name, primary_window, "retired")
                _write_gov_action("retire", "watch", "retired", name)
                conn.commit()
                reload_needed = True
                acted += 1
                logger.info("governance: retired %s primary_window=%s (no scores during probation)", name, primary_window)
                continue

            # No incumbent — direct promote to silver if positive composite
            if parent is None or inc_avg is None:
                if ch_avg > 0:
                    new_tier = "silver"
                    _update_mwt_primary(name, primary_window, new_tier)
                    _write_gov_action("promote", "watch", new_tier, name)
                    conn.execute(
                        "INSERT INTO cell_governance(cell_key, symbol, horizon_seconds, training_days, "
                        "incumbent_model_name, last_promotion_at) VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(cell_key) DO UPDATE SET "
                        "incumbent_model_name = excluded.incumbent_model_name, "
                        "last_promotion_at = excluded.last_promotion_at",
                        (cell_key, symbol, horizon, train_days, name, now_iso),
                    )
                    conn.commit()
                    reload_needed = True
                    acted += 1
                    logger.info("governance: promoted %s watch→silver primary_window=%s (no incumbent, score=%.4f)", name, primary_window, ch_avg)
                else:
                    _update_mwt_primary(name, primary_window, "retired")
                    _write_gov_action("retire", "watch", "retired", name)
                    conn.commit()
                    reload_needed = True
                    acted += 1
                    logger.info("governance: retired %s primary_window=%s (no incumbent, negative score=%.4f)", name, primary_window, ch_avg)
                continue

            # Compute ratio for decision matrix
            ratio = ch_avg / inc_avg if inc_avg != 0 else float("inf")
            reason_data["ratio"] = ratio

            if ratio >= 1.05:
                # Challenger beats incumbent by >5% — promote challenger, demote incumbent
                # Get incumbent's primary window
                inc_pwin_row = conn.execute(
                    "SELECT COALESCE(primary_market_window_seconds, training_horizon_seconds) AS pw "
                    "FROM model_registry WHERE name = ?", (parent,)
                ).fetchone()
                inc_primary_window = int(inc_pwin_row["pw"]) if inc_pwin_row and inc_pwin_row["pw"] else 900
                inc_mwt_row = conn.execute(
                    "SELECT tier FROM model_window_tier WHERE model_name = ? AND market_window_seconds = ?",
                    (parent, inc_primary_window),
                ).fetchone()
                inc_tier = inc_mwt_row["tier"] if inc_mwt_row else "gold"

                new_ch_tier = _TIER_PROMOTE.get(inc_tier, "silver")
                new_inc_tier = _TIER_DEMOTE.get(inc_tier, "watch")

                _update_mwt_primary(name, primary_window, new_ch_tier)
                _write_gov_action("promote", "watch", new_ch_tier, name)
                _update_mwt_primary(parent, inc_primary_window, new_inc_tier)
                conn.execute(
                    "INSERT INTO governance_actions "
                    "(ts, model_name, action, from_tier, to_tier, triggered_by, reason_json, cell_key) "
                    "VALUES (?, ?, 'demote', ?, ?, 'auto:probation_evaluator', ?, ?)",
                    (now_iso, parent, inc_tier, new_inc_tier, _json.dumps(reason_data), cell_key),
                )
                conn.execute(
                    "INSERT INTO cell_governance(cell_key, symbol, horizon_seconds, training_days, "
                    "incumbent_model_name, last_promotion_at) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(cell_key) DO UPDATE SET "
                    "incumbent_model_name = excluded.incumbent_model_name, "
                    "last_promotion_at = excluded.last_promotion_at",
                    (cell_key, symbol, horizon, train_days, name, now_iso),
                )
                conn.commit()
                reload_needed = True
                acted += 1
                logger.info(
                    "governance: promoted %s watch→%s (ratio=%.3f), demoted incumbent %s→%s",
                    name, new_ch_tier, ratio, parent, new_inc_tier,
                )

            elif ratio >= 0.95:
                # Within ±5% — extend probation by 48h; retire after 2 extensions
                notes_row = conn.execute(
                    "SELECT notes FROM cell_governance WHERE cell_key = ?", (cell_key,)
                ).fetchone()
                notes_str = notes_row["notes"] if notes_row and notes_row["notes"] else "{}"
                try:
                    notes = _json.loads(notes_str)
                except Exception:
                    notes = {}
                ext_count = int(notes.get("extension_count", 0)) + 1
                notes["extension_count"] = ext_count

                if ext_count > 2:
                    _update_mwt_primary(name, primary_window, "retired")
                    _write_gov_action("retire", "watch", "retired", name)
                    conn.commit()
                    reload_needed = True
                    acted += 1
                    logger.info("governance: retired %s (max extensions exceeded, ratio=%.3f)", name, ratio)
                else:
                    new_end = (
                        datetime.now(timezone.utc) + timedelta(hours=48)
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    conn.execute(
                        "UPDATE model_registry SET probation_end_at = ? WHERE name = ?",
                        (new_end, name),
                    )
                    conn.execute(
                        "INSERT INTO cell_governance(cell_key, symbol, horizon_seconds, training_days, notes) "
                        "VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(cell_key) DO UPDATE SET notes = excluded.notes",
                        (cell_key, symbol, horizon, train_days, _json.dumps(notes)),
                    )
                    _write_gov_action("extend_probation", "watch", "watch", name)
                    conn.commit()
                    acted += 1
                    logger.info("governance: extended probation for %s (ext_count=%d, ratio=%.3f)", name, ext_count, ratio)

            else:
                # ratio < 0.95 — challenger underperforms — retire
                _update_mwt_primary(name, primary_window, "retired")
                _write_gov_action("retire", "watch", "retired", name)
                conn.commit()
                reload_needed = True
                acted += 1
                logger.info("governance: retired %s (underperformed incumbent, ratio=%.3f)", name, ratio)

    except Exception as exc:
        logger.warning("governance probation tick error: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if reload_needed and _trf is not None:
        _trf()
        logger.info("governance probation: reloaded fleet after %d tier changes", acted)
    return acted


async def _governance_probation_loop():
    """Background loop: every 15 min, evaluate challenger probations."""
    try:
        await asyncio.sleep(_GOVERNANCE_PROBATION_BOOT_DELAY_S)
    except asyncio.CancelledError:
        return
    while True:
        try:
            n = await asyncio.to_thread(_run_governance_probation_tick)
            if n:
                logger.info("governance probation: acted on %d challengers", n)
        except Exception as exc:
            logger.warning("governance probation loop error: %s", exc)
        try:
            await asyncio.sleep(_GOVERNANCE_PROBATION_INTERVAL_S)
        except asyncio.CancelledError:
            return


# Phase 6a — Auto-demotion + retire + retrain_queue. Every 30 min.
_GOVERNANCE_ACTION_INTERVAL_S = 1800
_GOVERNANCE_ACTION_BOOT_DELAY_S = 60


def _run_governance_action_tick() -> int:
    """Scan decay_evaluations for decay alerts; demote/retire models. Return count acted.

    Pause kill-switch: V3_GOVERNANCE_ACTIONS_PAUSED=1 short-circuits this tick.
    Used while we land per-fleet baselines / grace window so we don't keep
    demoting models on contaminated cross-fleet baselines.
    """
    if os.environ.get("V3_GOVERNANCE_ACTIONS_PAUSED", "0") == "1":
        logger.info("governance action tick: PAUSED via V3_GOVERNANCE_ACTIONS_PAUSED=1")
        return 0
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    conn = _cutover_get_db()

    try:
        from dashboard_api.routers.models_admin import _trigger_reload_fleet as _trf
    except ModuleNotFoundError:
        try:
            from routers.models_admin import _trigger_reload_fleet as _trf  # type: ignore
        except ModuleNotFoundError:
            _trf = None  # type: ignore

    now_iso = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    acted = 0
    reload_needed = False

    try:
        # Track names acted on this tick to avoid double-processing (e.g., a model
        # demoted gold→silver in the same tick should not also be caught by silver→watch).
        _acted_this_tick: set[str] = set()

        # Find gold models with ≥3 triggered decay evaluations in last 6h.
        # Filters applied:
        #   - Stale-alert: ts > tier_assigned_at (fresh accounting per promotion)
        #   - calibration_drift excluded (no grace; fires on calibrating fleet)
        #   - PRIMARY WINDOW ONLY (Patch B / Phase 57): check decay at primary window.
        #     Phase 57: JOIN model_window_tier to find gold rows at primary window.
        gold_decay = conn.execute(
            "SELECT mr.name, mr.symbol, mr.training_horizon_seconds, mr.train_days, "
            "mr.primary_market_window_seconds "
            "FROM model_registry mr "
            "JOIN model_window_tier mwt "
            "  ON mwt.model_name = mr.name "
            " AND mwt.market_window_seconds = mr.primary_market_window_seconds "
            "WHERE mwt.tier = 'gold' AND COALESCE(mr.is_baseline, 0) = 0 "
            "  AND (SELECT COUNT(*) FROM decay_evaluations de "
            "       WHERE de.model_name = mr.name AND de.triggered = 1 "
            "         AND de.eval_type != 'calibration_drift' "
            "         AND de.market_window_seconds = mr.primary_market_window_seconds "
            "         AND de.ts >= datetime('now', '-6 hour') "
            "         AND de.ts > COALESCE(mwt.tier_assigned_at, '1970-01-01')) >= 3"
        ).fetchall()

        for row in gold_decay:
            name = row["name"]
            symbol = row["symbol"]
            horizon = row["training_horizon_seconds"]
            train_days = row["train_days"]
            primary_window = row["primary_market_window_seconds"] or 900
            cell_key = f"{symbol}_{horizon}_{train_days}_w{primary_window}"
            new_tier = "silver"
            new_kelly = _TIER_KELLY[new_tier]
            # Phase 57: UPDATE model_window_tier (primary window only), not model_registry.tier
            conn.execute(
                "UPDATE model_window_tier SET tier = ?, kelly_multiplier = ?, "
                "tier_assigned_at = ?, tier_assigned_by = 'auto:decay_monitor' "
                "WHERE model_name = ? AND market_window_seconds = ?",
                (new_tier, new_kelly, now_iso, name, primary_window),
            )
            conn.execute(
                "INSERT INTO governance_actions "
                "(ts, model_name, action, from_tier, to_tier, triggered_by, reason_json, cell_key) "
                "VALUES (?, ?, 'demote', 'gold', 'silver', 'auto:decay_monitor', ?, ?)",
                (now_iso, name, _json.dumps({"reason": "decay_alerts_6h_gte3"}), cell_key),
            )
            conn.commit()
            reload_needed = True
            acted += 1
            _acted_this_tick.add(name)
            logger.info("governance action: demoted %s gold→silver primary_window=%s (decay alerts in 6h)", name, primary_window)
            _maybe_insert_retrain_queue(conn, cell_key, symbol, horizon, train_days, now_iso)

        # Find silver models with ≥3 triggered decay evaluations in last 12h
        # (exclude models already acted on this tick to prevent double-demotion).
        # Phase 57: JOIN model_window_tier for silver check at primary window.
        silver_decay = conn.execute(
            "SELECT mr.name, mr.symbol, mr.training_horizon_seconds, mr.train_days, "
            "mr.primary_market_window_seconds "
            "FROM model_registry mr "
            "JOIN model_window_tier mwt "
            "  ON mwt.model_name = mr.name "
            " AND mwt.market_window_seconds = mr.primary_market_window_seconds "
            "WHERE mwt.tier = 'silver' AND COALESCE(mr.is_baseline, 0) = 0 "
            "  AND (SELECT COUNT(*) FROM decay_evaluations de "
            "       WHERE de.model_name = mr.name AND de.triggered = 1 "
            "         AND de.eval_type != 'calibration_drift' "
            "         AND de.market_window_seconds = mr.primary_market_window_seconds "
            "         AND de.ts >= datetime('now', '-12 hour') "
            "         AND de.ts > COALESCE(mwt.tier_assigned_at, '1970-01-01')) >= 3"
        ).fetchall()

        for row in silver_decay:
            if row["name"] in _acted_this_tick:
                continue
            name = row["name"]
            symbol = row["symbol"]
            horizon = row["training_horizon_seconds"]
            train_days = row["train_days"]
            primary_window = row["primary_market_window_seconds"] or 900
            cell_key = f"{symbol}_{horizon}_{train_days}_w{primary_window}"
            new_tier = "watch"
            new_kelly = _TIER_KELLY[new_tier]
            # Phase 57: UPDATE model_window_tier (primary window only)
            conn.execute(
                "UPDATE model_window_tier SET tier = ?, kelly_multiplier = ?, "
                "tier_assigned_at = ?, tier_assigned_by = 'auto:decay_monitor' "
                "WHERE model_name = ? AND market_window_seconds = ?",
                (new_tier, new_kelly, now_iso, name, primary_window),
            )
            conn.execute(
                "INSERT INTO governance_actions "
                "(ts, model_name, action, from_tier, to_tier, triggered_by, reason_json, cell_key) "
                "VALUES (?, ?, 'demote', 'silver', 'watch', 'auto:decay_monitor', ?, ?)",
                (now_iso, name, _json.dumps({"reason": "decay_alerts_12h_gte3"}), cell_key),
            )
            conn.commit()
            reload_needed = True
            acted += 1
            logger.info("governance action: demoted %s silver→watch primary_window=%s (decay alerts in 12h)", name, primary_window)
            _maybe_insert_retrain_queue(conn, cell_key, symbol, horizon, train_days, now_iso)

        # Find watch models to retire: sustained negative rwev for 7 days, sample_count >= 100.
        # Phase 57: tier lives in model_window_tier (per-window). Filter on the primary
        # window's tier, mirroring the gold/silver demote queries at lines ~620/~668.
        # Legacy mr.tier='watch' filter missed auto-demoted models (only manual promote_model
        # writes mr.tier), so watch-aged models never reached retire.
        watch_retire = conn.execute(
            "SELECT mr.name, mr.symbol, mr.training_horizon_seconds, mr.train_days "
            "FROM model_registry mr "
            "JOIN model_window_tier mwt "
            "  ON mwt.model_name = mr.name "
            " AND mwt.market_window_seconds = COALESCE(mr.primary_market_window_seconds, mr.training_horizon_seconds) "
            "WHERE mwt.tier = 'watch' AND COALESCE(mr.is_baseline, 0) = 0 "
            "  AND mr.paper_active = 1"
        ).fetchall()

        for row in watch_retire:
            name = row["name"]
            symbol = row["symbol"]
            horizon = row["training_horizon_seconds"]
            train_days = row["train_days"]
            cell_key = f"{symbol}_{horizon}_{train_days}"

            # Latest decay metrics entry
            latest = conn.execute(
                "SELECT recency_weighted_ev, sample_count FROM decay_metrics "
                "WHERE model_name = ? ORDER BY ts DESC LIMIT 1",
                (name,),
            ).fetchone()
            if not latest:
                continue
            if latest["recency_weighted_ev"] is None or latest["recency_weighted_ev"] >= 0:
                continue
            if (latest["sample_count"] or 0) < 100:
                continue

            # All 7d decay_metrics rows must have negative rwev
            neg_check = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN recency_weighted_ev < 0 THEN 1 ELSE 0 END) AS neg_count "
                "FROM decay_metrics "
                "WHERE model_name = ? AND ts >= datetime('now', '-7 day')",
                (name,),
            ).fetchone()
            if not neg_check or neg_check["total"] == 0:
                continue
            if neg_check["neg_count"] < neg_check["total"]:
                continue

            # All conditions met — retire. Terminal state: kill predictions across
            # ALL three windows (300/900/1800), not just the primary window like
            # gold→silver and silver→watch demotes do.
            conn.execute(
                "UPDATE model_registry SET tier = 'retired', kelly_multiplier = 0.0, "
                "paper_active = 0, "
                "tier_assigned_at = ?, tier_assigned_by = 'auto:decay_monitor' "
                "WHERE name = ?",
                (now_iso, name),
            )
            conn.execute(
                "UPDATE model_window_tier SET tier = 'retired', kelly_multiplier = 0.0, "
                "tier_assigned_at = ?, tier_assigned_by = 'auto:decay_monitor' "
                "WHERE model_name = ?",
                (now_iso, name),
            )
            conn.execute(
                "INSERT INTO governance_actions "
                "(ts, model_name, action, from_tier, to_tier, triggered_by, reason_json, cell_key) "
                "VALUES (?, ?, 'retire', 'watch', 'retired', 'auto:decay_monitor', ?, ?)",
                (now_iso, name, _json.dumps({
                    "reason": "sustained_negative_rwev_7d",
                    "latest_rwev": latest["recency_weighted_ev"],
                    "sample_count": latest["sample_count"],
                }), cell_key),
            )
            conn.commit()
            reload_needed = True
            acted += 1
            logger.info("governance action: retired %s (sustained negative rwev, 7d)", name)
            _maybe_insert_retrain_queue(conn, cell_key, symbol, horizon, train_days, now_iso)

    except Exception as exc:
        logger.warning("governance action tick error: %s", exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if reload_needed and _trf is not None:
        _trf()
        logger.info("governance action: reloaded fleet after %d changes", acted)
    return acted


def _maybe_insert_retrain_queue(
    conn,
    cell_key: str,
    symbol: str,
    horizon: int,
    train_days: int,
    now_iso: str,
) -> None:
    """Insert into retrain_queue if no gold/silver model remains in this cell
    and no row exists for this cell in the last 24h.
    """
    import json as _json
    surviving = conn.execute(
        "SELECT COUNT(*) FROM model_registry "
        "WHERE symbol = ? AND training_horizon_seconds = ? AND train_days = ? "
        "  AND tier IN ('gold', 'silver')",
        (symbol, horizon, train_days),
    ).fetchone()[0]
    if surviving > 0:
        return
    # Check if already queued in last 24h
    existing = conn.execute(
        "SELECT COUNT(*) FROM retrain_queue "
        "WHERE cell_key = ? AND requested_at >= datetime('now', '-24 hour')",
        (cell_key,),
    ).fetchone()[0]
    if existing > 0:
        return
    try:
        conn.execute(
            "INSERT INTO retrain_queue(cell_key, symbol, horizon_seconds, training_days, "
            "requested_at, triggered_by) VALUES (?, ?, ?, ?, ?, ?)",
            (cell_key, symbol, horizon, train_days, now_iso, "auto:no_incumbent"),
        )
        conn.commit()
        logger.info("governance: queued retrain for cell %s (no gold/silver incumbent)", cell_key)
    except Exception as e:
        logger.warning("retrain_queue insert failed for %s: %s", cell_key, e)


async def _governance_action_loop():
    """Background loop: every 30 min, run decay-based demotion/retire logic."""
    try:
        await asyncio.sleep(_GOVERNANCE_ACTION_BOOT_DELAY_S)
    except asyncio.CancelledError:
        return
    while True:
        try:
            n = await asyncio.to_thread(_run_governance_action_tick)
            if n:
                logger.info("governance action: acted on %d models", n)
        except Exception as exc:
            logger.warning("governance action loop error: %s", exc)
        try:
            await asyncio.sleep(_GOVERNANCE_ACTION_INTERVAL_S)
        except asyncio.CancelledError:
            return


# T2 — Daily rollup loop. Every 10 min, upsert aggregate rows for today and
# yesterday into predictions_daily_rollup. The fast leaderboard path
# reads from this table instead of scanning the full predictions table.
#
# PnL is NE_t (realized net per $1 stake) computed inline in SQL:
#   up+correct  : 1 - p_market - 0.009
#   up+wrong    : -(p_market + 0.009)
#   down+correct: p_market - 0.009
#   down+wrong  : -(1 - p_market + 0.009)
# Rows where p_market IS NULL contribute 0 to pnl sums.
_ROLLUP_INTERVAL_S = 600
_ROLLUP_BOOT_DELAY_S = 45

_ROLLUP_UPSERT_SQL = """
INSERT INTO predictions_daily_rollup (
  model_name, symbol, market_window_seconds, date_utc,
  n, n_correct, sum_pnl, sum_pnl_sq, sum_p_calibrated, sum_brier_terms,
  n_resolved_trades, updated_at
)
SELECT
  model_name,
  symbol,
  market_window_seconds,
  ? AS date_utc,
  COUNT(*),
  SUM(CASE WHEN prediction_correct THEN 1 ELSE 0 END),
  SUM(
    CASE
      WHEN p_market IS NULL THEN 0.0
      WHEN pred_direction = 'up' AND prediction_correct THEN  1.0 - p_market - 0.009
      WHEN pred_direction = 'up'                         THEN -(p_market + 0.009)
      WHEN pred_direction = 'down' AND prediction_correct THEN  p_market - 0.009
      ELSE                                                    -(1.0 - p_market + 0.009)
    END
  ),
  SUM(
    CASE
      WHEN p_market IS NULL THEN 0.0
      WHEN pred_direction = 'up' AND prediction_correct THEN  (1.0 - p_market - 0.009) * (1.0 - p_market - 0.009)
      WHEN pred_direction = 'up'                         THEN  (p_market + 0.009) * (p_market + 0.009)
      WHEN pred_direction = 'down' AND prediction_correct THEN  (p_market - 0.009) * (p_market - 0.009)
      ELSE                                                    (1.0 - p_market + 0.009) * (1.0 - p_market + 0.009)
    END
  ),
  SUM(COALESCE(pred_proba_calibrated, 0.5)),
  SUM(
    (CASE WHEN pred_proba_calibrated >= 0.5
          THEN pred_proba_calibrated
          ELSE 1.0 - pred_proba_calibrated END
     - CAST(prediction_correct AS REAL))
    *
    (CASE WHEN pred_proba_calibrated >= 0.5
          THEN pred_proba_calibrated
          ELSE 1.0 - pred_proba_calibrated END
     - CAST(prediction_correct AS REAL))
  ),
  SUM(CASE WHEN p_market IS NOT NULL THEN 1 ELSE 0 END),
  ? AS updated_at
FROM predictions
WHERE resolved = 1 AND warmup = 0
  AND prediction_correct IS NOT NULL
  AND strftime('%Y-%m-%d', datetime(ts_contract_open_ms / 1000, 'unixepoch')) = ?
GROUP BY model_name, symbol, market_window_seconds
ON CONFLICT(model_name, symbol, market_window_seconds, date_utc)
DO UPDATE SET
  n                 = excluded.n,
  n_correct         = excluded.n_correct,
  sum_pnl           = excluded.sum_pnl,
  sum_pnl_sq        = excluded.sum_pnl_sq,
  sum_p_calibrated  = excluded.sum_p_calibrated,
  sum_brier_terms   = excluded.sum_brier_terms,
  n_resolved_trades = excluded.n_resolved_trades,
  updated_at        = excluded.updated_at
"""


def _run_rollup_tick() -> int:
    """Upsert rollup rows for today + yesterday. Returns total rows upserted."""
    conn = _cutover_get_db()
    try:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        updated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

        total = 0
        for date_str in (today, yesterday):
            conn.execute(_ROLLUP_UPSERT_SQL, (date_str, updated_at, date_str))
            total += conn.execute(
                "SELECT COUNT(*) FROM predictions_daily_rollup WHERE date_utc = ?",
                (date_str,),
            ).fetchone()[0]
        conn.commit()
        return total
    finally:
        try:
            conn.close()
        except Exception:
            pass


async def _rollup_loop():
    """Background loop: every 10 min, upsert rollup rows for today + yesterday."""
    try:
        await asyncio.sleep(_ROLLUP_BOOT_DELAY_S)
    except asyncio.CancelledError:
        return
    while True:
        try:
            n = await asyncio.to_thread(_run_rollup_tick)
            logger.info("rollup loop: upserted %d rows across 2 dates", n)
        except Exception as exc:
            logger.warning("rollup loop tick failed: %s", exc)
        try:
            await asyncio.sleep(_ROLLUP_INTERVAL_S)
        except asyncio.CancelledError:
            return


def compute_full_backfill(conn, days: int = 90) -> int:
    """One-shot backfill: upsert rollup rows for the last ``days`` calendar days.

    Commits every 7 days of inserts to avoid one giant transaction.
    Designed to be called manually after deploy via CLI entry point.

    Returns total rollup rows present after backfill.
    """
    from datetime import date as _date, timedelta as _td

    today = datetime.now(timezone.utc).date()
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    total_inserted = 0
    batch = 0
    for offset in range(days, -1, -1):  # oldest → newest
        target_date = (today - _td(days=offset)).strftime("%Y-%m-%d")
        conn.execute(_ROLLUP_UPSERT_SQL, (target_date, updated_at, target_date))
        total_inserted += 1
        batch += 1
        if batch >= 7:
            conn.commit()
            batch = 0

    if batch > 0:
        conn.commit()

    total_rows = conn.execute(
        "SELECT COUNT(*) FROM predictions_daily_rollup"
    ).fetchone()[0]
    logger.info(
        "backfill complete: iterated %d dates, rollup table now has %d rows",
        total_inserted, total_rows,
    )
    return total_rows


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("Dashboard API starting...")
    # Validate admin secret on startup
    env = os.environ.get("V3_ENV", "dev")
    validate_admin_secret(env=env)
    # Apply pending schema migrations (T32: previously only paper_trader did this,
    # so a dashboard-only deploy left new columns missing until trader restart).
    try:
        from storage.db import open_database, init_schema
        db_path = os.environ.get("V3_DB_PATH", "/data/v3.db")
        _mig_conn = open_database(db_path)
        init_schema(_mig_conn)
        _mig_conn.close()
        logger.info("Schema migrations applied")
    except Exception as e:
        logger.error("Schema migration failed at startup: %s", e)
    LiveState.initialize()
    start_alert_worker()
    task = asyncio.create_task(_refresh_loop())
    precompute_task = asyncio.create_task(_analysis_precompute_loop())
    cutover_task = asyncio.create_task(_cutover_scheduler_loop())
    tier_task = asyncio.create_task(_tier_scoring_loop())
    governance_probation_task = asyncio.create_task(_governance_probation_loop())
    governance_action_task = asyncio.create_task(_governance_action_loop())
    rollup_task = asyncio.create_task(_rollup_loop())
    logger.info(
        "Dashboard API ready — background refresh + analysis precompute "
        "+ cutover scheduler + tier scoring + governance + rollup loops started"
    )
    yield
    task.cancel()
    precompute_task.cancel()
    cutover_task.cancel()
    tier_task.cancel()
    governance_probation_task.cancel()
    governance_action_task.cancel()
    rollup_task.cancel()
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
if _has_governance:
    _routers.append(governance_router.router)
if _has_system:
    _routers.append(system_router.router)
if _has_models_summary:
    _routers.append(models_summary_router.router)
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


# ---------------------------------------------------------------------------
# CLI entry point for one-shot backfill
# Usage: python -m dashboard_api.main backfill_rollups [--days 90]
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if args and args[0] == "backfill_rollups":
        import argparse

        parser = argparse.ArgumentParser(description="Backfill predictions_daily_rollup")
        parser.add_argument("command")
        parser.add_argument("--days", type=int, default=90,
                            help="Number of calendar days to backfill (default: 90)")
        parser.add_argument("--db", type=str,
                            help="Override DB path (default: $STORAGE_DB_PATH or /data/v3.db)")
        parsed = parser.parse_args(args)

        db_path = parsed.db or os.environ.get("STORAGE_DB_PATH", "/data/v3.db")
        try:
            from storage.db import open_database, init_schema
        except ModuleNotFoundError:
            import sys as _sys
            _sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
            from storage.db import open_database, init_schema  # type: ignore

        _conn = open_database(db_path)
        init_schema(_conn)
        n_rows = compute_full_backfill(_conn, days=parsed.days)
        _conn.close()
        print(f"Backfill done — {n_rows} total rows in predictions_daily_rollup")
        sys.exit(0)
    else:
        import uvicorn
        uvicorn.run("dashboard_api.main:app", host="0.0.0.0", port=8000, reload=False)
