from __future__ import annotations
"""
LiveFeatureComputer — real-time feature computation from streaming L2 data.

Replicates the exact feature logic from data/build_features.py +
data/build_features_v3.py for use with live WebSocket order book updates.

Maintains per-symbol rolling state and produces 1-minute aggregated feature
vectors matching the training schema (features_v3).

Usage:
    computer = LiveFeatureComputer(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    # On each L2 update:
    computer.on_book_update(symbol, sorted_bids, sorted_asks, cts_ms)
    # At each contract boundary (every 5 min):
    features = computer.get_1min_bar("BTCUSDT")
"""

import time
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── Constants (match build_features.py exactly) ──────────────
MLOFI_LEVELS = 10
MAD_WINDOW = 1000
VWAP_WINDOW = 100    # rolling window for VWAP computation
ROLL_WINDOW = 15
DOWNSAMPLE_MS = 1000  # 1-second downsample

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
SYMBOL_MAP = {s: i for i, s in enumerate(SYMBOLS)}

# v3 feature columns in training order (must match run_training.py FEATURE_COLS)
# V1/V2 models use this list (includes raw mid_price)
V3_FEATURE_COLS = [
    "mlofi", "ofi", "mid_price", "spread", "relative_spread",
    "vwap_deviation", "roll",
    "mlofi_1", "mlofi_2", "mlofi_3", "mlofi_4", "mlofi_5",
    "mlofi_6", "mlofi_7", "mlofi_8", "mlofi_9", "mlofi_10",
    "mlofi_30s_mean", "mlofi_60s_mean",
    "ofi_30s_mean", "ofi_60s_mean",
    "mlofi_30s_std", "mlofi_60s_std",
    "ofi_60s_std",
    "spread_5m_pct",
    "vwap_2m_deviation", "vwap_dev_velocity", "vwap_dev_30s_std",
    "mlofi_momentum",
    "btc_vwap_deviation", "btc_mlofi_30s_mean", "eth_mlofi_30s_mean",
    "symbol_cat",
]

# V3 model feature columns (mid_price replaced with mid_price_dev_30d, spread dropped)
V3_MODEL_FEATURE_COLS = [
    "mlofi", "ofi", "mid_price_dev_30d", "relative_spread",
    "vwap_deviation", "roll",
    "mlofi_1", "mlofi_2", "mlofi_3", "mlofi_4", "mlofi_5",
    "mlofi_6", "mlofi_7", "mlofi_8", "mlofi_9", "mlofi_10",
    "mlofi_30s_mean", "mlofi_60s_mean",
    "ofi_30s_mean", "ofi_60s_mean",
    "mlofi_30s_std", "mlofi_60s_std",
    "ofi_60s_std",
    "spread_5m_pct",
    "vwap_2m_deviation", "vwap_dev_velocity", "vwap_dev_30s_std",
    "mlofi_momentum",
    "btc_vwap_deviation", "btc_mlofi_30s_mean", "eth_mlofi_30s_mean",
    "symbol_cat",
]

# EWM constants
EWM_SPAN_30D = 43200      # 30 days × 1440 1-min bars
EWM_ALPHA = 2.0 / (EWM_SPAN_30D + 1)  # ~4.6e-5


@dataclass
class SymbolState:
    """Per-symbol rolling computation state."""
    symbol: str

    # MLOFI MAD normalisation
    mlofi_history: deque = field(default_factory=lambda: deque(maxlen=MAD_WINDOW))

    # OFI previous state
    prev_best_bid_price: float = 0.0
    prev_best_bid_vol: float = 0.0
    prev_best_ask_price: float = 0.0
    prev_best_ask_vol: float = 0.0

    # VWAP
    vwap_num: deque = field(default_factory=lambda: deque(maxlen=VWAP_WINDOW))
    vwap_den: deque = field(default_factory=lambda: deque(maxlen=VWAP_WINDOW))

    # Roll measure
    mid_price_history: deque = field(default_factory=lambda: deque(maxlen=ROLL_WINDOW + 2))

    # 1-second rows buffer for rolling features + price_at lookup.
    # Sized to ~40 min (2400 rows) so resolutions up to h1800 horizons can find
    # close prices without re-reading on-disk parquets.
    rows_1s: deque = field(default_factory=lambda: deque(maxlen=2400))

    # Downsample
    last_output_ts: int = 0

    # 1-minute aggregation buffer
    minute_rows: list = field(default_factory=list)
    current_minute_cts: int = 0

    # Latest 1-min bar (ready for model)
    latest_1min_bar: Optional[dict] = None

    # Warmup tracking
    first_row_ts: int = 0
    total_rows: int = 0

    # EWM running state for mid_price_dev_30d (O(1) per update)
    ewm_mean: float = 0.0
    ewm_var: float = 0.0
    ewm_initialized: bool = False
    ewm_count: int = 0


