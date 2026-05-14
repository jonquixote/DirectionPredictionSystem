"""
LiveState — in-memory singleton for fast-access dashboard state.

Updated every 5s by background refresh loop. Serves /api/status and WS pushes.
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, timezone
from services.sqlite_store import get_store
from services.metrics import (
    compute_realized_net, wilson_ci, rolling_accuracy_series, z_test, SYSTEM_FEE,
)

logger = logging.getLogger("dashboard.live_state")

# Suppression rules — hardcoded (matches paper_trader.py config)
SUPPRESSION_RULES = [
    {
        "name": "UTC_BLACKOUT",
        "active": True,
        "models_affected": ["h60", "h60_v3"],
        "description": "Suppress H60 paper trades 21:00–03:59 UTC (overnight weakness)",
        "since_date": "2026-03-31",
        "params": {"start_hour": 21, "end_hour": 4},
    },
    {
        "name": "CONTRACT_MISMATCH",
        "active": True,
        "models_affected": ["h300"],
        "description": "Suppress H300 300s contracts (5-min too short for signal)",
        "since_date": "2026-03-31",
        "params": {"suppressed_duration_seconds": 300},
    },
]


class LiveState:
    """Singleton holding fast-access state for dashboard."""

    _initialized: bool = False
    _last_refresh_ms: int = 0
    _started_at_ms: int = 0

    # State fields
    gate_status: dict = {}
    predictions_per_hour: dict = {}
    trades_per_hour: dict = {}
    open_trades_count: int = 0
    ewm_state: dict = {}
    psi_results: dict = {}

    # Alert tracking
    alert_queue: list[dict] = []
    _last_pushed_prediction_ms: int = 0
    _last_pushed_alert_ms: int = 0

    @classmethod
    def initialize(cls) -> None:
        cls._initialized = True
        cls._started_at_ms = int(time.time() * 1000)
        cls.gate_status = {}
        cls.predictions_per_hour = {}
        cls.trades_per_hour = {}
        cls.alert_queue = []
        cls.ewm_state = {}
        cls.psi_results = {}

        # Initial data load
        store = get_store()
        store.refresh()
        cls._update_metrics(store)
        logger.info("LiveState initialized")

    @classmethod
    def refresh(cls) -> None:
        """Called every 5s to update state from JSONL data."""
        store = get_store()
        store.refresh()
        cls._update_metrics(store)
        cls._last_refresh_ms = int(time.time() * 1000)

    @classmethod
    def _update_metrics(cls, store) -> None:
        """Recompute derived metrics from current data."""
        now_ms = int(time.time() * 1000)
        one_hour_ago = now_ms - 3_600_000

        # Predictions per hour
        cls.predictions_per_hour = {}
        for model in ["h60", "h300", "h60_v3"]:
            recent = [
                p for p in store.predictions
                if p.get("model") == model
                and p.get("ts_model_ran_ms", 0) >= one_hour_ago
                and not p.get("warmup", False)
            ]
            cls.predictions_per_hour[model] = len(recent)

        # Trades per hour (non-suppressed)
        cls.trades_per_hour = {}
        for model in ["h60", "h300", "h60_v3"]:
            recent = [
                t for t in store.trades
                if t.get("model") == model
                and t.get("ts_model_ran_ms", 0) >= one_hour_ago
                and not t.get("suppressed_reason")
            ]
            cls.trades_per_hour[model] = len(recent)

        # Open trades
        cls.open_trades_count = len(store.get_open_trades())

        # Gate status per model
        cls.gate_status = {}
        for model in ["h60", "h300", "h60_v3"]:
            resolved = store.get_resolved_trades(model=model)
            resolved.sort(key=lambda t: t.get("ts_model_ran_ms", 0))
            outcomes = [t.get("prediction_correct", False) for t in resolved]

            rolling = rolling_accuracy_series(outcomes, n=50)
            current = rolling[-1]["accuracy"] if rolling else None
            gate_threshold = 0.515
            passes = current is not None and current >= gate_threshold

            # Trend: compare last 3 points
            trend = "stable"
            if len(rolling) >= 3:
                last3 = [r["accuracy"] for r in rolling[-3:]]
                if last3[-1] > last3[0]:
                    trend = "up"
                elif last3[-1] < last3[0]:
                    trend = "down"

            cls.gate_status[model] = {
                "model": model,
                "rolling_n": 50,
                "rolling_value": current,
                "gate_threshold": gate_threshold,
                "pass": passes,
                "trend": trend,
                "distance_to_threshold": (
                    round(current - gate_threshold, 4) if current is not None else None
                ),
            }

    @classmethod
    def snapshot(cls) -> dict:
        """Return JSON-serializable snapshot for /api/status and WS push."""
        store = get_store()
        now_ms = int(time.time() * 1000)

        # Last prediction per model
        last_pred_per_model = {}
        for model in ["h60", "h300", "h60_v3"]:
            model_preds = [
                p for p in store.predictions
                if p.get("model") == model and not p.get("warmup", False)
            ]
            if model_preds:
                latest = max(model_preds, key=lambda p: p.get("ts_model_ran_ms", 0))
                last_pred_per_model[model] = latest.get("ts_model_ran_ms")
            else:
                last_pred_per_model[model] = None

        # Container status (approximate from prediction timing)
        containers = []
        for model in ["h60", "h300", "h60_v3"]:
            last_ms = last_pred_per_model.get(model)
            age = (now_ms - last_ms) / 1000 if last_ms else None
            containers.append({
                "model": model,
                "healthy": age is not None and age < 600,  # healthy if prediction within 10 min
                "warmup": False,
                "uptime_seconds": (now_ms - cls._started_at_ms) / 1000,
                "started_at_ms": cls._started_at_ms,
                "cpu_pct": 0,
                "ram_used_mb": 0,
                "ram_total_mb": 0,
                "last_prediction_ms": last_ms,
                "last_prediction_age_seconds": round(age, 1) if age else None,
            })

        return {
            "ts": now_ms,
            "containers": containers,
            "data_pipeline": {
                "last_parquet_update_ms": None,
                "hours_since_ingest": None,
                "parquet_file_sizes": {},
                "parquet_row_counts": {},
                "stale": False,
            },
            "pmarket_api": {
                "last_fetch_ms": None,
                "latency_ms": None,
                "consecutive_failures": 0,
                "degraded": False,
            },
            "ewm_state": cls.ewm_state,
            "model_meta": list(store.model_metadata.values()),
            "suppression_rules": SUPPRESSION_RULES,
            "coverage_rate_1h": {
                "predictions_fired": sum(cls.predictions_per_hour.values()),
                "contracts_available": 0,
                "rate": 0,
            },
            "gate_status": cls.gate_status,
            "predictions_per_hour": cls.predictions_per_hour,
            "trades_per_hour": cls.trades_per_hour,
            "open_trades_count": cls.open_trades_count,
            "psi_results": cls.psi_results,
            "system_fee": SYSTEM_FEE,
        }

    @classmethod
    def pop_new_predictions(cls) -> list[dict]:
        """Get predictions newer than last push."""
        store = get_store()
        new = [
            p for p in store.predictions
            if p.get("ts_model_ran_ms", 0) > cls._last_pushed_prediction_ms
            and not p.get("warmup", False)
        ]
        if new:
            cls._last_pushed_prediction_ms = max(
                p.get("ts_model_ran_ms", 0) for p in new
            )
        return new

    @classmethod
    def add_alert(cls, alert: dict) -> None:
        """Push a new alert into the queue and keep size managed."""
        cls.alert_queue.insert(0, alert)
        if len(cls.alert_queue) > 500:
            cls.alert_queue.pop()

    @classmethod
    def pop_new_alerts(cls) -> list[dict]:
        """Get alerts newer than last push."""
        cutoff = cls._last_pushed_alert_ms
        new = [a for a in cls.alert_queue if a.get("timestamp_ms", 0) > cutoff]
        if new:
            cls._last_pushed_alert_ms = max(a.get("timestamp_ms", 0) for a in new)
        return new
