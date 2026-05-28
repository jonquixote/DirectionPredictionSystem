"""Per-boundary scoring + trade-execution path extracted from PaperTrader.

For each 5/15/30-minute contract boundary, walks every (symbol, model) pair,
runs feature extraction -> model inference -> calibration -> filter pipeline ->
trade execution -> Kalshi dispatch. The hottest path in the system.

Per-model gate config (confidence, EV, blackout, warmup) comes from
model_registry.filter_config_json via _model_meta with global fallback.
See docs/superpowers/specs/2026-05-17-per-model-gates-design.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os as _os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import numpy as np

# Module-level constants are duplicated here from paper_trader to avoid a
# circular import (paper_trader imports BoundaryScorer; if boundary_scorer
# imported from paper_trader the cycle would deadlock at module load time).
# Values must stay in sync with trading/paper_trader.py.
PREDICTION_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
TRADE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
CONTRACT_DURATIONS = [300, 900, 1800]
SIMULATED_STAKE_USDC = 10.0
MAD_WARMUP_SECONDS = 1800

H60_BLACKOUT_HOURS = set(range(21, 24)) | set(range(0, 4))  # 21:00-03:59 UTC
H60_BLACKOUT_MODELS = {"h60", "h60_v2", "h60_v3"}
H300_SUPPRESS_DURATIONS: dict = {"h300": {300}}  # model -> set of suppressed durations

APFS_PAPER_CONFIDENCE_FLOOR = float(_os.environ.get("APFS_PAPER_CONFIDENCE_FLOOR", "0.50"))

logger = logging.getLogger("boundary_scorer")


class BoundaryScorer:
    """Per-boundary scoring + trade-execution path extracted from PaperTrader.

    Rather than copying the live-trader references at construction time,
    BoundaryScorer holds a back-reference to the owning PaperTrader instance
    and reads its attributes at call time. This ensures test harnesses,
    reconnect logic, and runtime mutations (e.g. rebinding _kalshi_trader)
    are automatically reflected without extra plumbing.

    Parameters
    ----------
    trader:
        The PaperTrader instance that owns this scorer.
    """

    def __init__(self, *, trader: object) -> None:
        self._trader = trader

    async def score_boundary(self, now_ms: int, boundary_ms: int) -> None:
        """Main entry. Pure relocation of _run_predictions body."""
        t = self._trader
        boundary_ts = boundary_ms // 1000
        # Phase 57 capacity probe — timing + RSS per boundary.
        _probe_t0 = time.time()
        try:
            import psutil as _psutil
            _probe_rss_mb = _psutil.Process().memory_info().rss / (1024 * 1024)
        except Exception:
            _probe_rss_mb = -1.0
        _probe_n_models = len(getattr(t, "models", {}) or {})
        logger.info(
            "boundary_start boundary_ms=%d n_models=%d rss_mb=%.1f",
            boundary_ms, _probe_n_models, _probe_rss_mb,
        )

        # C5: Initialize accumulator for overlap recording.
        # Keyed by (symbol, market_window_seconds) → list[ModelScore].
        # One row written to model_overlap per key after all models score.
        from trading.overlap_writer import ModelScore
        per_boundary_scores: dict = {}  # (symbol, window) -> list[ModelScore]

        # consensus_required: deferred paper_trade writes.
        # For models with filter_config.consensus_required=True, we cannot know
        # consensus at trade-write time (other models haven't all scored yet).
        # Solution: defer their paper_trade log_paper_trade calls until after all
        # models for the symbol have scored, then check local consensus from
        # per_boundary_scores. Keyed by symbol → list of (fn, args) callables.
        # Predictions are always logged immediately (unaffected by this gate).
        _deferred_trades: dict = {}  # symbol -> list[dict] of pending trade kwargs

        # Fetch live Kalshi bankroll to sync paper trader bankroll
        t._current_kalshi_bankroll = None
        if getattr(t, "_kalshi_trader", None) is not None:
            try:
                t._current_kalshi_bankroll = await t._kalshi_trader.effective_bankroll_usd()
            except Exception as e:
                logger.warning("Failed to fetch Kalshi bankroll for paper sync: %s", e)

        for symbol in PREDICTION_SYMBOLS:
            if not t.feature_computer.is_warmed_up(symbol):
                logger.info("Skipping %s — not warmed up yet", symbol)
                continue

            # Initialize deferred trade list for this symbol (consensus_required gate)
            _deferred_trades[symbol] = []

            trade_eligible = symbol in TRADE_SYMBOLS

            # Get feature bar
            bar = t.feature_computer.get_1min_bar(symbol)
            if bar is None:
                logger.warning("No 1-min bar for %s at boundary", symbol)
                continue

            # Mid-price range check
            mid_price = bar.get("mid_price", 0)
            t._check_mid_price_range(symbol, mid_price)

            # Query Polymarket p_market once per symbol per boundary
            p_market = None
            if t._http_session:
                try:
                    from trading.polymarket_discovery import get_p_market
                    p_market = await get_p_market(t._http_session, symbol, boundary_ts)
                    if p_market is not None:
                        logger.debug("[%s] p_market=%.3f", symbol, p_market)
                except Exception as e:
                    logger.warning("p_market query failed for %s: %s", symbol, e)

            _diag_guard_skip = 0
            _diag_blocked_skip = 0
            _diag_predicted = 0

            # -- Step 6: compute blocked models via ModelSelector --
            blocked_models: set = set()
            try:
                from trading.model_selector import ModelSelector
                selector = ModelSelector(t._db_conn)
                _candidates_by_sh: dict = {}
                for mn in t.models:
                    mm = t._model_meta.get(mn, {})
                    ms = mm.get("symbol", symbol)
                    mh = mm.get("training_horizon_seconds", 300)
                    if ms == symbol:
                        _candidates_by_sh.setdefault((ms, mh), []).append(mn)
                for (s, h), cands in _candidates_by_sh.items():
                    sel = selector.select(s, h, cands)
                    blocked_models.update(sel.blocked)
                logger.debug("[DIAG] %s: sh_groups=%d blocked=%d total_models=%d", symbol, len(_candidates_by_sh), len(blocked_models), len(t.models))
            except Exception as e:
                logger.debug("[DIAG] %s: ModelSelector failed: %s", symbol, e)

            for model_name, model in t.models.items():
                meta = t._get_meta(model_name)

                if meta.get("symbol") != symbol:
                    _diag_guard_skip += 1
                    continue

                if model_name in blocked_models:
                    _diag_blocked_skip += 1
                    continue

                features = {col: bar.get(col, 0.0) for col in t.feature_names[model_name]}
                feature_vec = np.array(
                    [features[col] for col in t.feature_names[model_name]],
                    dtype=np.float64,
                ).reshape(1, -1)

                pred_proba = float(model.predict(feature_vec)[0])
                pred_direction = "up" if pred_proba > 0.5 else "down"

                # Try to get calibrated prediction from CalibratorRegistry.
                # Use the 300s calibrator as canonical for the gate check; it is
                # the standard native window.  KeyError means no calibrator file
                # has been written yet (normal at cold start) — fall back to
                # identity.  All other exceptions propagate so real bugs are visible.
                pred_proba_calibrated = pred_proba
                try:
                    calibrator = t.calibrators.get(model_name, symbol, 300)
                    pred_proba_calibrated = calibrator.calibrate(pred_proba)
                except KeyError:
                    logger.info(
                        "calibrator_missing: no calibrator registered for %s/%s/300 — using raw proba",
                        model_name, symbol,
                    )

                # In APFS mode, lower the confidence floor so APFS
                # can evaluate the full prediction range.
                _apfs_active = (
                    getattr(t, "_kalshi_trader", None) is not None
                    and t._kalshi_trader._apfs_enabled
                )
                _ct = APFS_PAPER_CONFIDENCE_FLOOR if _apfs_active else t.filters["confidence_threshold"]
                above_threshold = pred_proba > _ct or pred_proba < (1 - _ct)
                _active_filter_mode = "apfs" if _apfs_active else "confidence_gate"

                ts_model_ran_ms = int(time.time() * 1000)
                # Per-model warmup resolved below after _model_fc is populated.
                # Set a provisional value (will be overridden at ~line 1042).
                in_warmup = t._is_in_warmup()

                # Compute metadata for prediction record
                pred_dt = datetime.fromtimestamp(ts_model_ran_ms / 1000, tz=timezone.utc)
                utc_hour = pred_dt.hour
                day_of_week = pred_dt.weekday()  # 0=Monday, 6=Sunday

                # Compute divergence
                p_model_minus_market = None
                if p_market is not None:
                    p_model_minus_market = round(pred_proba - p_market, 6)

                # Log prediction (always — even during warmup, even for non-trade symbols)
                prediction_id = t._emit_prediction_rows(
                    model_name=model_name,
                    symbol=symbol,
                    boundary_ms=boundary_ms,
                    ts_model_ran_ms=ts_model_ran_ms,
                    pred_proba_raw=pred_proba,
                    pred_proba_calibrated=pred_proba_calibrated,
                    pred_direction=pred_direction,
                    above_threshold=above_threshold,
                    warmup=in_warmup,
                    platform="paper",
                    price_at_open=mid_price,
                    p_market=p_market,
                    p_model_minus_market=p_model_minus_market,
                    utc_hour=utc_hour,
                    day_of_week=day_of_week,
                    is_weekend=(day_of_week >= 5),
                    relative_spread=features.get("relative_spread"),
                    regime_features=features,
                )
                t._prediction_count += 1
                _diag_predicted += 1

                # C3: Wire _evaluate_paper_filters to gate trade emission
                calibrated_p = max(pred_proba_calibrated, 1 - pred_proba_calibrated)
                ev = 0.0
                if p_market is not None and 0 < p_market < 1:
                    from execution.ev import compute_ev_polymarket
                    ev_result = compute_ev_polymarket(
                        calibrated_p=calibrated_p,
                        p_market=p_market,
                        stake=SIMULATED_STAKE_USDC,
                    )
                    ev = ev_result.ev
                blackout_hours = list(H60_BLACKOUT_HOURS) if (model_name in H60_BLACKOUT_MODELS or meta.get("training_horizon_seconds") == 60) else []
                book_age = (ts_model_ran_ms - bar.get("ts_ms", ts_model_ran_ms)) / 1000.0

                # -- Step 5: resolve per-model filter_config overrides --
                _model_fc = meta.get("filter_config", {})
                if isinstance(_model_fc, str):
                    _model_fc = json.loads(_model_fc)
                _base_ct = _model_fc.get("confidence_threshold", _ct)
                _base_ev = _model_fc.get("ev_threshold",
                    t.filters.get("ev_threshold", 0.0))
                # Per-regime threshold override (Phase 2b — shared helper).
                from filters.regime_gate import resolve_thresholds as _resolve_thresholds
                _regime_tags = t._tag_regime(symbol, features or {})
                _regime_for_row = {
                    "volatility": getattr(_regime_tags, "volatility", None),
                    "liquidity": getattr(_regime_tags, "liquidity", None),
                    "trend": getattr(_regime_tags, "trend", None),
                }
                effective_ct, effective_ev_thresh = _resolve_thresholds(
                    _model_fc, _regime_for_row, _base_ct, _base_ev,
                )

                # Per-model warmup override (overrides provisional value set above)
                effective_warmup_s = _model_fc.get("warmup_seconds", MAD_WARMUP_SECONDS)
                in_warmup = t._is_model_in_warmup(effective_warmup_s)

                filter_ctx = {
                    "prediction_id": prediction_id,
                    "model_name": model_name,
                    "symbol": symbol,
                    "boundary_ms": boundary_ms,
                    "pred_proba": pred_proba,
                    "pred_proba_calibrated": pred_proba_calibrated,
                    "pred_direction": pred_direction,
                    "above_threshold": above_threshold,
                    "warmup": in_warmup,
                    "confidence_threshold": effective_ct,
                    "ev_threshold": effective_ev_thresh,
                    "active_filter_mode": _active_filter_mode,
                    "p_market": p_market,
                    "regime_features": features,
                    "calibrated_p": calibrated_p,
                    "ev": ev,
                    "utc_hour": utc_hour,
                    "blackout_hours": blackout_hours,
                    "model_conflict": False,
                    "book_age_seconds": book_age,
                    "book_has_quotes": bar.get("has_quotes", True),
                }
                verdict = t._evaluate_paper_filters(filter_ctx)
                if not verdict.passed:
                    logger.info("paper_filter_blocked",
                            extra={"reason": verdict.reason,
                                   "prediction_id": prediction_id})
                    t._record_compact_decision(
                        prediction_id=prediction_id,
                        outcome="gated",
                        reason="paper_filter",
                        ev_estimate=None,
                        kelly_fraction_capped=None,
                        final_size_usdc=None,
                        order_type=None,
                    )
                    continue  # skip to next symbol/model

                # C5: Accumulate score for overlap recording.
                # Record once per (symbol, window) group; use CONTRACT_DURATIONS
                # so each duration gets its own model_overlap row.
                for _overlap_window in CONTRACT_DURATIONS:
                    try:
                        _dur_cal_p = pred_proba_calibrated
                        try:
                            _dc = t.calibrators.get(model_name, symbol, _overlap_window)
                            _dur_cal_p = _dc.calibrate(pred_proba)
                        except (KeyError, Exception):
                            pass
                        score = ModelScore(
                            model_name=model_name,
                            direction=pred_direction,
                            calibrated_confidence=_dur_cal_p,
                        )
                        per_boundary_scores.setdefault(
                            (symbol, _overlap_window), []
                        ).append(score)
                    except Exception as e:
                        logger.warning("overlap_score_accumulation_failed: %s", e)

                # Suppress trades during warmup
                if in_warmup:
                    t._record_compact_decision(
                        prediction_id=prediction_id,
                        outcome="gated",
                        reason="warmup",
                        ev_estimate=None,
                        kelly_fraction_capped=None,
                        final_size_usdc=None,
                        order_type=None,
                    )
                    if above_threshold and trade_eligible:
                        warmup_elapsed = (ts_model_ran_ms - (t._first_data_time_ms or ts_model_ran_ms)) / 1000
                        warmup_remaining = MAD_WARMUP_SECONDS - warmup_elapsed
                        logger.info(
                            "[%s] %s: proba=%.4f WARMUP (%.0fs remaining) — trade suppressed",
                            model_name, symbol, pred_proba, warmup_remaining,
                        )
                    continue

                # Skip trades for non-eligible symbols
                if not trade_eligible:
                    if above_threshold:
                        logger.debug(
                            "[%s] %s: proba=%.4f (trade_eligible=false)",
                            model_name, symbol, pred_proba,
                        )
                    continue

                # -- UTC blackout (per-model) --
                # effective_blackout_hours: per-model override via filter_config_json,
                # falling back to the H60-family default (already resolved above as
                # `blackout_hours`).
                effective_blackout_hours = _model_fc.get("blackout_hours", blackout_hours)
                if effective_blackout_hours and utc_hour in effective_blackout_hours:
                    if above_threshold:
                        t._record_compact_decision(
                            prediction_id=prediction_id,
                            outcome="suppressed",
                            reason="utc_blackout",
                            ev_estimate=ev,
                            kelly_fraction_capped=None,
                            final_size_usdc=0.0,
                            order_type="skipped",
                        )
                        logger.info(
                            "[%s] %s: proba=%.4f dir=%s SUPPRESSED (utc_blackout %02d:00)",
                            model_name, symbol, pred_proba, pred_direction, utc_hour,
                        )
                    continue

                # -- Trade passes all filters — execute ---
                if not above_threshold:
                    logger.debug(
                        "[%s] %s: proba=%.4f (below threshold)",
                        model_name, symbol, pred_proba,
                    )
                    continue

                # Compute stake (Kelly or flat depending on config).
                # Phase 57: pass training_horizon_seconds as market_window_seconds so
                # the per-window kelly_multiplier is used for the primary window.
                _primary_window = meta.get("training_horizon_seconds")
                stake = t._compute_stake(
                    model_name=model_name,
                    pred_proba=pred_proba,
                    pred_direction=pred_direction,
                    p_market=p_market,
                    market_window_seconds=_primary_window,
                )

                t._record_compact_decision(
                    prediction_id=prediction_id,
                    outcome="executed",
                    reason=None,
                    ev_estimate=ev,
                    kelly_fraction_capped=None,
                    final_size_usdc=stake,
                    order_type="maker",
                )

                boundary_sec = boundary_ms // 1000
                is_15m_boundary = (boundary_sec % 900 == 0)

                # consensus_required gate: if this model requires fleet consensus,
                # defer its paper_trade writes until after all models for this
                # symbol have scored (so we can compute local consensus).
                # Predictions are ALWAYS logged immediately (see _emit_prediction_rows above).
                _needs_consensus = bool(_model_fc.get("consensus_required"))

                for duration in CONTRACT_DURATIONS:
                    suppressed_durs = H300_SUPPRESS_DURATIONS.get(model_name, set())
                    suppress_reason = "contract_mismatch" if duration in suppressed_durs else None

                    if duration == 900 and not is_15m_boundary and not suppress_reason:
                        suppress_reason = "non_15m_boundary"

                    # Per-duration calibration (fall back to gate-check calibrated)
                    try:
                        dur_cal = t.calibrators.get(model_name, symbol, duration)
                        dur_pred_proba_calibrated = dur_cal.calibrate(pred_proba)
                    except KeyError:
                        dur_pred_proba_calibrated = pred_proba_calibrated

                    trade_kwargs = dict(
                        prediction_id=prediction_id,
                        envelope=t._build_envelope(model_name, platform="paper"),
                        symbol=symbol,
                        market_window_seconds=duration,
                        resolution_type="evaluation",
                        ts_model_ran_ms=ts_model_ran_ms,
                        ts_contract_open_ms=boundary_ms,
                        ts_resolve_at_ms=boundary_ms + duration * 1000,
                        pred_proba_raw=pred_proba,
                        pred_proba_calibrated=dur_pred_proba_calibrated,
                        pred_direction=pred_direction,
                        confidence_threshold_used=effective_ct,
                        simulated_stake_usdc=stake,
                        decision_outcome="executed" if not suppress_reason else "suppressed",
                        decision_reason=suppress_reason,
                        ev_estimate=ev,
                        kelly_fraction_capped=None,
                        final_size_usdc=stake,
                        order_type=None,
                        warmup=in_warmup,
                        platform="paper",
                        p_market=p_market,
                    )

                    if _needs_consensus:
                        # Defer: consensus not yet determined (other models still scoring)
                        _deferred_trades[symbol].append({
                            "model_name": model_name,
                            "duration": duration,
                            "suppress_reason": suppress_reason,
                            "trade_kwargs": trade_kwargs,
                            "prediction_id": prediction_id,
                        })
                        continue

                    t.sqlite_ledger.log_paper_trade(**trade_kwargs)

                    if suppress_reason:
                        continue

                    t._trade_count += 1

                    # Kalshi live dispatch (only on 15-min boundaries for h300 BTC 900s)
                    if t.kalshi_dispatch_eligible(
                        model_name=model_name,
                        symbol=symbol,
                        market_window_seconds=duration,
                    ):
                        if not t.is_in_warmup(ts_model_ran_ms):
                            asyncio.create_task(t._dispatch_kalshi_live(
                                symbol=symbol,
                                duration_sec=duration,
                                boundary_ms=boundary_ms,
                                pred_proba=pred_proba,
                                pred_direction=pred_direction,
                                paper_stake_usd=stake,
                                features=features,
                                model_name=model_name,
                            ))

                if not _needs_consensus:
                    logger.info(
                        "[%s] %s %s: proba=%.4f dir=%s TRADE",
                        model_name, symbol, "\U0001f53c" if pred_direction == "up" else "\U0001f53d",
                        pred_proba, pred_direction,
                    )
                else:
                    logger.info(
                        "[%s] %s %s: proba=%.4f dir=%s TRADE_DEFERRED (consensus_required)",
                        model_name, symbol, "\U0001f53c" if pred_direction == "up" else "\U0001f53d",
                        pred_proba, pred_direction,
                    )

            logger.debug("[DIAG] %s: guard_skip=%d blocked_skip=%d predicted=%d", symbol, _diag_guard_skip, _diag_blocked_skip, _diag_predicted)

            # consensus_required: flush deferred paper_trade writes now that all
            # models for this symbol have scored.  Compute local consensus from the
            # per_boundary_scores accumulator (already populated for models that passed
            # the filter verdicts above).
            deferred = _deferred_trades.get(symbol, [])
            if deferred:
                # Build consensus map: (symbol, window) → bool
                _consensus_by_window: dict = {}
                for _win in CONTRACT_DURATIONS:
                    _scores = per_boundary_scores.get((symbol, _win), [])
                    if _scores:
                        _dirs = {s.direction for s in _scores}
                        _consensus_by_window[_win] = len(_dirs) == 1
                    else:
                        _consensus_by_window[_win] = False

                for _pending in deferred:
                    _dur = _pending["duration"]
                    _sr = _pending["suppress_reason"]
                    _kw = _pending["trade_kwargs"]
                    _pid = _pending["prediction_id"]
                    _mname = _pending["model_name"]
                    _has_consensus = _consensus_by_window.get(_dur, False)

                    if not _has_consensus:
                        # Gate: no consensus → skip paper_trade write, log suppression
                        logger.info(
                            "consensus_gate_blocked: %s %s %ds — no fleet consensus, "
                            "prediction logged, paper_trade suppressed",
                            _mname, symbol, _dur,
                        )
                        t._record_compact_decision(
                            prediction_id=_pid,
                            outcome="suppressed",
                            reason="consensus_required",
                            ev_estimate=_kw.get("ev_estimate"),
                            kelly_fraction_capped=None,
                            final_size_usdc=0.0,
                            order_type="skipped",
                        )
                        continue

                    # Consensus present → write trade
                    t.sqlite_ledger.log_paper_trade(**_kw)

                    if _sr:
                        continue

                    t._trade_count += 1
                    logger.info(
                        "consensus_gate_passed: %s %s %ds — consensus=1, trade written",
                        _mname, symbol, _dur,
                    )

        # C5: Record overlap scores for the boundary — one row per (symbol, window).
        for (sym, win), score_list in per_boundary_scores.items():
            if not score_list:
                continue
            try:
                t.record_overlap_for_boundary(
                    ts_contract_open_ms=boundary_ms,
                    symbol=sym,
                    market_window_seconds=win,
                    scores=score_list,
                )
            except Exception as e:
                logger.exception(
                    "overlap_recording_failed",
                    extra={"symbol": sym, "window": win, "err": str(e)},
                )

        logger.info(
            "Boundary %s: %d predictions total, %d trades total",
            datetime.fromtimestamp(boundary_ms / 1000, tz=timezone.utc).strftime("%H:%M"),
            t._prediction_count, t._trade_count,
        )
        if t._model_meta:
            _first_k, _first_v = next(iter(t._model_meta.items()))
            logger.debug("[DIAG] meta_sample: %s=%s", _first_k, _first_v)
        # Phase 57 capacity probe — wall-clock + RSS at end of boundary.
        try:
            import psutil as _psutil
            _probe_rss_end = _psutil.Process().memory_info().rss / (1024 * 1024)
        except Exception:
            _probe_rss_end = -1.0
        _probe_elapsed_ms = int((time.time() - _probe_t0) * 1000)
        logger.info(
            "boundary_done boundary_ms=%d n_models=%d elapsed_ms=%d "
            "rss_start_mb=%.1f rss_end_mb=%.1f",
            boundary_ms, _probe_n_models, _probe_elapsed_ms,
            _probe_rss_mb, _probe_rss_end,
        )