class LiveFeatureComputer:
    """
    Computes features_v3 from live order book updates.

    Call on_book_update() with every L2 update. The computer:
    1. Downsamples to ~1s resolution
    2. Computes point-in-time features (matching build_features.py)
    3. Computes rolling features (matching add_rolling_features.py)
    4. Computes v3 enrichment features (matching build_features_v3.py)
    5. Aggregates to 1-minute bars
    6. Adds cross-asset features
    """

    def __init__(self, symbols: list[str] | None = None,
                 features_dir: str = "/data/features_v3"):
        self.symbols = [s.upper() for s in (symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"])]
        self.states: dict[str, SymbolState] = {
            sym: SymbolState(symbol=sym) for sym in self.symbols
        }
        self._warmup_seconds = 120  # need 120s of data before predictions
        self._features_dir = Path(features_dir)

    def preload_ewm(self) -> None:
        """Preload 30-day EWM state from historical parquet files.

        Reads mid_price from the last 30 days of features_v3 parquets
        and initializes the running EWM mean/var for each symbol.
        Falls back to 7-day window if insufficient data.
        Never crashes.
        """
        try:
            import polars as pl
        except ImportError:
            logger.warning("polars not available — EWM preload skipped")
            return

        for sym in self.symbols:
            state = self.states[sym]
            sym_dir = self._features_dir / sym
            if not sym_dir.exists():
                logger.warning("No features dir for %s at %s — EWM not initialized", sym, sym_dir)
                continue

            files = sorted(sym_dir.glob("*_features.parquet"))
            if not files:
                logger.warning("No parquet files for %s — EWM not initialized", sym)
                continue

            # Use last 30 files (~30 days)
            recent = files[-30:]
            if len(recent) < 7:
                logger.warning("%s: only %d files (< 7 days) — EWM may be unstable", sym, len(recent))

            try:
                df = pl.concat([pl.read_parquet(f) for f in recent]).sort("cts")
                mid_prices = df["mid_price"].to_numpy()

                if len(mid_prices) == 0:
                    logger.warning("%s: no mid_price data in parquets", sym)
                    continue

                # Initialize running EWM from historical data
                alpha = EWM_ALPHA
                ewm_mean = mid_prices[0]
                ewm_var = 0.0
                for i in range(1, len(mid_prices)):
                    x = mid_prices[i]
                    diff = x - ewm_mean
                    ewm_mean = alpha * x + (1 - alpha) * ewm_mean
                    ewm_var = (1 - alpha) * (ewm_var + alpha * diff * diff)

                state.ewm_mean = ewm_mean
                state.ewm_var = ewm_var
                state.ewm_initialized = True
                state.ewm_count = len(mid_prices)

                ewm_std = np.sqrt(ewm_var) if ewm_var > 0 else 0.0
                logger.info(
                    "%s EWM preloaded: %d rows from %d files, mean=%.2f std=%.2f",
                    sym, len(mid_prices), len(recent), ewm_mean, ewm_std,
                )
            except Exception as e:
                logger.warning("%s: EWM preload failed: %s", sym, e)

    def is_warmed_up(self, symbol: str) -> bool:
        """Check if we have enough data for predictions."""
        state = self.states.get(symbol.upper())
        if not state or state.total_rows < 60:
            return False
        if state.first_row_ts == 0:
            return False
        elapsed_ms = state.last_output_ts - state.first_row_ts
        return elapsed_ms >= self._warmup_seconds * 1000

    def price_at(self, symbol: str, ts_ms: int, tol_ms: int = 2000) -> float:
        """Return mid_price closest to ts_ms within ±tol_ms.

        Scans the in-memory `rows_1s` buffer (~40 min window).
        Raises KeyError when no row in tolerance — caller should re-queue.
        """
        state = self.states.get(symbol.upper())
        if not state or not state.rows_1s:
            raise KeyError(f"price_at: no rows for {symbol}")
        best = None
        best_delta = tol_ms + 1
        # Iterate newest→oldest; bail once cts is far enough below ts_ms.
        for row in reversed(state.rows_1s):
            cts = row.get("cts")
            if cts is None:
                continue
            delta = abs(cts - ts_ms)
            if delta < best_delta:
                best_delta = delta
                best = row
            if cts < ts_ms - tol_ms:
                break
        if best is None or best_delta > tol_ms:
            raise KeyError(f"price_at: no row within ±{tol_ms}ms of {ts_ms} for {symbol}")
        return float(best["mid_price"])

    def on_book_update(
        self,
        symbol: str,
        sorted_bids: list[tuple[float, float]],
        sorted_asks: list[tuple[float, float]],
        cts_ms: int,
    ) -> Optional[dict]:
        """
        Process an L2 update. Returns a 1s feature row if downsample interval
        has elapsed, otherwise None.

        Args:
            symbol: e.g. "BTCUSDT"
            sorted_bids: [(price, size), ...] descending by price
            sorted_asks: [(price, size), ...] ascending by price
            cts_ms: exchange timestamp in milliseconds
        """
        symbol = symbol.upper()
        state = self.states.get(symbol)
        if not state:
            return None

        if not sorted_bids or not sorted_asks:
            return None

        # Downsample: only compute one row per DOWNSAMPLE_MS
        if cts_ms and (cts_ms - state.last_output_ts) < DOWNSAMPLE_MS:
            return None
        state.last_output_ts = cts_ms

        if state.first_row_ts == 0:
            state.first_row_ts = cts_ms

        # ── Compute point-in-time features ──
        row = self._compute_features(state, sorted_bids, sorted_asks, cts_ms)
        state.total_rows += 1

        # Note: mid_price_dev_30d is NOT computed per 1s tick.
        # It is computed once per minute in _finalize_minute_bar()
        # to match training pipeline (EWM on 1-min bars, not 1s ticks).
        row["mid_price_dev_30d"] = 0.0  # placeholder for 1s row

        # Add to 1s buffer for rolling computation
        state.rows_1s.append(row)

        # Compute rolling features on the 1s buffer
        row_with_rolling = self._compute_rolling_features(state, row)

        # Compute v3 enrichment
        row_enriched = self._compute_v3_enrichment(state, row_with_rolling)

        # Aggregate to 1-minute bars
        self._aggregate_to_1min(state, row_enriched, cts_ms)

        return row_enriched

    def get_1min_bar(self, symbol: str) -> Optional[dict]:
        """
        Get the latest completed 1-minute bar with all features.
        Returns None if no bar is ready or warmup incomplete.
        """
        symbol = symbol.upper()
        state = self.states.get(symbol)
        if not state or not state.latest_1min_bar:
            return None

        bar = dict(state.latest_1min_bar)

        # Add cross-asset features
        bar = self._add_cross_asset_features(symbol, bar)

        # Add symbol_cat
        bar["symbol_cat"] = SYMBOL_MAP.get(symbol, 0)

        return bar

    def get_feature_vector(self, symbol: str, feature_cols: list[str] | None = None) -> Optional[np.ndarray]:
        """Get feature vector as numpy array in training column order."""
        cols = feature_cols or V3_FEATURE_COLS
        bar = self.get_1min_bar(symbol)
        if bar is None:
            return None
        try:
            return np.array([bar[col] for col in cols], dtype=np.float64)
        except KeyError as e:
            logger.error("Missing feature column %s for %s", e, symbol)
            return None

    def get_feature_dict(self, symbol: str, feature_cols: list[str] | None = None) -> Optional[dict]:
        """Get feature dict with only the columns needed for prediction."""
        cols = feature_cols or V3_FEATURE_COLS
        bar = self.get_1min_bar(symbol)
        if bar is None:
            return None
        return {col: bar.get(col, 0.0) for col in cols}

    # ── Internal: point-in-time features (matches build_features.py) ──

    def _compute_features(
        self,
        state: SymbolState,
        sorted_bids: list[tuple[float, float]],
        sorted_asks: list[tuple[float, float]],
        cts_ms: int,
    ) -> dict:
        """Compute all point-in-time features from a single L2 snapshot."""
        best_bid_price, best_bid_vol = sorted_bids[0]
        best_ask_price, best_ask_vol = sorted_asks[0]

        # Mid-price and spread
        mid_price = (best_bid_price + best_ask_price) / 2.0
        spread = best_ask_price - best_bid_price
        relative_spread = spread / mid_price if mid_price > 0 else 0.0

        # Track mid-price for Roll measure
        state.mid_price_history.append(mid_price)

        # ── MLOFI (levels 1-10, 1/k weighted) ──
        mlofi_raw = 0.0
        mlofi_per_level = {}
        for k in range(1, MLOFI_LEVELS + 1):
            weight = 1.0 / k
            v_bid = sorted_bids[k - 1][1] if k <= len(sorted_bids) else 0.0
            v_ask = sorted_asks[k - 1][1] if k <= len(sorted_asks) else 0.0
            denom = v_bid + v_ask
            imb = (v_bid - v_ask) / denom if denom > 0 else 0.0
            mlofi_raw += weight * imb
            mlofi_per_level[f"mlofi_{k}"] = weight * imb

        # MAD normalise
        if len(state.mlofi_history) >= 10:
            arr = np.array(state.mlofi_history)
            median = np.median(arr)
            mad = np.median(np.abs(arr - median))
            mlofi_norm = (mlofi_raw - median) / mad if mad > 0 else 0.0
        else:
            mlofi_norm = 0.0
        state.mlofi_history.append(mlofi_raw)

        # ── OFI (Cont et al. 2014) ──
        ofi = 0.0
        if state.prev_best_bid_price > 0:
            if best_bid_price > state.prev_best_bid_price:
                delta_bid = best_bid_vol
            elif best_bid_price == state.prev_best_bid_price:
                delta_bid = best_bid_vol - state.prev_best_bid_vol
            else:
                delta_bid = -state.prev_best_bid_vol

            if best_ask_price < state.prev_best_ask_price:
                delta_ask = best_ask_vol
            elif best_ask_price == state.prev_best_ask_price:
                delta_ask = best_ask_vol - state.prev_best_ask_vol
            else:
                delta_ask = -state.prev_best_ask_vol

            ofi = delta_bid - delta_ask

        state.prev_best_bid_vol = best_bid_vol
        state.prev_best_ask_vol = best_ask_vol
        state.prev_best_bid_price = best_bid_price
        state.prev_best_ask_price = best_ask_price

        # ── VWAP deviation ──
        total_bid_vol = sum(v for _, v in sorted_bids[:MLOFI_LEVELS])
        total_ask_vol = sum(v for _, v in sorted_asks[:MLOFI_LEVELS])
        total_vol = total_bid_vol + total_ask_vol
        state.vwap_num.append(mid_price * total_vol)
        state.vwap_den.append(total_vol)

        vwap_dev = 0.0
        if len(state.vwap_den) >= 10:
            total_vwap_vol = sum(state.vwap_den)
            if total_vwap_vol > 0:
                vwap = sum(state.vwap_num) / total_vwap_vol
                vwap_dev = (mid_price - vwap) / vwap if vwap > 0 else 0.0

        # ── Roll measure ──
        roll = 0.0
        if len(state.mid_price_history) >= ROLL_WINDOW + 1:
            prices = np.array(state.mid_price_history)
            returns = np.diff(prices[-ROLL_WINDOW - 1:])
            if len(returns) >= 2:
                cov = np.cov(returns[:-1], returns[1:])[0, 1]
                roll = float(2 * np.sqrt(abs(cov)))

        # Build feature row
        feature_row = {
            "cts": cts_ms,
            "mid_price": mid_price,
            "spread": spread,
            "relative_spread": relative_spread,
            "mlofi": mlofi_norm,
            "ofi": ofi,
            "vwap_deviation": vwap_dev,
            "roll": roll,
        }
        feature_row.update(mlofi_per_level)

        return feature_row

    # ── Internal: rolling features (matches add_rolling_features.py) ──

    def _compute_rolling_features(self, state: SymbolState, row: dict) -> dict:
        """Compute rolling means/stds from the 1s buffer."""
        rows = state.rows_1s
        n = len(rows)
        row = dict(row)  # copy

        cts = row["cts"]

        # Helper: get values from buffer within a time window
        def _get_window_values(column: str, window_ms: int) -> list[float]:
            vals = []
            cutoff = cts - window_ms
            for r in rows:
                if r["cts"] >= cutoff:
                    vals.append(r[column])
            return vals

        # Rolling means
        mlofi_30 = _get_window_values("mlofi", 30_000)
        mlofi_60 = _get_window_values("mlofi", 60_000)
        ofi_30 = _get_window_values("ofi", 30_000)
        ofi_60 = _get_window_values("ofi", 60_000)

        row["mlofi_30s_mean"] = float(np.mean(mlofi_30)) if mlofi_30 else 0.0
        row["mlofi_60s_mean"] = float(np.mean(mlofi_60)) if mlofi_60 else 0.0
        row["ofi_30s_mean"] = float(np.mean(ofi_30)) if ofi_30 else 0.0
        row["ofi_60s_mean"] = float(np.mean(ofi_60)) if ofi_60 else 0.0

        # Rolling stds
        row["mlofi_30s_std"] = float(np.std(mlofi_30)) if len(mlofi_30) > 1 else 0.0
        row["mlofi_60s_std"] = float(np.std(mlofi_60)) if len(mlofi_60) > 1 else 0.0
        row["ofi_60s_std"] = float(np.std(ofi_60)) if len(ofi_60) > 1 else 0.0

        # Spread 5-min percentile rank
        spread_5m = _get_window_values("spread", 300_000)
        if len(spread_5m) > 1:
            current_spread = row["spread"]
            rank = sum(1 for s in spread_5m if s <= current_spread) / len(spread_5m)
            row["spread_5m_pct"] = rank
        else:
            row["spread_5m_pct"] = 0.5

        return row

    # ── Internal: v3 enrichment (matches build_features_v3.py) ──

    def _compute_v3_enrichment(self, state: SymbolState, row: dict) -> dict:
        """Compute v3-specific enrichment features."""
        row = dict(row)  # copy
        rows = state.rows_1s
        cts = row["cts"]

        # vwap_2m_deviation: (mid_price - 2min_rolling_mean(mid_price)) / mean
        mid_2m = [r["mid_price"] for r in rows if r["cts"] >= cts - 120_000]
        if len(mid_2m) >= 10:
            mean_2m = float(np.mean(mid_2m))
            row["vwap_2m_deviation"] = (row["mid_price"] - mean_2m) / mean_2m if mean_2m > 0 else 0.0
        else:
            row["vwap_2m_deviation"] = 0.0

        # vwap_dev_velocity: first difference of vwap_deviation
        if len(rows) >= 2:
            prev_vwap_dev = rows[-2].get("vwap_deviation", 0.0)
            row["vwap_dev_velocity"] = row["vwap_deviation"] - prev_vwap_dev
        else:
            row["vwap_dev_velocity"] = 0.0

        # vwap_dev_30s_std
        vwap_dev_30 = [r.get("vwap_deviation", 0.0) for r in rows if r["cts"] >= cts - 30_000]
        row["vwap_dev_30s_std"] = float(np.std(vwap_dev_30)) if len(vwap_dev_30) > 1 else 0.0

        # mlofi_momentum: mlofi_30s_mean - mlofi_60s_mean
        row["mlofi_momentum"] = row.get("mlofi_30s_mean", 0.0) - row.get("mlofi_60s_mean", 0.0)

        return row

    # ── Internal: 1-minute aggregation ──

    def _aggregate_to_1min(self, state: SymbolState, row: dict, cts_ms: int) -> None:
        """Aggregate 1s rows into 1-minute bars."""
        minute_cts = (cts_ms // 60_000) * 60_000

        if state.current_minute_cts == 0:
            state.current_minute_cts = minute_cts

        if minute_cts > state.current_minute_cts and state.minute_rows:
            # New minute — finalize the previous bar
            bar = self._finalize_minute_bar(state, state.minute_rows)
            state.latest_1min_bar = bar
            state.minute_rows = [row]
            state.current_minute_cts = minute_cts
        else:
            state.minute_rows.append(row)

    def _finalize_minute_bar(self, state: SymbolState, rows: list[dict]) -> dict:
        """Average numeric features over the minute, take last for price/regime.

        Also updates the running EWM state and computes mid_price_dev_30d.
        EWM is updated once per minute (matching training pipeline).
        """
        if not rows:
            return {}

        # Mean columns (flow/imbalance features)
        mean_cols = [
            "spread", "relative_spread", "mlofi", "ofi",
            "vwap_deviation", "roll",
            "mlofi_1", "mlofi_2", "mlofi_3", "mlofi_4", "mlofi_5",
            "mlofi_6", "mlofi_7", "mlofi_8", "mlofi_9", "mlofi_10",
            "mlofi_30s_mean", "mlofi_60s_mean",
            "ofi_30s_mean", "ofi_60s_mean",
            "mlofi_30s_std", "mlofi_60s_std", "ofi_60s_std",
            "vwap_2m_deviation", "vwap_dev_velocity", "vwap_dev_30s_std",
            "mlofi_momentum",
        ]

        bar = {}
        bar["cts"] = rows[-1]["cts"]  # last cts

        for col in mean_cols:
            vals = [r.get(col, 0.0) for r in rows]
            bar[col] = float(np.mean(vals))

        # Last value columns
        mid_price = rows[-1].get("mid_price", 0.0)
        bar["mid_price"] = mid_price
        bar["spread_5m_pct"] = rows[-1].get("spread_5m_pct", 0.5)

        # ── Update EWM and compute mid_price_dev_30d (once per minute) ──
        if state.ewm_initialized:
            # Compute deviation BEFORE updating EWM (matches training: deviation
            # is current price vs trailing EWM up to previous bar)
            ewm_std = np.sqrt(state.ewm_var) if state.ewm_var > 0 else 0.0
            bar["mid_price_dev_30d"] = (
                (mid_price - state.ewm_mean) / ewm_std if ewm_std > 0 else 0.0
            )
            # Now update EWM state with this minute's mid_price
            diff = mid_price - state.ewm_mean
            state.ewm_mean = EWM_ALPHA * mid_price + (1 - EWM_ALPHA) * state.ewm_mean
            state.ewm_var = (1 - EWM_ALPHA) * (state.ewm_var + EWM_ALPHA * diff * diff)
            state.ewm_count += 1
        else:
            # Not preloaded — initialize from first bar
            state.ewm_mean = mid_price
            state.ewm_var = 0.0
            state.ewm_initialized = True
            state.ewm_count = 1
            bar["mid_price_dev_30d"] = 0.0

        return bar

    # ── Internal: cross-asset features ──

    def _add_cross_asset_features(self, symbol: str, bar: dict) -> dict:
        """Add cross-asset features from other symbols' latest bars."""
        bar = dict(bar)

        if symbol != "BTCUSDT":
            # Get BTC features for ETH/SOL/XRP
            btc_state = self.states.get("BTCUSDT")
            if btc_state and btc_state.latest_1min_bar:
                bar["btc_vwap_deviation"] = btc_state.latest_1min_bar.get("vwap_deviation", 0.0)
                bar["btc_mlofi_30s_mean"] = btc_state.latest_1min_bar.get("mlofi_30s_mean", 0.0)
            else:
                bar["btc_vwap_deviation"] = 0.0
                bar["btc_mlofi_30s_mean"] = 0.0
            bar["eth_mlofi_30s_mean"] = 0.0
        else:
            # Get ETH features for BTC
            eth_state = self.states.get("ETHUSDT")
            if eth_state and eth_state.latest_1min_bar:
                bar["eth_mlofi_30s_mean"] = eth_state.latest_1min_bar.get("mlofi_30s_mean", 0.0)
            else:
                bar["eth_mlofi_30s_mean"] = 0.0
            bar["btc_vwap_deviation"] = 0.0
            bar["btc_mlofi_30s_mean"] = 0.0

        return bar
