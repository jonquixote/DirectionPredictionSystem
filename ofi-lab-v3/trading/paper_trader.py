#!/usr/bin/env python3
"""
Paper Trader — dual model comparison on Bybit spot L2 data.

Runs two LightGBM models (H=60 and H=300) simultaneously against
live WebSocket order book data, logging predictions and simulated
trades to append-only JSONL ledgers.

Usage:
    python -m trading.paper_trader --testnet     # validate on testnet
    python -m trading.paper_trader               # production WebSocket
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import argparse
import shutil
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import numpy as np
import lightgbm as lgb

from api.bybit import BybitOrderBookManager
from trading.live_features import LiveFeatureComputer, V3_FEATURE_COLS
from trading.ledger import Ledger
from trading.polymarket_discovery import get_p_market

# Exchange-aware live execution (default OFF — kill switch in KalshiLiveTrader)
import os as _os

# Load Kalshi creds from /data/kalshi.env if present (container deploy convention)
_env_file = "/data/kalshi.env"
if _os.path.exists(_env_file):
    with open(_env_file) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                _os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from execution.kalshi_live_trader import KalshiLiveTrader

import os
import config
from storage.db import open_database, init_schema
from storage.registry_state import RegistryState
from storage.policy_snapshot import PolicySnapshot
from storage.sqlite_ledger import SQLiteLedger
from storage.decision_trace import DecisionTraceWriter, FilterEval
from storage.pending_queue import PendingResolutionQueue, PendingEntry
from storage.window_planner import plan_resolution_rows
from storage.provenance import sha256_file, feature_names_hash, ProvenanceEnvelope, calibration_map_hash
from storage.lifecycle import evaluate_lifecycle_transitions
from execution.calibration import CalibratorRegistry
from regime.tagger import compute_regime, RegimeTags

EXCHANGE = _os.environ.get("EXCHANGE", "kalshi").lower()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("paper_trader")

# ── Configuration ──────────────────────────────────────────────

PREDICTION_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]  # all symbols get predictions
TRADE_SYMBOLS = ["BTCUSDT", "SOLUSDT"]                   # ETH excluded from trades
# CONFIDENCE_THRESHOLD removed — use self.filters["confidence_threshold"] (dynamic, dashboard-controllable)
# Legacy references point to the filters dict; see _run_predictions().
CONTRACT_DURATIONS = [300, 900]  # seconds
SIMULATED_STAKE_USDC = 10.0
POLYMARKET_FEE_COEFFICIENT = 0.072  # crypto taker fee: fee = shares * price * 0.072 * p * (1-p)
MIN_FEATURE_WARMUP_SECONDS = 120   # feature buffer fill
MAD_WARMUP_SECONDS = 1800          # 30 minutes for MAD normalization convergence

# UTC blackout: H60 models suppress paper trades during these hours
# (predictions still logged for all models at all hours)
H60_BLACKOUT_HOURS = set(range(21, 24)) | set(range(0, 4))  # 21:00-03:59 UTC
H60_BLACKOUT_MODELS = {"h60", "h60_v2", "h60_v3"}  # all H60 variants

# H300: suppress 300s contracts (5-min too short for signal to materialize)
H300_SUPPRESS_DURATIONS = {"h300": {300}}  # model -> set of suppressed durations

# Mid-price training ranges (from training data distributions)
MID_PRICE_TRAINING_RANGE = {
    "BTCUSDT": [58_000.0, 110_000.0],
    "ETHUSDT": [1_400.0, 4_200.0],
    "SOLUSDT": [90.0, 220.0],
}

# ── Filter Defaults (all OFF except confidence gate) ──────────
# These can be overridden via CLI args. When a filter is disabled,
# the bot still logs the relevant data for offline analysis.

FILTER_CONFIDENCE_GATE_ENABLED = True
# Default 0.52, overridable at boot via PAPER_CONFIDENCE_THRESHOLD env var
# (loaded from /data/kalshi.env). Runtime-mutable via PATCH /config and
# api_server persists changes back to the env file.
FILTER_CONFIDENCE_THRESHOLD = float(_os.environ.get("PAPER_CONFIDENCE_THRESHOLD", "0.52"))

# When APFS mode is active, lower the paper confidence floor so APFS
# can score/decide on the full prediction range (not just 0.52+).
APFS_PAPER_CONFIDENCE_FLOOR = float(_os.environ.get("APFS_PAPER_CONFIDENCE_FLOOR", "0.50"))

FILTER_CIRCUIT_BREAKER_ENABLED = False
FILTER_CIRCUIT_BREAKER_MAX_DRAWDOWN = -50.0  # USDC; suppress trades if running P&L drops below this

FILTER_CLOB_DIVERGENCE_ENABLED = False
FILTER_CLOB_DIVERGENCE_MIN_EDGE = 0.02  # require model_prob to exceed market_prob by at least this

FILTER_VOLATILITY_ENABLED = False
FILTER_VOLATILITY_MAX_SPREAD = 0.005  # suppress trades when relative_spread exceeds this

FILTER_KELLY_SIZING_ENABLED = True
FILTER_KELLY_FRACTION = 0.5           # half-Kelly (industry standard for ruin prevention)
FILTER_KELLY_MAX_BET_USDC = 50.0      # hard cap per trade
FILTER_KELLY_BANKROLL_USDC = 1000.0   # virtual bankroll

FILTER_PAUSE_TRADING = False           # emergency kill switch — suppresses all trades

# Per-symbol confidence overrides (empty = use global threshold for all)
# Example: {"BTCUSDT": 0.53, "SOLUSDT": 0.58}
FILTER_PER_SYMBOL_CONFIDENCE: dict[str, float] = {}

# Runtime API server
API_SERVER_PORT = 8080


class PaperTrader:
    """
    Main paper trading loop.

    Connects to Bybit WebSocket, computes features at each L2 update,
    scores both models at 5-minute contract boundaries, and logs
    predictions and paper trades.
    """

    def __init__(
        self,
        model_paths: dict[str, str],
        log_dir: str = "/data/logs",
        testnet: bool = True,
        features_dir: str | None = None,
        filter_config: dict | None = None,
        confidence_threshold: float | None = None,
    ):
        self.testnet = testnet
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # ── Filter configuration ──────────────────────────────
        fc = filter_config or {}
        self.filters = {
            "confidence_gate_enabled": fc.get("confidence_gate_enabled", FILTER_CONFIDENCE_GATE_ENABLED),
            "confidence_threshold": fc.get("confidence_threshold", FILTER_CONFIDENCE_THRESHOLD),
            "circuit_breaker_enabled": fc.get("circuit_breaker_enabled", FILTER_CIRCUIT_BREAKER_ENABLED),
            "circuit_breaker_max_drawdown": fc.get("circuit_breaker_max_drawdown", FILTER_CIRCUIT_BREAKER_MAX_DRAWDOWN),
            "clob_divergence_enabled": fc.get("clob_divergence_enabled", FILTER_CLOB_DIVERGENCE_ENABLED),
            "clob_divergence_min_edge": fc.get("clob_divergence_min_edge", FILTER_CLOB_DIVERGENCE_MIN_EDGE),
            "volatility_enabled": fc.get("volatility_enabled", FILTER_VOLATILITY_ENABLED),
            "volatility_max_spread": fc.get("volatility_max_spread", FILTER_VOLATILITY_MAX_SPREAD),
            "kelly_sizing_enabled": fc.get("kelly_sizing_enabled", FILTER_KELLY_SIZING_ENABLED),
            "kelly_fraction": fc.get("kelly_fraction", FILTER_KELLY_FRACTION),
            "kelly_max_bet_usdc": fc.get("kelly_max_bet_usdc", FILTER_KELLY_MAX_BET_USDC),
            "kelly_bankroll_usdc": fc.get("kelly_bankroll_usdc", FILTER_KELLY_BANKROLL_USDC),
            "pause_trading": fc.get("pause_trading", FILTER_PAUSE_TRADING),
            "per_symbol_confidence": fc.get("per_symbol_confidence", dict(FILTER_PER_SYMBOL_CONFIDENCE)),
        }
        if confidence_threshold is not None:
            self.filters["confidence_threshold"] = confidence_threshold

        # API server port (0 = disabled)
        self._api_port = fc.get("api_port", API_SERVER_PORT)

        # Load models
        self.models: dict[str, lgb.Booster] = {}
        self.feature_names: dict[str, list[str]] = {}
        for name, path in model_paths.items():
            model_dir = Path(path).parent
            self.models[name] = lgb.Booster(model_file=path)
            fn_path = model_dir / "feature_names.json"
            if fn_path.exists():
                with open(fn_path) as f:
                    self.feature_names[name] = json.load(f)
            else:
                self.feature_names[name] = V3_FEATURE_COLS
            logger.info("Loaded model %s from %s (%d features)",
                        name, path, len(self.feature_names[name]))

        # Ledgers (one per model)
        self.ledgers: dict[str, Ledger] = {}  # legacy retired in T22; SQLite is canonical

        # Feature computer (all prediction symbols need L2 data)
        self.feature_computer = LiveFeatureComputer(
            symbols=PREDICTION_SYMBOLS,
            features_dir=features_dir or "/data/features_v3",
        )

        # Order book manager
        self.book_manager = BybitOrderBookManager(
            symbols=PREDICTION_SYMBOLS,
            levels=10,
            testnet=testnet,
        )

        # aiohttp session for Polymarket / Kalshi REST queries
        self._http_session: Optional[aiohttp.ClientSession] = None

        # Live execution gateway (default-deny; kill switch lives in trader itself)
        self._kalshi_trader: Optional[KalshiLiveTrader] = None
        # Cache: boundary_ts -> kalshi ticker, refreshed once per boundary
        self._kalshi_ticker_cache: dict[int, str] = {}

        # Tracking
        self._last_contract_boundary_ms = 0
        self._prediction_count = 0
        self._trade_count = 0
        self._paper_trade_count = 0
        self._running = True
        self._start_time_ms = int(time.time() * 1000)
        self._first_data_time_ms: int | None = None

        # ── Boundary cadence counters (for periodic tasks) ────────
        self._boundary_count = 0
        self._decay_refresh_interval = 4  # every 4 boundaries
        self._lifecycle_interval = 16      # every 16 boundaries

        # ── Running P&L for circuit breaker ───────────────────
        self._running_pnl: dict[str, float] = {}  # per-model running P&L
        self._hydrate_running_pnl()

        # ---------- v3 storage layer (parallel to legacy JSONL during cutover) ----------
        db_path = os.environ.get("STORAGE_DB_PATH", config.STORAGE_DB_PATH)
        self._db_conn = open_database(db_path)
        init_schema(self._db_conn)
        self.registry_state = RegistryState(self._db_conn)
        self.registry_state.bootstrap_if_empty(reason="paper_trader boot")

        self.policy_snapshot = PolicySnapshot(self._db_conn)
        self._policy_snapshot_dict = self._capture_policy_dict()
        self.policy_snapshot.capture(self._policy_snapshot_dict, initiated_by="boot")

        self.sqlite_ledger = SQLiteLedger(self._db_conn)
        self.decision_trace = DecisionTraceWriter(self._db_conn)
        self.pending_queue = PendingResolutionQueue(
            self.log_dir / "pending_v3.json"
        )

        calib_dir = os.environ.get("KALSHI_CALIBRATION_DIR", "/data")
        self.calibrators = CalibratorRegistry(base_dir=calib_dir)

        self._regime_thresholds_path = os.environ.get(
            "REGIME_THRESHOLDS_PATH",
            "/data/regime_thresholds.json",
        )
        self._regime_thresholds: dict = {}
        self._reload_regime_thresholds()

        # Compute provenance hashes for each loaded model
        self._model_envelopes: dict[str, dict] = {}
        for name, path in model_paths.items():
            self._model_envelopes[name] = {
                "model_artifact_hash": sha256_file(path),
                "feature_names_hash": feature_names_hash(self.feature_names[name]),
            }

        # Boot timestamp for warmup tagging
        self._boot_ts_ms = int(time.time() * 1000)

    def _reload_regime_thresholds(self) -> None:
        import json
        p = Path(self._regime_thresholds_path)
        if p.exists():
            try:
                self._regime_thresholds = json.loads(p.read_text())
            except Exception:
                self._regime_thresholds = {}

    def _tag_regime(self, symbol: str, regime_features: dict) -> RegimeTags:
        if not self._regime_thresholds:
            return RegimeTags(volatility="unknown", liquidity="unknown",
                              trend="unknown")
        return compute_regime(symbol, regime_features, self._regime_thresholds)

    def _capture_policy_dict(self) -> dict:
        """Snapshot the runtime-mutable filter/threshold/Kelly config.

        Canonical input to PolicySnapshot.capture(). Any field that
        influences a trade decision and can change at runtime must
        appear here.
        """
        f = self.filters
        return {
            "confidence_threshold": f.get("confidence_threshold"),
            "per_symbol_confidence": f.get("per_symbol_confidence", {}),
            "kelly_fraction": f.get("kelly_fraction"),
            "ev_threshold": f.get("ev_threshold", 0.0),
            "circuit_breaker_drawdown": f.get("circuit_breaker_drawdown"),
            "clob_divergence_min_edge": f.get("clob_divergence_min_edge"),
            "filter_mode": f.get("filter_mode"),
            "blackout_hours_utc": list(f.get("blackout_hours_utc", [])),
        }

    def _build_envelope(self, model_name: str, platform: str) -> ProvenanceEnvelope:
        """Construct the provenance envelope for the next prediction.

        Re-captures the policy snapshot (cheap; only writes a new audit row
        if the canonical form changed) and re-hashes the active calibration
        map for the model so any out-of-band refit flows through.
        """
        meta = config.PAPER_TRADING["model_metadata"][model_name]
        art_hash = self._model_envelopes[model_name]["model_artifact_hash"]
        fname_hash = self._model_envelopes[model_name]["feature_names_hash"]
        policy_v, policy_h = self.policy_snapshot.capture(
            self._capture_policy_dict(), initiated_by="prediction"
        )
        cal = self.calibrators.get(model_name, meta["symbol"],
                                    meta["training_horizon_seconds"])
        cal_map = {"method": "binmap", "bins": list(cal._bins)}
        cal_h = calibration_map_hash(cal_map)
        return ProvenanceEnvelope(
            model_name=model_name,
            model_artifact_hash=art_hash,
            feature_names_hash=fname_hash,
            feature_version=meta["feature_version"],
            training_horizon_seconds=meta["training_horizon_seconds"],
            train_window_start=meta["train_window_start"],
            train_window_end=meta["train_window_end"],
            train_cutoff=meta["train_cutoff"],
            registry_load_generation=self.registry_state.current_generation(),
            policy_config_hash=policy_h,
            decision_policy_version=policy_v,
            calibration_map_hash=cal_h,
            platform=platform,
        )

    def _emit_prediction_rows(
        self,
        *,
        model_name: str,
        symbol: str,
        boundary_ms: int,
        ts_model_ran_ms: int,
        pred_proba_raw: float,
        pred_proba_calibrated: float,
        pred_direction: str,
        above_threshold: bool,
        warmup: bool,
        platform: str,
        price_at_open: float,
        p_market: Optional[float] = None,
        p_model_minus_market: Optional[float] = None,
        utc_hour: Optional[int] = None,
        day_of_week: Optional[int] = None,
        is_weekend: Optional[int] = None,
        relative_spread: Optional[float] = None,
        regime_features: Optional[dict] = None,
    ) -> str:
        """Insert native + evaluation prediction rows for one boundary
        and enqueue each in the pending resolution queue. Returns the
        native row's prediction_id.
        """
        meta = config.PAPER_TRADING["model_metadata"][model_name]
        horizon = meta["training_horizon_seconds"]
        rows = plan_resolution_rows(
            boundary_ms=boundary_ms,
            training_horizon_seconds=horizon,
            evaluation_windows=config.EVALUATION_WINDOWS,
        )
        envelope = self._build_envelope(model_name, platform=platform)
        tags = self._tag_regime(symbol, regime_features or {})
        native_pid = self.sqlite_ledger.log_prediction_set(
            envelope=envelope,
            symbol=symbol,
            ts_model_ran_ms=ts_model_ran_ms,
            ts_contract_open_ms=boundary_ms,
            rows=rows,
            pred_proba_raw=pred_proba_raw,
            pred_proba_calibrated=pred_proba_calibrated,
            pred_direction=pred_direction,
            above_threshold=above_threshold,
            warmup=warmup,
            platform=platform,
            p_market=p_market,
            p_model_minus_market=p_model_minus_market,
            utc_hour=utc_hour,
            day_of_week=day_of_week,
            is_weekend=is_weekend,
            relative_spread=relative_spread,
            regime_volatility=tags.volatility,
            regime_liquidity=tags.liquidity,
            regime_trend=tags.trend,
        )
        # Build prediction_id for each row to enqueue. Mirror SQLiteLedger's
        # suffix scheme: <prefix>_<window><n|e>.
        prefix = native_pid.rsplit("_", 1)[0]
        for r in rows:
            suffix = f"{r.market_window_seconds}{r.resolution_type[0]}"
            pid = f"{prefix}_{suffix}"
            self.pending_queue.enqueue(PendingEntry(
                prediction_id=pid,
                boundary_ms=boundary_ms,
                model_name=model_name,
                symbol=symbol,
                market_window_seconds=r.market_window_seconds,
                registry_load_generation=envelope.registry_load_generation,
                ts_resolve_at_ms=r.ts_resolve_at_ms,
                resolution_type=r.resolution_type,
                price_at_open=price_at_open,
            ))
        self.pending_queue.persist()
        return native_pid

    async def _check_prediction_resolutions_v3(self, now_ms: int) -> None:
        """Walk the pending queue and resolve every ripe row.

        Native rows update calibration_outcomes via SQLiteLedger;
        evaluation rows do not. Each resolved row is removed from the
        queue. The queue is persisted at the end so a crash mid-loop
        leaves the partially-resolved state recoverable.
        """
        ripe = list(self.pending_queue.iter_ripe(now_ms))
        if not ripe:
            return
        for entry in ripe:
            try:
                close_price = self.feature_computer.price_at(
                    entry.symbol, entry.ts_resolve_at_ms,
                )
            except KeyError:
                # Price not yet available for this exact ts; leave queued.
                continue
            result, correct = self._compute_outcome(
                direction=self._direction_for(entry.prediction_id),
                price_open=entry.price_at_open,
                price_close=close_price,
            )
            if entry.resolution_type == "native":
                self.sqlite_ledger.record_native_resolution(
                    prediction_id=entry.prediction_id,
                    ts_resolved_ms=entry.ts_resolve_at_ms,
                    price_at_open=entry.price_at_open,
                    price_at_close=close_price,
                    contract_result=result,
                    prediction_correct=correct,
                )
                # Feed per-model calibrator (only for native, only when not
                # warmup)
                row = self._db_conn.execute(
                    "SELECT pred_proba_raw, warmup, model_name, symbol,"
                    " market_window_seconds FROM predictions"
                    " WHERE prediction_id = ?",
                    (entry.prediction_id,),
                ).fetchone()
                if row and row["warmup"] == 0:
                    cal = self.calibrators.get(
                        row["model_name"], row["symbol"],
                        row["market_window_seconds"],
                    )
                    cal.record_outcome(float(row["pred_proba_raw"]), bool(correct))
            else:
                self.sqlite_ledger.record_evaluation_resolution(
                    prediction_id=entry.prediction_id,
                    ts_resolved_ms=entry.ts_resolve_at_ms,
                    price_at_open=entry.price_at_open,
                    price_at_close=close_price,
                    contract_result=result,
                    prediction_correct=correct,
                )
            self.pending_queue.remove(entry.prediction_id)
        self.pending_queue.persist()

    async def _check_trade_resolutions_v3(self, now_ms: int) -> None:
        """Resolve any open paper_trades whose ts_resolve_at_ms <= now_ms.

        Uses Polymarket-style binary option PnL math (fee coef 0.072).
        Plan B extends this to a per-platform fee model.
        """
        rows = self._db_conn.execute(
            "SELECT trade_id, prediction_id, symbol,"
            " ts_resolve_at_ms, pred_proba_calibrated, pred_direction,"
            " simulated_stake_usdc, market_window_seconds"
            " FROM paper_trades WHERE resolved = 0 AND ts_resolve_at_ms <= ?",
            (now_ms,),
        ).fetchall()
        for row in rows:
            try:
                close = self.feature_computer.price_at(
                    row["symbol"], row["ts_resolve_at_ms"]
                )
            except (KeyError, AttributeError):
                continue
            pred = self._db_conn.execute(
                "SELECT price_at_open FROM predictions WHERE prediction_id = ?",
                (row["prediction_id"],)
            ).fetchone()
            if pred is None or pred["price_at_open"] is None:
                continue
            gross, fee, net, result, correct = self._compute_paper_pnl(
                direction=row["pred_direction"],
                calibrated_p=row["pred_proba_calibrated"],
                stake=row["simulated_stake_usdc"] or 10.0,
                price_open=pred["price_at_open"],
                price_close=close,
            )
            self.sqlite_ledger.record_trade_resolution(
                trade_id=row["trade_id"],
                ts_resolved_ms=row["ts_resolve_at_ms"],
                price_at_close=close,
                contract_result=result,
                prediction_correct=correct,
                gross_pnl=gross,
                fee_paid=fee,
                net_pnl=net,
                trade_result="win" if correct else "loss",
                pnl_method="binary_polymarket",
            )

    def _compute_paper_pnl(self, *, direction, calibrated_p, stake,
                          price_open, price_close):
        """Polymarket-style binary option PnL.

        Lifted from v2 paper_trader.py _resolve_trade lines ~1100-1141.
        Fee coef 0.072 matches Polymarket's published rate.
        """
        if price_close > price_open:
            result = "up"
        elif price_close < price_open:
            result = "down"
        else:
            result = "flat"
        correct = (result == direction)
        fee = 0.072 * calibrated_p * (1 - calibrated_p) * stake
        if correct:
            gross = stake * (1 - calibrated_p) / calibrated_p if calibrated_p > 0 else 0
        else:
            gross = -stake
        net = gross - fee
        return gross, fee, net, result, correct

    def is_in_warmup(self, now_ms: int) -> bool:
        """True while the predictor is still inside the warmup window.

        Predictions made during warmup are stamped warmup=1 and excluded
        from calibration / decay / Kalshi dispatch by downstream filters.
        """
        return now_ms < self._boot_ts_ms + config.WARMUP_SECONDS * 1000

    def _record_compact_decision(
        self,
        *,
        prediction_id: str,
        outcome: str,
        reason: Optional[str],
        ev_estimate: Optional[float],
        kelly_fraction_capped: Optional[float],
        final_size_usdc: Optional[float],
        order_type: Optional[str],
    ) -> None:
        """UPDATE the prediction row with the inline compact-decision fields."""
        self.sqlite_ledger.log_compact_decision(
            prediction_id=prediction_id,
            decision_outcome=outcome,
            decision_reason=reason,
            ev_estimate=ev_estimate,
            kelly_fraction_capped=kelly_fraction_capped,
            final_size_usdc=final_size_usdc,
            order_type=order_type,
        )

    def _write_verbose_trace_for_v2_filters(
        self,
        *,
        prediction_id: str,
        envelope,
        filter_inputs: dict,
        kelly_raw: Optional[float],
        kelly_capped: Optional[float],
        bankroll_used: Optional[float],
        per_trade_cap_usdc: Optional[float],
        fee_model: str,
        fee_amount: Optional[float],
        platform_gate: Optional[dict],
        warmup: bool,
        consensus_data: Optional[dict],
    ) -> None:
        """Adapter from v2 filter dict to FilterEval rows.

        ``filter_inputs`` shape: {name: (threshold, input_value, passed)}.
        """
        filters = [
            FilterEval(name=n, threshold=t, input_value=v, passed=bool(p))
            for n, (t, v, p) in filter_inputs.items()
        ]
        self.decision_trace.write(
            prediction_id=prediction_id,
            filters=filters,
            kelly_raw=kelly_raw, kelly_capped=kelly_capped,
            bankroll_used=bankroll_used,
            per_trade_cap_usdc=per_trade_cap_usdc,
            fee_model=fee_model, fee_amount=fee_amount,
            platform_gate=platform_gate,
            warmup=warmup, consensus_data=consensus_data,
            policy_config_hash=envelope.policy_config_hash,
            calibration_map_hash=envelope.calibration_map_hash,
            registry_load_generation=envelope.registry_load_generation,
        )

    # ── Plan B integration helpers (T26, T29, T36) ─────────────

    def _evaluate_paper_filters(self, ctx: dict):
        """Run the paper-tier filter pipeline against a decision context."""
        from filters.pipeline import FilterPipeline
        from filters.staleness import build_stale_price_stage, build_stale_book_stage
        from filters.paper_filter import build_paper_filter_stage

        pipeline = FilterPipeline([
            build_stale_book_stage(),
            build_stale_price_stage(
                max_age_seconds=self.filters.get("max_book_age_seconds", 30),
            ),
            build_paper_filter_stage(
                confidence_threshold=self.filters.get("confidence_threshold", 0.55),
                ev_threshold=self.filters.get("ev_threshold", 0.0),
            ),
        ])
        return pipeline.run(ctx)

    @property
    def _decay_writer(self):
        if not hasattr(self, "_decay_writer_cache"):
            from storage.decay_writer import DecayWriter
            self._decay_writer_cache = DecayWriter(self._db_conn)
        return self._decay_writer_cache

    def refresh_decay_metrics(self, *, window_size: int = 100) -> None:
        """Compute rolling decay metrics for every (model, symbol, window) and
        write a snapshot row to ``decay_metrics``.
        """
        from storage.decay_metrics import (
            compute_brier_score, compute_calibration_error,
            compute_rolling_ev, compute_recency_weighted_ev,
        )
        writer = self._decay_writer
        # Find every distinct (model, symbol, market_window_seconds)
        triples = self._db_conn.execute(
            "SELECT DISTINCT model_name, symbol, market_window_seconds"
            " FROM paper_trades"
            " WHERE resolution_type = 'native' AND resolved = 1"
            "   AND warmup = 0"
        ).fetchall()
        for t in triples:
            model_name = t["model_name"]
            symbol = t["symbol"]
            window = t["market_window_seconds"]
            rows = self._db_conn.execute(
                "SELECT net_pnl, simulated_stake_usdc, pred_proba_calibrated,"
                " prediction_correct"
                " FROM paper_trades"
                " WHERE model_name = ? AND symbol = ? AND market_window_seconds = ?"
                "   AND resolution_type = 'native' AND resolved = 1 AND warmup = 0"
                " ORDER BY ts_contract_open_ms DESC LIMIT ?",
                (model_name, symbol, window, window_size),
            ).fetchall()
            if not rows:
                continue
            # Reverse to oldest-first for EWMA
            rows = list(reversed(rows))
            ev_values = [(r["net_pnl"] or 0.0) / (r["simulated_stake_usdc"] or 1.0)
                          for r in rows]
            cal_rows = [
                (r["pred_proba_calibrated"], bool(r["prediction_correct"] or 0))
                for r in rows
            ]
            win_rate = sum(1 for r in rows if r["prediction_correct"]) / len(rows)
            writer.write_snapshot(
                model_name=model_name, symbol=symbol,
                market_window_seconds=window,
                window_size=window_size,
                rolling_ev=compute_rolling_ev(ev_values),
                recency_weighted_ev=compute_recency_weighted_ev(ev_values, alpha=0.05),
                rolling_win_rate=win_rate,
                brier_score=compute_brier_score(cal_rows),
                calibration_error=compute_calibration_error(cal_rows),
                sample_count=len(rows),
            )

    def refresh_price_ranges(self) -> None:
        """Refresh dynamic price ranges for tracked symbols from klines."""
        from data.range_computer import compute_mid_price_range
        self._price_ranges = {}
        tracked = getattr(self, "_tracked_symbols", PREDICTION_SYMBOLS)
        for symbol in tracked:
            try:
                price_range = compute_mid_price_range(
                    symbol, self._db_conn, lookback_days=7
                )
                if price_range:
                    self._price_ranges[symbol] = price_range
            except Exception as e:
                logger.exception("range_compute_failed",
                                extra={"symbol": symbol, "err": str(e)})

    @property
    def _overlap_writer(self):
        if not hasattr(self, "_overlap_writer_cache"):
            from trading.overlap_writer import OverlapWriter
            self._overlap_writer_cache = OverlapWriter(self._db_conn)
        return self._overlap_writer_cache

    def record_overlap_for_boundary(
        self, *, ts_contract_open_ms: int, symbol: str,
        market_window_seconds: int, scores,
    ) -> None:
        gen = self.registry_state.current_generation()
        self._overlap_writer.record_boundary(
            ts_contract_open_ms=ts_contract_open_ms,
            symbol=symbol,
            market_window_seconds=market_window_seconds,
            registry_load_generation=gen,
            scores=scores,
        )

    def kalshi_dispatch_eligible(
        self, *, model_name: str, symbol: str, market_window_seconds: int,
    ) -> bool:
        """Registry-driven Kalshi gate.

        Replaces v2 hardcoded check
        ``model_name == 'h300' and symbol == 'BTCUSDT' and duration == 900``.
        Plan B replaces config.PAPER_TRADING["model_metadata"] with
        model_registry.json — call site stays the same.
        """
        meta = config.PAPER_TRADING["model_metadata"].get(model_name)
        if meta is None:
            return False
        if not meta.get("kalshi_dispatch_enabled", False):
            return False
        if symbol != meta["symbol"]:
            return False
        # Only native horizon dispatches.
        if market_window_seconds != meta["training_horizon_seconds"]:
            return False
        return True

    def _direction_for(self, prediction_id: str) -> str:
        row = self._db_conn.execute(
            "SELECT pred_direction FROM predictions WHERE prediction_id = ?",
            (prediction_id,),
        ).fetchone()
        return row["pred_direction"] if row else "up"

    @staticmethod
    def _compute_outcome(direction, price_open, price_close):
        if price_close > price_open:
            result = "up"
        elif price_close < price_open:
            result = "down"
        else:
            result = "flat"
        correct = (result == direction)
        return result, correct


    def _hydrate_running_pnl(self) -> None:
        """Restore running P&L from existing trade ledger for circuit breaker continuity."""
        from trading.ledger import read_records
        for model_name in self.models:
            trades_path = self.log_dir / f"paper_trades_{model_name}.jsonl"
            total_pnl = 0.0
            if trades_path.exists():
                for rec in read_records(trades_path):
                    if rec.get("record_type") == "trade_resolution":
                        total_pnl += rec.get("net_pnl", 0.0)
            self._running_pnl[model_name] = total_pnl
            if total_pnl != 0.0:
                logger.info("Hydrated running P&L for %s: $%.2f", model_name, total_pnl)

    def _check_filters(
        self,
        model_name: str,
        pred_proba: float,
        pred_direction: str,
        p_market: float | None,
        relative_spread: float | None,
        symbol: str | None = None,
    ) -> str | None:
        """
        Evaluate all filter conditions. Returns the first suppress_reason
        that fires, or None if the trade should proceed.

        Filter evaluation order (first match wins):
          0. Pause trading (emergency kill switch)
          1. Confidence gate (with per-symbol override)
          2. Circuit breaker
          3. CLOB divergence gate
          4. Volatility regime filter
        """
        f = self.filters
        p_side = pred_proba if pred_proba > 0.5 else (1 - pred_proba)

        # 0. Pause trading — emergency kill switch
        if f["pause_trading"]:
            return "paused"

        # 1. Confidence gate (with per-symbol override)
        if f["confidence_gate_enabled"]:
            # Check per-symbol threshold first, fall back to global
            per_sym = f.get("per_symbol_confidence", {})
            threshold = per_sym.get(symbol, f["confidence_threshold"]) if symbol else f["confidence_threshold"]
            if p_side <= threshold:
                return "below_confidence"

        # 2. Circuit breaker — cumulative drawdown protection
        if f["circuit_breaker_enabled"]:
            running = self._running_pnl.get(model_name, 0.0)
            if running <= f["circuit_breaker_max_drawdown"]:
                return "circuit_breaker"

        # 3. CLOB divergence gate — require edge over market
        if f["clob_divergence_enabled"] and p_market is not None:
            # Compute the model's edge: how much higher is our confidence
            # vs what the market is already pricing in?
            if pred_direction == "up":
                edge = pred_proba - p_market
            else:
                edge = (1 - pred_proba) - (1 - p_market)
            if edge < f["clob_divergence_min_edge"]:
                return "clob_divergence"

        # 4. Volatility regime filter — wide spreads = chaos
        if f["volatility_enabled"] and relative_spread is not None:
            if relative_spread > f["volatility_max_spread"]:
                return "high_volatility"

        return None

    def _is_in_warmup(self) -> bool:
        """Check if we're still in the 30-minute MAD warmup period."""
        if self._first_data_time_ms is None:
            return True
        elapsed_ms = int(time.time() * 1000) - self._first_data_time_ms
        return elapsed_ms < MAD_WARMUP_SECONDS * 1000

    def _compute_stake(
        self,
        model_name: str,
        pred_proba: float,
        pred_direction: str,
        p_market: float | None,
    ) -> float:
        """
        Compute the stake for a trade. Returns SIMULATED_STAKE_USDC (flat $10)
        when Kelly sizing is disabled or p_market is unavailable.

        Kelly formula for binary options:
          edge = p_model_side - p_market_side
          odds = (1 / buy_price) - 1
          kelly_fraction = edge / odds  (capped at [0, 1])
          stake = bankroll * kelly_fraction * kelly_multiplier
        """
        f = self.filters
        if not f["kelly_sizing_enabled"] or p_market is None or p_market <= 0 or p_market >= 1:
            return SIMULATED_STAKE_USDC

        # Determine buy price and model's probability on the side we're buying
        if pred_direction == "up":
            buy_price = p_market
            p_model_side = pred_proba
        else:
            buy_price = 1 - p_market
            p_model_side = 1 - pred_proba

        # Edge: how much our model exceeds the market on this side
        edge = p_model_side - buy_price
        if edge <= 0:
            # Negative edge — Kelly says don't bet. Return minimum flat stake
            # so the trade still executes (filters already approved it).
            return SIMULATED_STAKE_USDC

        # Odds: payout ratio for binary option (shares pay $1 each)
        odds = (1 / buy_price) - 1
        if odds <= 0:
            return SIMULATED_STAKE_USDC

        kelly_raw = edge / odds

        # Apply fraction multiplier (half-Kelly = 0.5)
        kelly_adj = kelly_raw * f["kelly_fraction"]
        kelly_adj = max(0, min(kelly_adj, 1.0))  # clamp to [0, 1]

        # Sync bankroll with Kalshi if available, otherwise use running P&L
        live_bankroll = getattr(self, "_current_kalshi_bankroll", None)
        if live_bankroll is not None and live_bankroll > 0:
            bankroll = live_bankroll
        else:
            bankroll = f["kelly_bankroll_usdc"] + self._running_pnl.get(model_name, 0.0)

        if bankroll <= 0:
            return SIMULATED_STAKE_USDC

        stake = bankroll * kelly_adj

        # Hard cap
        stake = min(stake, f["kelly_max_bet_usdc"])

        # Floor at $1 minimum
        stake = max(stake, 1.0)

        logger.debug(
            "[%s] Kelly: edge=%.4f odds=%.2f raw=%.4f adj=%.4f bankroll=$%.2f stake=$%.2f",
            model_name, edge, odds, kelly_raw, kelly_adj, bankroll, stake,
        )
        return round(stake, 2)

    async def _dispatch_kalshi_live(
        self,
        *,
        symbol: str,
        duration_sec: int,
        boundary_ms: int,
        pred_proba: float,
        pred_direction: str,
        paper_stake_usd: float = SIMULATED_STAKE_USDC,
        features: Optional[dict] = None,
        model_name: str = "h300",
    ) -> None:
        """
        Dispatch a finalized signal to Kalshi for live execution.

        Called only on 15-minute boundaries (enforced at the call site) so
        the model's 900s prediction window aligns exactly with the Kalshi
        15M contract window.

        All gating (kill switch, allow-list, confidence, sizing) lives in
        KalshiLiveTrader. This method only:
          1. Fetches the currently open KXBTC15M market.
          2. Fetches the orderbook midpoint for the YES outcome.
          3. Maps pred_direction → Kalshi side (up→yes, down→no).
          4. Awaits maybe_place_order, which records to the live ledger
             regardless of whether an order is placed.
          5. On ZERO_CONTRACTS, retries up to 5× with 2s delays to let
             market makers post quotes after the dead zone clears.

        Errors are caught and logged so paper trading is never disrupted.
        """
        if self._kalshi_trader is None or self._kalshi_trader._rest is None:
            return
        try:
            # Refresh calibration map if it changed on disk
            self._kalshi_trader._calibrator.reload()

            ticker = await self._resolve_kalshi_ticker_for_boundary(boundary_ms, duration_sec)
            if ticker is None:
                logger.info(
                    "kalshi dispatch: no market matches boundary close_unix=%d",
                    boundary_ms // 1000 + duration_sec,
                )
                return

            side = "yes" if pred_direction == "up" else "no"
            max_attempts = 5
            for attempt in range(max_attempts):
                ob = await self._kalshi_trader._rest.get_orderbook(ticker)
                yes_levels = ob.get("yes") or []
                no_levels = ob.get("no") or []
                yes_mid = self._midpoint_from_levels(yes_levels, no_levels)
                if yes_mid is None:
                    if attempt < max_attempts - 1:
                        logger.info("kalshi dispatch: empty book for %s, retry %d/%d",
                                    ticker, attempt + 1, max_attempts)
                        await asyncio.sleep(2)
                        continue
                    logger.info("kalshi dispatch: empty book for %s after %d attempts",
                                ticker, max_attempts)
                    return
                # Defense: reject extreme-priced markets
                if yes_mid < 0.05 or yes_mid > 0.95:
                    logger.info(
                        "kalshi dispatch: skipping extreme price yes_mid=%.4f on %s",
                        yes_mid, ticker,
                    )
                    return

                logger.info(
                    "kalshi dispatch: %s %s on %s (conf=%.4f, yes_mid=%.2f, attempt=%d)",
                    side.upper(), pred_direction, ticker, pred_proba, yes_mid, attempt + 1,
                )
                result = await self._kalshi_trader.maybe_place_order(
                    symbol=symbol,
                    duration_sec=duration_sec,
                    boundary_ts=boundary_ms // 1000,
                    model_p=pred_proba,
                    side=side,
                    ticker=ticker,
                    market_yes_price=yes_mid,
                    paper_stake_usd=paper_stake_usd,
                    features=features,
                    model_name=model_name,
                    pred_direction=pred_direction,
                )

                # If ZERO_CONTRACTS, the market price may shift — retry
                if result.reason == "GATED_ZERO_CONTRACTS" and attempt < max_attempts - 1:
                    logger.info(
                        "kalshi dispatch: ZERO_CONTRACTS on %s, retrying in 2s (%d/%d)",
                        ticker, attempt + 1, max_attempts,
                    )
                    await asyncio.sleep(2)
                    continue

                # Any other result (PLACED, GATED_*, ERROR) — stop retrying
                break

        except Exception as e:
            logger.warning("kalshi dispatch error (paper continues): %s", e)

    async def _resolve_kalshi_ticker_for_boundary(self, boundary_ms: int, market_window_seconds: int = 900) -> str | None:
        """Return the Kalshi ticker whose close_time matches the contract that
        spans (boundary_ms, boundary_ms + market_window_seconds].

        Kalshi rollover quirk (empirically measured 2026-05-03 23:14-23:15 UTC):
          - At boundary T, the OLD contract (close=T) stays in the active list
            for ~39 seconds AFTER T.
          - The NEW contract (close=T+market_window_seconds) is NOT in the active list during
            that window — neither active nor reachable via no-status-filter
            queries.
          - At ~T+39s, Kalshi flips: OLD disappears, NEW appears as active.

        So we retry every 4 seconds for up to 60 seconds, looking for an
        exact close_time match against (boundary_ts + market_window_seconds). Once found we
        return the ticker and trading still has ~14 min remaining.
        """
        if self._kalshi_trader is None or self._kalshi_trader._rest is None:
            return None
        target_close_unix = boundary_ms // 1000 + market_window_seconds

        from datetime import datetime
        max_attempts = 16  # 16 × 4 sec = 64 sec total window
        for attempt in range(max_attempts):
            try:
                markets = await self._kalshi_trader._rest.get_active_tickers(
                    series_ticker=self._kalshi_trader.config.series_ticker,
                )
            except Exception as e:
                logger.warning("kalshi ticker discovery failed (attempt %d): %s", attempt, e)
                await asyncio.sleep(4)
                continue

            for m in markets or []:
                close_iso = m.get("close_time")
                if not close_iso:
                    continue
                try:
                    close_unix = int(datetime.fromisoformat(
                        close_iso.replace("Z", "+00:00")
                    ).timestamp())
                except (ValueError, TypeError):
                    continue
                if close_unix == target_close_unix:
                    if attempt > 0:
                        logger.info(
                            "kalshi resolver: matched %s after %d retries (~%ds)",
                            m.get("ticker"), attempt, attempt * 4,
                        )
                    return m.get("ticker")

            # No match yet — log the gap once and keep retrying
            if attempt == 0:
                observed = [m.get("ticker") for m in (markets or [])[:3]]
                logger.info(
                    "kalshi resolver: waiting for rollover, target_close=%s, observed=%s",
                    datetime.fromtimestamp(target_close_unix).isoformat(),
                    observed,
                )
            await asyncio.sleep(4)

        logger.warning(
            "kalshi resolver: gave up after %ds, no market with close_unix=%d",
            max_attempts * 4, target_close_unix,
        )
        return None

    @staticmethod
    def _midpoint_from_levels(yes_levels: list, no_levels: list) -> float | None:
        """Compute YES-side midpoint from Kalshi orderbook levels.

        Levels are normalised by kalshi.py to [[price_float, size_float], ...].
        Prices are in [0, 1] (dollar fraction).

        YES best bid  = max price across yes_levels with size > 0
        YES best ask  = 1.0 - max(no_levels price) (Kalshi reciprocal)
        Midpoint      = (yes_bid + yes_ask) / 2
        """
        yes_bid = max((lvl[0] for lvl in yes_levels if lvl[1] > 0), default=None)
        no_bid = max((lvl[0] for lvl in no_levels if lvl[1] > 0), default=None)
        if yes_bid is None and no_bid is None:
            return None
        if yes_bid is None:
            assert no_bid is not None
            return 1.0 - no_bid
        if no_bid is None:
            return yes_bid
        yes_ask = 1.0 - no_bid
        return (yes_bid + yes_ask) / 2.0

    def _on_book_update(self, symbol: str, bids: list, asks: list) -> None:
        """Callback fired on each L2 update from WebSocket."""
        if not bids or not asks:
            return

        # Track first data arrival
        if self._first_data_time_ms is None:
            self._first_data_time_ms = int(time.time() * 1000)
            logger.info("First L2 data received — MAD warmup starts (30 min)")

        # Get exchange timestamp from book manager
        book = self.book_manager.books.get(symbol, {})
        cts_ms = book.get("exchange_ts", int(time.time() * 1000))

        # Feed to feature computer
        self.feature_computer.on_book_update(symbol, bids, asks, cts_ms)

    async def _contract_boundary_loop(self) -> None:
        """Check for 5-minute contract boundaries and trigger predictions."""
        while self._running:
            now_ms = int(time.time() * 1000)
            contract_interval_ms = 300_000  # 5 minutes
            boundary_ms = (now_ms // contract_interval_ms) * contract_interval_ms

            # Are we within ±30s of a boundary we haven't processed?
            near_boundary = abs(now_ms - boundary_ms) <= 30_000 or \
                            abs(now_ms - (boundary_ms + contract_interval_ms)) <= 30_000

            if near_boundary and boundary_ms > self._last_contract_boundary_ms:
                self._last_contract_boundary_ms = boundary_ms

                # Kill switch check — skip all prediction + trade work when engaged
                try:
                    from dashboard_api.services import kill_switch_state as _ks
                    if _ks.is_engaged():
                        logger.warning("kill_switch_engaged_skipping_boundary",
                                       extra=_ks.read_state() or {})
                        await asyncio.sleep(1.0)
                        continue
                except Exception as _ke:
                    logger.exception("kill_switch_check_failed", extra={"err": str(_ke)})

                await self._run_predictions(now_ms, boundary_ms)

                # ── Periodic boundary tasks ──────────────────────────────
                self._boundary_count += 1

                # C4: Refresh decay metrics every 4 boundaries
                if self._boundary_count % self._decay_refresh_interval == 0:
                    try:
                        self.refresh_decay_metrics()
                    except Exception as e:
                        logger.exception("decay_refresh_failed", extra={"err": str(e)})

                # C6: Evaluate lifecycle FSM every 16 boundaries
                if self._boundary_count % self._lifecycle_interval == 0:
                    try:
                        transitions = evaluate_lifecycle_transitions(self._db_conn, now_ms=now_ms)
                        for t in transitions:
                            logger.info("lifecycle_transition", extra=t._asdict())
                        if transitions:
                            self.registry_state.increment("lifecycle_transitions",
                                                        {"count": len(transitions)})
                    except Exception as e:
                        logger.exception("lifecycle_eval_failed", extra={"err": str(e)})

            # Check pending resolutions
            await self._check_trade_resolutions_v3(now_ms)
            await self._check_prediction_resolutions_v3(now_ms)

            await asyncio.sleep(1.0)

    async def _run_predictions(self, now_ms: int, boundary_ms: int) -> None:
        """Score all symbols with all models at a contract boundary."""
        boundary_ts = boundary_ms // 1000

        # C5: Initialize accumulator for overlap recording
        per_boundary_scores: dict = {}

        # Fetch live Kalshi bankroll to sync paper trader bankroll
        self._current_kalshi_bankroll = None
        if getattr(self, "_kalshi_trader", None) is not None:
            try:
                self._current_kalshi_bankroll = await self._kalshi_trader.effective_bankroll_usd()
            except Exception as e:
                logger.warning("Failed to fetch Kalshi bankroll for paper sync: %s", e)

        for symbol in PREDICTION_SYMBOLS:
            if not self.feature_computer.is_warmed_up(symbol):
                logger.info("Skipping %s — not warmed up yet", symbol)
                continue

            trade_eligible = symbol in TRADE_SYMBOLS

            # Get feature bar
            bar = self.feature_computer.get_1min_bar(symbol)
            if bar is None:
                logger.warning("No 1-min bar for %s at boundary", symbol)
                continue

            # Mid-price range check
            mid_price = bar.get("mid_price", 0)
            self._check_mid_price_range(symbol, mid_price)

            # Query Polymarket p_market once per symbol per boundary
            p_market = None
            if self._http_session:
                try:
                    p_market = await get_p_market(self._http_session, symbol, boundary_ts)
                    if p_market is not None:
                        logger.debug("[%s] p_market=%.3f", symbol, p_market)
                except Exception as e:
                    logger.warning("p_market query failed for %s: %s", symbol, e)

            # Score with each model
            for model_name, model in self.models.items():
                meta = config.PAPER_TRADING["model_metadata"][model_name]
                features = {col: bar.get(col, 0.0) for col in self.feature_names[model_name]}
                feature_vec = np.array(
                    [features[col] for col in self.feature_names[model_name]],
                    dtype=np.float64,
                ).reshape(1, -1)

                pred_proba = float(model.predict(feature_vec)[0])
                pred_direction = "up" if pred_proba > 0.5 else "down"

                # Try to get calibrated prediction from CalibratorRegistry
                pred_proba_calibrated = pred_proba
                try:
                    calibrator = self.calibrators.get(model_name)
                    if calibrator:
                        pred_proba_calibrated = calibrator.calibrate(pred_proba)
                except Exception as e:
                    logger.debug("calibration_failed for %s: %s", model_name, e)

                # In APFS mode, lower the confidence floor so APFS
                # can evaluate the full prediction range.
                _apfs_active = (
                    getattr(self, "_kalshi_trader", None) is not None
                    and self._kalshi_trader._apfs_enabled
                )
                _ct = APFS_PAPER_CONFIDENCE_FLOOR if _apfs_active else self.filters["confidence_threshold"]
                above_threshold = pred_proba > _ct or pred_proba < (1 - _ct)
                _active_filter_mode = "apfs" if _apfs_active else "confidence_gate"

                ts_model_ran_ms = int(time.time() * 1000)
                in_warmup = self._is_in_warmup()

                # Compute metadata for prediction record
                pred_dt = datetime.fromtimestamp(ts_model_ran_ms / 1000, tz=timezone.utc)
                utc_hour = pred_dt.hour
                day_of_week = pred_dt.weekday()  # 0=Monday, 6=Sunday

                # Compute divergence
                p_model_minus_market = None
                if p_market is not None:
                    p_model_minus_market = round(pred_proba - p_market, 6)

                # Log prediction (always — even during warmup, even for non-trade symbols)
                prediction_id = self._emit_prediction_rows(
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
                self._prediction_count += 1

                # C3: Wire _evaluate_paper_filters to gate trade emission
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
                    "confidence_threshold": _ct,
                    "active_filter_mode": _active_filter_mode,
                    "p_market": p_market,
                    "regime_features": features,
                }
                verdict = self._evaluate_paper_filters(filter_ctx)
                if not verdict.passed:
                    logger.info("paper_filter_blocked",
                                extra={"reasons": verdict.reasons,
                                       "prediction_id": prediction_id})
                    self._record_compact_decision(
                        prediction_id=prediction_id,
                        outcome="gated",
                        reason="paper_filter",
                        ev_estimate=None,
                        kelly_fraction_capped=None,
                        final_size_usdc=None,
                        order_type=None,
                    )
                    continue  # skip to next symbol/model

                # C5: Accumulate score for overlap recording
                try:
                    from trading.overlap_writer import ModelScore
                    per_boundary_scores[(model_name, symbol)] = ModelScore(
                        proba=pred_proba_calibrated,
                        direction=pred_direction,
                        ev=None,  # Will be filled in after trade resolution
                    )
                except Exception as e:
                    logger.debug("overlap_score_accumulation_failed: %s", e)

                # Suppress trades during warmup
                if in_warmup:
                    self._record_compact_decision(
                        prediction_id=prediction_id,
                        outcome="gated",
                        reason="warmup",
                        ev_estimate=None,
                        kelly_fraction_capped=None,
                        final_size_usdc=None,
                        order_type=None,
                    )
                    if above_threshold and trade_eligible:
                        warmup_elapsed = (ts_model_ran_ms - (self._first_data_time_ms or ts_model_ran_ms)) / 1000
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

                # UTC blackout: suppress H60 trades during overnight hours
                if model_name in H60_BLACKOUT_MODELS and utc_hour in H60_BLACKOUT_HOURS:
                    if above_threshold:
                        # Log the suppression to the ledger so outcome is still tracked
                        self.sqlite_ledger.log_paper_trade(
                            prediction_id=prediction_id,
                            envelope=self._build_envelope(model_name, platform="paper"),
                            symbol=symbol,
                            market_window_seconds=meta["training_horizon_seconds"],  # native horizon
                            resolution_type="native",
                            ts_model_ran_ms=ts_model_ran_ms,
                            ts_contract_open_ms=boundary_ms,
                            ts_resolve_at_ms=boundary_ms + meta["training_horizon_seconds"] * 1000,
                            pred_proba_raw=pred_proba,
                            pred_proba_calibrated=pred_proba,
                            pred_direction=pred_direction,
                            confidence_threshold_used=self.filters["confidence_threshold"],
                            simulated_stake_usdc=SIMULATED_STAKE_USDC,
                            decision_outcome="suppressed",
                            decision_reason="utc_blackout",
                            ev_estimate=None,
                            kelly_fraction_capped=None,
                            final_size_usdc=SIMULATED_STAKE_USDC,
                            order_type=None,
                            warmup=in_warmup,
                            platform="paper",
                            p_market=p_market,
                        )
                        self._record_compact_decision(
                            prediction_id=prediction_id,
                            outcome="suppressed",
                            reason="utc_blackout",
                            ev_estimate=None,
                            kelly_fraction_capped=None,
                            final_size_usdc=0.0,
                            order_type="skipped",
                        )
                        logger.info(
                            "[%s] %s: proba=%.4f dir=%s SUPPRESSED (utc_blackout %02d:00)",
                            model_name, symbol, pred_proba, pred_direction, utc_hour,
                        )
                    continue

                # (Kalshi dispatch moved AFTER paper trade logic so Kalshi
                #  trades if and only if the paper trader trades. See below.)

                # ── Run filter pipeline ────────────────────────
                rel_spread = features.get("relative_spread", None)
                filter_reason = self._check_filters(
                    model_name=model_name,
                    pred_proba=pred_proba,
                    pred_direction=pred_direction,
                    p_market=p_market,
                    relative_spread=rel_spread,
                    symbol=symbol,
                )

                if filter_reason:
                    # Filter fired — log the suppressed trade for analysis, don't schedule resolution
                    if filter_reason != "below_confidence":
                        # Only log a trade entry for non-confidence filters
                        # (below_confidence means we wouldn't have traded anyway)
                        self.sqlite_ledger.log_paper_trade(
                            prediction_id=prediction_id,
                            envelope=self._build_envelope(model_name, platform="paper"),
                            symbol=symbol,
                            market_window_seconds=meta["training_horizon_seconds"],  # native horizon
                            resolution_type="native",
                            ts_model_ran_ms=ts_model_ran_ms,
                            ts_contract_open_ms=boundary_ms,
                            ts_resolve_at_ms=boundary_ms + meta["training_horizon_seconds"] * 1000,
                            pred_proba_raw=pred_proba,
                            pred_proba_calibrated=pred_proba,
                            pred_direction=pred_direction,
                            confidence_threshold_used=self.filters["confidence_threshold"],
                            simulated_stake_usdc=SIMULATED_STAKE_USDC,
                            decision_outcome="suppressed",
                            decision_reason=filter_reason,
                            ev_estimate=None,
                            kelly_fraction_capped=None,
                            final_size_usdc=SIMULATED_STAKE_USDC,
                            order_type=None,
                            warmup=in_warmup,
                            platform="paper",
                            p_market=p_market,
                        )
                        self._record_compact_decision(
                            prediction_id=prediction_id,
                            outcome="suppressed",
                            reason=filter_reason,
                            ev_estimate=None,
                            kelly_fraction_capped=None,
                            final_size_usdc=0.0,
                            order_type="skipped",
                        )
                        logger.info(
                            "[%s] %s: proba=%.4f dir=%s SUPPRESSED (%s)",
                            model_name, symbol, pred_proba, pred_direction, filter_reason,
                        )
                    else:
                        self._record_compact_decision(
                            prediction_id=prediction_id,
                            outcome="suppressed",
                            reason="below_confidence",
                            ev_estimate=None,
                            kelly_fraction_capped=None,
                            final_size_usdc=0.0,
                            order_type="skipped",
                        )
                        logger.debug(
                            "[%s] %s: proba=%.4f (below_confidence)", model_name, symbol, pred_proba,
                        )
                    continue

                # ── Trade passes all filters — execute ────────
                if above_threshold:
                    # Compute stake (Kelly or flat depending on config)
                    stake = self._compute_stake(
                        model_name=model_name,
                        pred_proba=pred_proba,
                        pred_direction=pred_direction,
                        p_market=p_market,
                    )

                    # Record the compact decision for executed trade
                    self._record_compact_decision(
                        prediction_id=prediction_id,
                        outcome="executed",
                        reason=None,
                        ev_estimate=None,
                        kelly_fraction_capped=None,
                        final_size_usdc=stake,
                        order_type="maker",
                    )

                    boundary_sec = boundary_ms // 1000
                    is_15m_boundary = (boundary_sec % 900 == 0)

                    for duration in CONTRACT_DURATIONS:
                        # Check if this duration is suppressed for this model
                        suppressed_durs = H300_SUPPRESS_DURATIONS.get(model_name, set())
                        suppress_reason = "contract_mismatch" if duration in suppressed_durs else None

                        # 900s trades must only occur on 15-minute boundaries
                        # (xx:00, xx:15, xx:30, xx:45) to align with Kalshi 15M
                        # contract windows. Suppress at xx:05, xx:10, etc.
                        if duration == 900 and not is_15m_boundary and not suppress_reason:
                            suppress_reason = "non_15m_boundary"

                        self.sqlite_ledger.log_paper_trade(
                            prediction_id=prediction_id,
                            envelope=self._build_envelope(model_name, platform="paper"),
                            symbol=symbol,
                            market_window_seconds=meta["training_horizon_seconds"],  # native horizon
                            resolution_type="native",
                            ts_model_ran_ms=ts_model_ran_ms,
                            ts_contract_open_ms=boundary_ms,
                            ts_resolve_at_ms=boundary_ms + duration * 1000,
                            pred_proba_raw=pred_proba,
                            pred_proba_calibrated=pred_proba,
                            pred_direction=pred_direction,
                            confidence_threshold_used=self.filters["confidence_threshold"],
                            simulated_stake_usdc=stake,
                            decision_outcome="executed" if not suppress_reason else "suppressed",
                            decision_reason=suppress_reason,
                            ev_estimate=None,
                            kelly_fraction_capped=None,
                            final_size_usdc=stake,
                            order_type=None,
                            warmup=in_warmup,
                            platform="paper",
                            p_market=p_market,
                        )

                        if suppress_reason:
                            # Suppressed: logged but not scheduled for resolution
                            continue

                        self._trade_count += 1

                        # ── Live Kalshi dispatch ────────────────────────
                        # Dispatch to Kalshi when paper trade is placed on a
                        # 15-min boundary for h300 BTCUSDT 900s. This ensures
                        # Kalshi trades 1:1 with paper trades.
                        if self.kalshi_dispatch_eligible(
                            model_name=model_name,
                            symbol=symbol,
                            market_window_seconds=duration,
                        ):
                            if not self.is_in_warmup(ts_model_ran_ms):
                                asyncio.create_task(self._dispatch_kalshi_live(
                                    symbol=symbol,
                                    duration_sec=duration,
                                    boundary_ms=boundary_ms,
                                    pred_proba=pred_proba,
                                    pred_direction=pred_direction,
                                    paper_stake_usd=stake,
                                    features=features,
                                    model_name=model_name,
                                ))

                    logger.info(
                        "[%s] %s %s: proba=%.4f dir=%s TRADE",
                        model_name, symbol, "🔼" if pred_direction == "up" else "🔽",
                        pred_proba, pred_direction,
                    )
                else:
                    logger.debug(
                        "[%s] %s: proba=%.4f (below threshold)", model_name, symbol, pred_proba,
                    )

        # C5: Record overlap scores for the boundary
        try:
            if per_boundary_scores:
                self.record_overlap_for_boundary(
                    ts_contract_open_ms=boundary_ms,
                    symbol=PREDICTION_SYMBOLS[0] if PREDICTION_SYMBOLS else "BTCUSDT",
                    market_window_seconds=900,
                    scores=per_boundary_scores,
                )
        except Exception as e:
            logger.exception("overlap_recording_failed", extra={"err": str(e)})

        logger.info(
            "Boundary %s: %d predictions total, %d trades total",
            datetime.fromtimestamp(boundary_ms / 1000, tz=timezone.utc).strftime("%H:%M"),
            self._prediction_count, self._trade_count,
        )


    def _check_mid_price_range(self, symbol: str, mid_price: float) -> None:
        """Warn if mid_price is outside training range."""
        range_ = MID_PRICE_TRAINING_RANGE.get(symbol)
        if range_ and (mid_price < range_[0] or mid_price > range_[1]):
            logger.warning(
                "⚠️  %s mid_price %.2f OUTSIDE training range [%.0f, %.0f]",
                symbol, mid_price, range_[0], range_[1],
            )

    # ── Runtime API Server ─────────────────────────────────────

    async def _start_api_server(self):
        """Start the comprehensive monitoring API server."""
        from trading.api_server import start_api_server
        return await start_api_server(self, self._api_port)

    async def run(self) -> None:
        """Start the paper trading loop."""
        logger.info("=" * 60)
        logger.info("Paper Trader starting")
        logger.info("  Testnet: %s", self.testnet)
        logger.info("  Prediction symbols: %s", PREDICTION_SYMBOLS)
        logger.info("  Trade symbols: %s", TRADE_SYMBOLS)
        logger.info("  Models: %s", list(self.models.keys()))
        logger.info("  MAD warmup: %ds", MAD_WARMUP_SECONDS)
        logger.info("  Log dir: %s", self.log_dir)
        logger.info("  ── Filters ──")
        for k, v in self.filters.items():
            logger.info("    %s: %s", k, v)
        for mn, pnl in self._running_pnl.items():
            if pnl != 0.0:
                logger.info("  Running P&L [%s]: $%.2f", mn, pnl)
        logger.info("=" * 60)

        # Preload EWM state from historical parquets (for V3 mid_price_dev_30d)
        self.feature_computer.preload_ewm()

        # Register book update callback
        self.book_manager.on_update(self._on_book_update)

        # Create aiohttp session for Polymarket / Kalshi REST
        self._http_session = aiohttp.ClientSession()
        logger.info("  HTTP session created (exchange=%s)", EXCHANGE)

        # Initialize Kalshi live trader (default-deny: kill switch off, allow-list narrow)
        if EXCHANGE == "kalshi":
            self._kalshi_trader = KalshiLiveTrader()
            await self._kalshi_trader.connect(self._http_session)
            await self._kalshi_trader.run_tz_diagnostic()
            logger.info("  Kalshi live trader initialized: %s", self._kalshi_trader.status())

        # Start runtime API server
        api_runner = None
        if self._api_port > 0:
            api_runner = await self._start_api_server()

        # Run WebSocket + contract boundary loop concurrently
        try:
            await asyncio.gather(
                self.book_manager.connect_async(),
                self._contract_boundary_loop(),
            )
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        except Exception as e:
            logger.error("Fatal error: %s", e, exc_info=True)
            raise
        finally:
            if api_runner:
                await api_runner.cleanup()
            if self._http_session:
                await self._http_session.close()
            self.book_manager.disconnect()
            logger.info("Paper trader stopped. Predictions: %d, Trades: %d",
                        self._prediction_count, self._trade_count)


def main():
    parser = argparse.ArgumentParser(description="Paper trader — dual model comparison")
    parser.add_argument("--testnet", action="store_true", default=False,
                        help="Use Bybit testnet WebSocket")
    parser.add_argument("--log-dir", type=str, default="/data/logs",
                        help="Log/ledger directory")
    parser.add_argument("--h60-model", type=str,
                        default="/data/models/latest_h60/model.lgb",
                        help="Path to H=60 model")
    parser.add_argument("--h60-v3-model", type=str,
                        default="",
                        help="Path to H=60 V3 (debiased with mid_price_dev_30d) model.")
    parser.add_argument("--h300-model", type=str,
                        default="/data/models/latest_h300/model.lgb",
                        help="Path to H=300 model")
    parser.add_argument("--features-dir", type=str, default="/data/features_v3",
                        help="Path to features_v3 parquets for EWM preload")

    # ── Filter toggle args (all OFF by default except confidence gate) ──
    parser.add_argument("--confidence-threshold", type=float, default=FILTER_CONFIDENCE_THRESHOLD,
                        help="Confidence gate threshold (default: 0.52)")
    parser.add_argument("--no-confidence-gate", action="store_true", default=False,
                        help="Disable the confidence gate entirely")
    parser.add_argument("--enable-circuit-breaker", action="store_true", default=False,
                        help="Enable circuit breaker (drawdown protection)")
    parser.add_argument("--circuit-breaker-max-drawdown", type=float, default=FILTER_CIRCUIT_BREAKER_MAX_DRAWDOWN,
                        help="Max drawdown in USDC before circuit breaker trips (default: -50)")
    parser.add_argument("--enable-clob-divergence", action="store_true", default=False,
                        help="Enable CLOB divergence gate (require edge over market)")
    parser.add_argument("--clob-divergence-min-edge", type=float, default=FILTER_CLOB_DIVERGENCE_MIN_EDGE,
                        help="Min edge (model_prob - market_prob) required (default: 0.02)")
    parser.add_argument("--enable-volatility-filter", action="store_true", default=False,
                        help="Enable volatility regime filter (suppress during wide spreads)")
    parser.add_argument("--volatility-max-spread", type=float, default=FILTER_VOLATILITY_MAX_SPREAD,
                        help="Max relative_spread before filter triggers (default: 0.005)")

    # Kelly sizing
    parser.add_argument("--enable-kelly-sizing", action="store_true", default=False,
                        help="Enable Kelly criterion position sizing")
    parser.add_argument("--kelly-fraction", type=float, default=FILTER_KELLY_FRACTION,
                        help="Kelly fraction multiplier (default: 0.5 = half-Kelly)")
    parser.add_argument("--kelly-max-bet", type=float, default=FILTER_KELLY_MAX_BET_USDC,
                        help="Max bet size in USDC (default: 50)")
    parser.add_argument("--kelly-bankroll", type=float, default=FILTER_KELLY_BANKROLL_USDC,
                        help="Virtual bankroll in USDC (default: 1000)")

    # Pause / per-symbol / API
    parser.add_argument("--pause-trading", action="store_true", default=False,
                        help="Start with trading paused (emergency kill switch)")
    parser.add_argument("--per-symbol-confidence", type=str, default="",
                        help='JSON map of per-symbol thresholds, e.g. \'{"BTCUSDT":0.53,"SOLUSDT":0.58}\'')
    parser.add_argument("--api-port", type=int, default=API_SERVER_PORT,
                        help="Runtime API server port (0 to disable, default: 8080)")

    args = parser.parse_args()

    # Parse per-symbol confidence
    per_symbol_conf = {}
    if args.per_symbol_confidence:
        try:
            per_symbol_conf = json.loads(args.per_symbol_confidence)
        except json.JSONDecodeError as e:
            logger.error("Invalid --per-symbol-confidence JSON: %s", e)
            sys.exit(1)

    # Build filter config from CLI args
    filter_config = {
        "confidence_gate_enabled": not args.no_confidence_gate,
        "confidence_threshold": args.confidence_threshold,
        "circuit_breaker_enabled": args.enable_circuit_breaker,
        "circuit_breaker_max_drawdown": args.circuit_breaker_max_drawdown,
        "clob_divergence_enabled": args.enable_clob_divergence,
        "clob_divergence_min_edge": args.clob_divergence_min_edge,
        "volatility_enabled": args.enable_volatility_filter,
        "volatility_max_spread": args.volatility_max_spread,
        "kelly_sizing_enabled": args.enable_kelly_sizing,
        "kelly_fraction": args.kelly_fraction,
        "kelly_max_bet_usdc": args.kelly_max_bet,
        "kelly_bankroll_usdc": args.kelly_bankroll,
        "pause_trading": args.pause_trading,
        "per_symbol_confidence": per_symbol_conf,
        "api_port": args.api_port,
    }

    model_paths = {
        "h60": args.h60_model,
        "h300": args.h300_model,
    }
    if args.h60_v3_model:
        model_paths["h60_v3"] = args.h60_v3_model

    # Verify model files exist
    for name, path in model_paths.items():
        if not Path(path).exists():
            logger.error("Model file not found: %s (%s)", path, name)
            sys.exit(1)

    trader = PaperTrader(
        model_paths=model_paths,
        log_dir=args.log_dir,
        testnet=args.testnet,
        features_dir=args.features_dir,
        filter_config=filter_config,
    )

    # Handle signals gracefully
    def handle_signal(sig, frame):
        logger.info("Signal %s received, shutting down...", sig)
        trader._running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    asyncio.run(trader.run())


if __name__ == "__main__":
    main()
