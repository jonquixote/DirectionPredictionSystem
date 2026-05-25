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
from trading.metric_writers import MetricWriters
from trading.provenance_builder import ProvenanceBuilder
from trading.resolution_checker import ResolutionChecker
from trading.kalshi_dispatcher import KalshiDispatcher
from trading.boundary_scorer import BoundaryScorer

EXCHANGE = _os.environ.get("EXCHANGE", "kalshi").lower()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("paper_trader")

# ── Configuration ──────────────────────────────────────────────

PREDICTION_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
TRADE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
# CONFIDENCE_THRESHOLD removed — use self.filters["confidence_threshold"] (dynamic, dashboard-controllable)
# Legacy references point to the filters dict; see _run_predictions().
CONTRACT_DURATIONS = [300, 900, 1800]
SIMULATED_STAKE_USDC = 10.0
POLYMARKET_FEE_COEFFICIENT = 0.072  # crypto taker fee: fee = shares * price * 0.072 * p * (1-p)
MIN_FEATURE_WARMUP_SECONDS = 120   # feature buffer fill
MAD_WARMUP_SECONDS = 1800          # 30 minutes for MAD normalization convergence

# UTC blackout: H60 models suppress paper trades during these hours
# (predictions still logged for all models at all hours)
H60_BLACKOUT_HOURS = set(range(21, 24)) | set(range(0, 4))  # 21:00-03:59 UTC
H60_BLACKOUT_MODELS = {"h60", "h60_v2", "h60_v3"}

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
            "ev_threshold": fc.get("ev_threshold", 0.0),
            "max_book_age_seconds": fc.get("max_book_age_seconds", 30),
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

        self.resolution_checker = ResolutionChecker(
            db_conn=self._db_conn,
            sqlite_ledger=self.sqlite_ledger,
            feature_computer=self.feature_computer,
            calibrators=self.calibrators,
            pending_queue=self.pending_queue,
        )

        self._regime_thresholds_path = os.environ.get(
            "REGIME_THRESHOLDS_PATH",
            "/data/regime_thresholds.json",
        )
        self._regime_thresholds: dict = {}
        self._reload_regime_thresholds()

        # ── Model metadata: registry-first, config fallback ────────
        self._model_meta: dict[str, dict] = {}
        _cfg_meta = config.PAPER_TRADING.get("model_metadata", {})
        for name in model_paths:
            reg = self._db_conn.execute(
                "SELECT symbol, training_horizon_seconds, feature_version, "
                "train_window_start, train_window_end, train_days, "
                "platform_active_json, filter_config_json, "
                "fleet_version, live_eligible, kelly_multiplier, tier "
                "FROM model_registry WHERE name=?",
                (name,),
            ).fetchone()
            if reg:
                pa = {}
                if reg["platform_active_json"]:
                    try:
                        import json as _json
                        pa = _json.loads(reg["platform_active_json"])
                    except Exception:
                        pa = {}
                fc = {}
                if reg["filter_config_json"]:
                    try:
                        import json as _json
                        fc = _json.loads(reg["filter_config_json"])
                    except Exception:
                        fc = {}
                self._model_meta[name] = {
                    "symbol": reg["symbol"],
                    "training_horizon_seconds": reg["training_horizon_seconds"],
                    "feature_version": reg["feature_version"] or "v3",
                    "train_window_start": reg["train_window_start"] or "",
                    "train_window_end": reg["train_window_end"] or "",
                    "train_cutoff": reg["train_window_end"] or "",
                    "kalshi_dispatch_enabled": pa.get("kalshi", False),
                    "filter_config": fc,
                    # Phase 5: tier-aware Kelly
                    "kelly_multiplier": float(reg["kelly_multiplier"]) if reg["kelly_multiplier"] is not None else 0.0,
                    "tier": reg["tier"] or "watch",
                }
            elif name in _cfg_meta:
                self._model_meta[name] = dict(_cfg_meta[name])
            else:
                self._model_meta[name] = {
                    "symbol": "BTCUSDT",
                    "training_horizon_seconds": 300,
                    "feature_version": "v3",
                    "train_window_start": "",
                    "train_window_end": "",
                    "train_cutoff": "",
                    "kalshi_dispatch_enabled": False,
                }

        # Compute provenance hashes for each loaded model
        self._model_envelopes: dict[str, dict] = {}
        for name, path in model_paths.items():
            self._model_envelopes[name] = {
                "model_artifact_hash": sha256_file(path),
                "feature_names_hash": feature_names_hash(self.feature_names[name]),
            }

        # Metric writers (decay snapshots, price ranges, overlap records)
        self.metric_writers = MetricWriters(
            db_conn=self._db_conn,
            registry_state=self.registry_state,
        )

        # Provenance + audit-trail builder (extracted from PaperTrader D.3)
        self.provenance_builder = ProvenanceBuilder(trader=self)

        # Kalshi live order dispatch (extracted from PaperTrader D.4)
        self.kalshi_dispatcher = KalshiDispatcher(trader=self)

        # Boundary scoring + trade execution (extracted from PaperTrader D.5b)
        self.boundary_scorer = BoundaryScorer(trader=self)

        # Boot timestamp for warmup tagging
        self._boot_ts_ms = int(time.time() * 1000)

    def _reload_regime_thresholds(self) -> None:
        """Load regime thresholds: JSON file (primary) or SQLite table (fallback).

        JSON path: $REGIME_THRESHOLDS_PATH (default /data/regime_thresholds.json)
        DB fallback: regime_thresholds table (populated by backfill/nightly cron)
        """
        import json as _json
        p = Path(self._regime_thresholds_path)
        if p.exists():
            try:
                data = _json.loads(p.read_text())
                self._regime_thresholds = data
                logger.info(
                    "regime_thresholds loaded from file: %d symbols",
                    sum(1 for k in data if k != "updated_at"),
                )
                return
            except Exception as e:
                logger.warning("regime_thresholds file load failed: %s", e)

        # Fallback: read from regime_thresholds DB table
        try:
            from regime.threshold_updater import load_thresholds_from_db
            data = load_thresholds_from_db(self._db_conn)
            if data:
                self._regime_thresholds = data
                logger.info(
                    "regime_thresholds loaded from DB: %d symbols", len(data)
                )
            else:
                logger.warning(
                    "regime_thresholds: JSON file absent and DB table empty — "
                    "all regime tags will be 'unknown' until backfill runs"
                )
                self._regime_thresholds = {}
        except Exception as e:
            logger.warning("regime_thresholds DB fallback failed: %s", e)
            self._regime_thresholds = {}

    def _tag_regime(self, symbol: str, regime_features: dict) -> RegimeTags:
        if not self._regime_thresholds:
            return RegimeTags(volatility="unknown", liquidity="unknown",
                              trend="unknown")
        return compute_regime(symbol, regime_features, self._regime_thresholds)

    def _get_meta(self, model_name: str) -> dict:
        if model_name in self._model_meta:
            return self._model_meta[model_name]
        cfg_meta = config.PAPER_TRADING.get("model_metadata", {}).get(model_name)
        if cfg_meta:
            self._model_meta[model_name] = dict(cfg_meta)
            return self._model_meta[model_name]
        raise KeyError(f"no metadata for model '{model_name}'")

    def _reload_model_meta(self) -> None:
        """Re-read filter_config_json for every registered model.

        Called every 16 boundaries from _contract_boundary_loop, or on demand
        via runtime API POST /reload_meta. Updates self._model_meta in place.
        Only filter_config is updated; symbol/horizon/etc. are left unchanged
        (those require a full restart to pick up).
        """
        if not getattr(self, "_db_conn", None):
            return
        try:
            rows = self._db_conn.execute(
                "SELECT name, filter_config_json, kelly_multiplier, tier FROM model_registry"
            ).fetchall()
        except Exception as e:
            logger.warning("reload_model_meta SQL failed: %s", e)
            return
        changed = []
        for row in rows:
            name = row["name"] if hasattr(row, "keys") else row[0]
            fc_json = row["filter_config_json"] if hasattr(row, "keys") else row[1]
            if name not in self._model_meta:
                continue
            try:
                new_fc = json.loads(fc_json or "{}")
            except json.JSONDecodeError:
                logger.warning("model %s has invalid filter_config_json — skipping", name)
                continue
            old_fc = self._model_meta[name].get("filter_config", {})
            if isinstance(old_fc, str):
                try:
                    old_fc = json.loads(old_fc)
                except json.JSONDecodeError:
                    old_fc = {}
            if new_fc != old_fc:
                self._model_meta[name]["filter_config"] = new_fc
                changed.append(name)
            # Phase 5: always refresh kelly_multiplier + tier so demotion/promotion is live
            new_mult = float(row["kelly_multiplier"]) if row["kelly_multiplier"] is not None else 0.0
            new_tier = row["tier"] or "watch"
            self._model_meta[name]["kelly_multiplier"] = new_mult
            self._model_meta[name]["tier"] = new_tier
        if changed:
            logger.info("reloaded filter_config for %d models: %s", len(changed), changed)

    def _reload_fleet(self) -> None:
        """Full fleet hot-reload: sync self.models and self._model_meta with model_registry.

        Detects:
          - Added models (in registry but not loaded): loads .lgb, builds calibrator entry
          - Removed models (loaded but gone/deactivated): removes from self.models + meta
          - Updated filter_config_json for existing models: updates in-place
          - Updated lifecycle_state / enabled flags: updates meta

        Logs counts: added=N removed=M updated=K unchanged=L
        Safe to call at any time; errors per-model are caught and logged so one
        bad model does not abort the rest.
        """
        if not getattr(self, "_db_conn", None):
            logger.warning("_reload_fleet: no db_conn available")
            return

        try:
            rows = self._db_conn.execute(
                "SELECT name, symbol, training_horizon_seconds, feature_version, "
                "train_window_start, train_window_end, train_days, "
                "artifact_path, feature_names_path, "
                "platform_active_json, filter_config_json, "
                "lifecycle_state, paper_active, live_eligible, fleet_version, "
                "kelly_multiplier, tier "
                "FROM model_registry "
                "WHERE paper_active = 1 AND lifecycle_state != 'suspended' "
                "  AND COALESCE(tier, 'gold') != 'retired' "
                "ORDER BY live_eligible DESC, fleet_version DESC, is_baseline DESC, "
                "training_horizon_seconds, name"
            ).fetchall()
        except Exception as e:
            logger.error("_reload_fleet SQL failed: %s", e)
            return

        registry_names: set[str] = set()
        added: list[str] = []
        removed: list[str] = []
        updated: list[str] = []
        unchanged: list[str] = []

        for row in rows:
            name = row["name"]
            registry_names.add(name)

            # Parse platform_active and filter_config
            try:
                pa_dict = json.loads(row["platform_active_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                pa_dict = {}
            try:
                fc_dict = json.loads(row["filter_config_json"] or "{}")
            except (json.JSONDecodeError, TypeError):
                fc_dict = {}

            new_meta = {
                "symbol": row["symbol"],
                "training_horizon_seconds": row["training_horizon_seconds"],
                "feature_version": row["feature_version"] or "v3",
                "train_window_start": row["train_window_start"] or "",
                "train_window_end": row["train_window_end"] or "",
                "train_cutoff": row["train_window_end"] or "",
                "kalshi_dispatch_enabled": pa_dict.get("kalshi", False),
                "filter_config": fc_dict,
                "lifecycle_state": row["lifecycle_state"],
                "paper_active": bool(row["paper_active"]),
                "live_eligible": bool(row["live_eligible"]),
                # Phase 5: tier-aware Kelly
                "kelly_multiplier": float(row["kelly_multiplier"]) if row["kelly_multiplier"] is not None else 0.0,
                "tier": row["tier"] or "watch",
            }

            if name not in self.models:
                # ── New model: load .lgb file ──────────────────────────
                artifact_path = row["artifact_path"]
                if not artifact_path or not Path(artifact_path).exists():
                    logger.warning(
                        "fleet_hot_reload: model %s artifact missing at %s — skipping",
                        name, artifact_path,
                    )
                    registry_names.discard(name)
                    continue
                try:
                    new_booster = lgb.Booster(model_file=artifact_path)
                except Exception as load_err:
                    logger.error(
                        "fleet_hot_reload: failed to load lgb for %s: %s — skipping",
                        name, load_err,
                    )
                    registry_names.discard(name)
                    continue

                # Load feature names
                fn_path = Path(artifact_path).parent / "feature_names.json"
                if row["feature_names_path"] and Path(row["feature_names_path"]).exists():
                    fn_path = Path(row["feature_names_path"])
                if fn_path.exists():
                    try:
                        with open(fn_path) as _f:
                            feat_names = json.load(_f)
                    except Exception:
                        feat_names = V3_FEATURE_COLS
                else:
                    feat_names = V3_FEATURE_COLS

                self.models[name] = new_booster
                self.feature_names[name] = feat_names

                # Compute provenance envelope
                from storage.provenance import sha256_file, feature_names_hash
                self._model_envelopes[name] = {
                    "model_artifact_hash": sha256_file(artifact_path),
                    "feature_names_hash": feature_names_hash(feat_names),
                }

                self._model_meta[name] = new_meta
                logger.info(
                    "fleet_hot_reload: added model %s (%d features) from %s",
                    name, len(feat_names), artifact_path,
                )
                added.append(name)
            else:
                # ── Existing model: check for changes ─────────────────
                old_meta = self._model_meta.get(name, {})
                changed_fields: list[str] = []

                if old_meta.get("filter_config") != fc_dict:
                    changed_fields.append("filter_config")
                if old_meta.get("lifecycle_state") != row["lifecycle_state"]:
                    changed_fields.append("lifecycle_state")
                if old_meta.get("kalshi_dispatch_enabled") != pa_dict.get("kalshi", False):
                    changed_fields.append("kalshi_dispatch_enabled")
                if old_meta.get("paper_active") != bool(row["paper_active"]):
                    changed_fields.append("paper_active")
                if old_meta.get("live_eligible") != bool(row["live_eligible"]):
                    changed_fields.append("live_eligible")

                if changed_fields:
                    self._model_meta[name] = {**old_meta, **new_meta}
                    logger.info(
                        "fleet_hot_reload: updated model %s fields=%s",
                        name, changed_fields,
                    )
                    updated.append(name)
                else:
                    unchanged.append(name)

        # ── Remove models no longer in active registry ─────────────────
        for name in list(self.models.keys()):
            if name not in registry_names:
                del self.models[name]
                self.feature_names.pop(name, None)
                self._model_envelopes.pop(name, None)
                self._model_meta.pop(name, None)
                logger.info("fleet_hot_reload: removed model %s (deactivated or suspended)", name)
                removed.append(name)

        logger.info(
            "fleet_hot_reload complete: added=%d removed=%d updated=%d unchanged=%d",
            len(added), len(removed), len(updated), len(unchanged),
        )

    def _handle_sighup(self, *args) -> None:
        """Signal handler for SIGHUP — sets deferred reload flag.

        Heavy work (DB queries, lgb loads) must not run inside a signal handler.
        Sets _sighup_requested=True; _contract_boundary_loop picks it up at
        the top of the next tick.
        """
        logger.info("SIGHUP received — scheduling fleet hot-reload at next boundary")
        self._sighup_requested = True

    def _capture_policy_dict(self) -> dict:
        # provenance_builder is not yet available during early __init__
        # (called at line 256, before the builder is instantiated at ~355).
        # Fall back to direct filter access so __init__ ordering is preserved.
        if not hasattr(self, "provenance_builder"):
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
        return self.provenance_builder.capture_policy_dict()

    def _build_envelope(self, model_name: str, platform: str) -> ProvenanceEnvelope:
        return self.provenance_builder.build_envelope(model_name, platform=platform)

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
        """Insert evaluation prediction rows for one boundary
        and enqueue each in the pending resolution queue. Returns the
        canonical (300s eval) row's prediction_id.
        """
        meta = self._get_meta(model_name)
        horizon = meta["training_horizon_seconds"]
        rows = plan_resolution_rows(
            boundary_ms=boundary_ms,
            training_horizon_seconds=horizon,
            evaluation_windows=config.EVALUATION_WINDOWS,
        )
        envelope = self._build_envelope(model_name, platform=platform)
        tags = self._tag_regime(symbol, regime_features or {})
        canonical_pid = self.sqlite_ledger.log_prediction_set(
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
    # suffix scheme: <prefix>_<window>e.
        prefix = canonical_pid.rsplit("_", 1)[0]
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
        return canonical_pid

    async def _check_prediction_resolutions_v3(self, now_ms: int) -> None:
        self.resolution_checker._feature_computer = self.feature_computer
        return await self.resolution_checker.check_predictions(now_ms)

    async def _check_trade_resolutions_v3(self, now_ms: int) -> None:
        self.resolution_checker._feature_computer = self.feature_computer
        return await self.resolution_checker.check_trades(now_ms)

    @staticmethod
    def _compute_paper_pnl(*, direction, calibrated_p, stake,
                           price_open, price_close):
        return ResolutionChecker.compute_paper_pnl(
            direction=direction, calibrated_p=calibrated_p, stake=stake,
            price_open=price_open, price_close=price_close,
        )

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
        return self.provenance_builder.record_compact_decision(
            prediction_id=prediction_id,
            outcome=outcome,
            reason=reason,
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
        return self.provenance_builder.write_verbose_trace_for_v2_filters(
            prediction_id=prediction_id,
            envelope=envelope,
            filter_inputs=filter_inputs,
            kelly_raw=kelly_raw,
            kelly_capped=kelly_capped,
            bankroll_used=bankroll_used,
            per_trade_cap_usdc=per_trade_cap_usdc,
            fee_model=fee_model,
            fee_amount=fee_amount,
            platform_gate=platform_gate,
            warmup=warmup,
            consensus_data=consensus_data,
        )

    # ── Plan B integration helpers (T26, T29, T36) ─────────────

    def _evaluate_paper_filters(self, ctx: dict):
        """Run the paper-tier filter pipeline against a decision context.

        Per-model overrides: if ctx contains ``confidence_threshold`` or
        ``ev_threshold``, those values take precedence over the global
        ``self.filters`` defaults (wired by Step 5 of Plan A).
        """
        from filters.pipeline import FilterPipeline
        from filters.staleness import build_stale_price_stage, build_stale_book_stage
        from filters.paper_filter import build_paper_filter_stage

        # Per-model overrides flow through ctx; fall back to global filters
        confidence_threshold = ctx.get(
            "confidence_threshold", self.filters.get("confidence_threshold", 0.55)
        )
        ev_threshold = ctx.get(
            "ev_threshold", self.filters.get("ev_threshold", 0.0)
        )

        pipeline = FilterPipeline([
            build_stale_book_stage(),
            build_stale_price_stage(
                max_age_seconds=self.filters.get("max_book_age_seconds", 30),
            ),
            build_paper_filter_stage(
                confidence_threshold=confidence_threshold,
                ev_threshold=ev_threshold,
            ),
        ])
        return pipeline.run(ctx)

    # ------------------------------------------------------------------
    # Metric-writer pass-through wrappers (implementations live in
    # trading/metric_writers.py — MetricWriters).  These wrappers keep
    # existing call sites inside this file working without change.
    # Phase D.5 can remove them once all callers migrate.
    # ------------------------------------------------------------------

    def refresh_decay_metrics(self, *, window_size: int = 100) -> None:
        """Pass-through → MetricWriters.refresh_decay_metrics."""
        self.metric_writers.refresh_decay_metrics(window_size=window_size)

    def refresh_price_ranges(self) -> None:
        """Pass-through → MetricWriters.refresh_price_ranges.

        Result is assigned back to self._price_ranges so downstream code
        that reads self._price_ranges continues to work unchanged.
        """
        tracked = getattr(self, "_tracked_symbols", PREDICTION_SYMBOLS)
        self._price_ranges = self.metric_writers.refresh_price_ranges(
            tracked_symbols=tracked
        )

    def record_overlap_for_boundary(
        self, *, ts_contract_open_ms: int, symbol: str,
        market_window_seconds: int, scores,
    ) -> None:
        """Pass-through → MetricWriters.record_overlap_for_boundary."""
        self.metric_writers.record_overlap_for_boundary(
            ts_contract_open_ms=ts_contract_open_ms,
            symbol=symbol,
            market_window_seconds=market_window_seconds,
            scores=scores,
        )

    def kalshi_dispatch_eligible(
        self, *, model_name: str, symbol: str, market_window_seconds: int,
    ) -> bool:
        return self.kalshi_dispatcher.is_eligible(
            model_name=model_name,
            symbol=symbol,
            market_window_seconds=market_window_seconds,
        )

    def _direction_for(self, prediction_id: str) -> str:
        return self.resolution_checker.direction_for(prediction_id)

    @staticmethod
    def _compute_outcome(direction, price_open, price_close):
        return ResolutionChecker.compute_outcome(direction, price_open, price_close)


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

    def _is_model_in_warmup(self, warmup_seconds: int) -> bool:
        """Per-model warmup check using a caller-supplied warmup duration.

        Falls back to True (warmup active) when no data has arrived yet.
        Use _is_in_warmup() for the global MAD_WARMUP_SECONDS check.
        """
        if self._first_data_time_ms is None:
            return True
        elapsed_ms = int(time.time() * 1000) - self._first_data_time_ms
        return elapsed_ms < warmup_seconds * 1000

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

        # Phase 5: scale by per-model tier kelly_multiplier (gold=1.0, silver=0.3,
        # watch=0.0, retired=0.0).  Models with multiplier=0.0 get flat paper stake.
        kelly_multiplier = float(
            self._model_meta.get(model_name, {}).get("kelly_multiplier", 0.0) or 0.0
        )
        if kelly_multiplier == 0.0:
            return SIMULATED_STAKE_USDC

        # Apply fraction multiplier (half-Kelly = 0.5) then tier multiplier
        effective_kelly_fraction = f["kelly_fraction"] * kelly_multiplier
        kelly_adj = kelly_raw * effective_kelly_fraction
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
            "[%s] Kelly: edge=%.4f odds=%.2f raw=%.4f adj=%.4f mult=%.2f bankroll=$%.2f stake=$%.2f",
            model_name, edge, odds, kelly_raw, kelly_adj, kelly_multiplier, bankroll, stake,
        )
        return round(stake, 2)

    async def _dispatch_kalshi_live(self, **kwargs) -> None:
        return await self.kalshi_dispatcher.dispatch_live(**kwargs)

    async def _resolve_kalshi_ticker_for_boundary(
        self, boundary_ms: int, market_window_seconds: int = 900,
    ) -> str | None:
        return await self.kalshi_dispatcher.resolve_ticker_for_boundary(
            symbol="",
            duration_sec=market_window_seconds,
            boundary_ms=boundary_ms,
        )

    @staticmethod
    def _midpoint_from_levels(yes_levels: list, no_levels: list) -> float | None:
        return KalshiDispatcher.midpoint_from_levels(yes_levels, no_levels)

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
            # ── Deferred SIGHUP fleet hot-reload ──────────────────────
            if getattr(self, "_sighup_requested", False):
                self._sighup_requested = False
                try:
                    self._reload_fleet()
                except Exception as e:
                    logger.error("fleet_hot_reload_failed: %s", e, exc_info=True)

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

                # Reload regime thresholds every 12 boundaries (~60 min)
                if self._boundary_count % 12 == 0:
                    try:
                        self._reload_regime_thresholds()
                    except Exception as e:
                        logger.exception("regime_threshold_reload_failed", extra={"err": str(e)})

                # C6: Evaluate lifecycle FSM every 16 boundaries + reload model meta
                if self._boundary_count % self._lifecycle_interval == 0:
                    try:
                        self._reload_model_meta()
                    except Exception as e:
                        logger.exception("reload_model_meta_failed", extra={"err": str(e)})
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
        """Score all symbols with all models at a contract boundary.

        Thin wrapper — body extracted to trading/boundary_scorer.py (D.5b).
        """
        return await self.boundary_scorer.score_boundary(now_ms, boundary_ms)


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
                        default=None,
                        help="Path to H=60 model (omit for fleet-mode)")
    parser.add_argument("--h60-v3-model", type=str,
                        default=None,
                        help="Path to H=60 V3 (debiased with mid_price_dev_30d) model.")
    parser.add_argument("--h300-model", type=str,
                        default=None,
                        help="Path to H=300 model (omit for fleet-mode)")
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

    # Check if we should use fleet-mode (registry-driven) or legacy CLI-flag mode
    if not args.h60_model and not args.h300_model:
        # Registry-driven fleet mode
        logger.info("No CLI model flags provided, entering fleet-mode from registry")
        from trading.fleet_loader import load_active_fleet
        db_path = _os.environ.get("STORAGE_DB_PATH", "/data/v3.db")
        conn = open_database(db_path)
        fleet = load_active_fleet(conn)
        if not fleet:
            logger.error("ERROR: no active models in registry. Run scripts/train_fleet.py first.")
            sys.exit(1)
        model_paths = {c["name"]: c["artifact_path"] for c in fleet}
        logger.info(f"Fleet mode: loading {len(model_paths)} models from registry")
    else:
        # Legacy CLI-flag mode (h60 and/or h300 only)
        model_paths = {
            "h60": args.h60_model,
            "h300": args.h300_model,
        }
        if args.h60_v3_model:
            model_paths["h60_v3"] = args.h60_v3_model

    # Verify model files exist
    for name, path in model_paths.items():
        if not path:
            logger.error("Model path is None for %s", name)
            sys.exit(1)
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
    signal.signal(signal.SIGHUP, trader._handle_sighup)

    asyncio.run(trader.run())


if __name__ == "__main__":
    main()
