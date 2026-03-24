# BUILD SPECIFICATION
# Polymarket Crypto Direction Prediction System
# Version 2.6 — March 2026
# Status: Research complete. Build from this file.
# Changelog v2.5 → v2.6:
#   CRITICAL: TRAINING_BOUNDARIES corrected — Oct 2022 boundary was wrong
#             Zero-fee BTC/USDT ran until 2023-03-22, not Oct 2022
#             BTC/TUSD zero-fee ended 2023-09-07 (full restoration)
#             Source: Binance official announcements
#   SIGNIFICANT: Polymarket Jan 2026 fee nonlinearity documented in get_fee_rate
#                Taker fees peak around 1.5–1.6% at p ≈ 0.50 and decline toward 0% at extremes
#                Highest NE_t suppression expected at contract open (p≈0.50)
#   SIGNIFICANT: fee_t stake-fraction requirement documented in compute_breakeven_win_rate
#                (nonlinear fee + wrong units → miscalibrated break-even at p≈0.50)
#   MINOR: MLOFICalculator documents OBI vs OFI snapshot/change-based distinction
#          Bieganowski & Ślepaczuk 2026 MLOFI citation flagged as unverified
#   MINOR: TrackB attention documented as additive; multi-head noted as upgrade path
#   MINOR: compute_net_edge historical comment corrected
#          Previous comment said old formula 'omitted the (1-p_model) loss term'.
#          Wrong — the old formula had a loss term, it used the wrong probability.
#          Old: p_model*b - (1-p_market)  [market prob as loss rate]
#          New: p_model*b - (1-p_model)   [model prob as loss rate — correct]
#          Additive error was (p_model - p_market); NE_t understated when
#          p_model > p_market. Gate sign preserved. Comment now accurate.
#
# ── Specification final ─────────────────────────────────────────────────────
# All 16 sections present and verified. No known issues remaining.
# TRAINING_BOUNDARIES sourced. NE_t formula and comment both correct.
# All unverified citations flagged inline. FeeRegimeChecker internally consistent.

# ============================================================
# SECTION 0: CRITICAL PATH
# ============================================================
# Do not begin model training until the propagation lag measurement
# returns a result. The entire framework is conditional on that test.
#
# Order of operations:
#   1. Identify Binance fee change dates (Section 3.1)
#   2. Instrument and run propagation lag measurement (Section 3.2)
#   3. Evaluate lag outcome against pre-committed decision rules (Section 3.3)
#   4. If lag > 30s: build execution pipeline (Section 5)
#   5. Instrument paper trading with full logging schema (Section 10)
#   6. Calibrate adverse selection composite (Section 5.2)
#   7. Train and validate model (Section 4)
#   8. Run 200-market paper trading gate before live capital

# ============================================================
# SECTION 1: DEPENDENCIES
# ============================================================

# Python 3.11+
# Core
numpy>=1.26
pandas>=2.1
scipy>=1.11

# Data ingestion
websockets>=12.0          # Binance WebSocket streams
aiohttp>=3.9              # Async HTTP for Polymarket REST
py-clob-client>=0.18      # Polymarket CLOB SDK  →  pip install py-clob-client

# Feature engineering
statsmodels>=0.14         # OFI serial correlation, covariance estimation

# ML
scikit-learn>=1.4         # Logistic regression, isotonic calibration, MI selection
torch>=2.2                # MLP (Track A) and CNN-BiLSTM-Attention (Track B)
xgboost>=2.0              # Ablation baseline
shap>=0.44                # Feature importance attribution

# Monitoring
scipy.stats               # Bayesian Beta posterior (stdlib)

# Storage
sqlite3 (stdlib)          # Candidate trade log
pyarrow>=14               # Parquet for raw tick data

# Utilities
python-dotenv             # Secrets management
structlog                 # Structured logging
pytest>=7.4               # Test suite

# ============================================================
# SECTION 2: CONFIGURATION
# ============================================================

CONFIG = {
    # Scope
    "assets": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
    "market_type": "5min_15min_binary_direction",

    # Binance data
    "binance_depth_levels": 10,           # MLOFI uses top 10 levels
    "binance_stream_speed_ms": 100,       # Use @depth@100ms — not 1000ms
    "binance_snapshot_interval_s": 30,    # Resync local book every 30s

    # MLOFI
    "mlofi_weights": "inverse_depth",     # w_k = 1/k
    "mlofi_normalisation": "MAD",         # Never z-score
    "mlofi_mad_window": 1000,             # Rolling window for median/MAD

    # Lag measurement trigger (proxy threshold — used BEFORE model is trained)
    # Normalised MLOFI (MAD-scaled) above this absolute value triggers a lag
    # measurement event. Fixed before first log entry. Do not adjust mid-measurement.
    # Natural choice: 1.0 SD of recent distribution (~84th percentile).
    "lag_measurement_trigger_threshold": 1.0,

    # Execution gates
    "spread_gate_percentile": 95,         # Suppress if Binance spread > 95th pct (rolling)
    "spread_gate_window": 1000,           # Bars for rolling spread percentile
    "suppress_seconds_to_resolution": 90,
    "suppress_near_expiry_seconds": 180,
    "suppress_near_expiry_delta": 0.10,   # |p_market - 0.5| < 0.10

    # Adverse selection composite
    "adverse_depth_window_s": 10,
    "adverse_spread_window_s": 10,
    "adverse_fpr_target": 0.20,           # Max 20% false positive rate
    "adverse_min_events_to_fit": 30,      # Min adversely-selected events before fitting

    # Sanderink gate (Bayesian)
    "sanderink_prior_wins": 52,           # Beta(52, 48) — literature baseline
    "sanderink_prior_losses": 48,
    "sanderink_suspend_threshold": 0.80,  # P(p_true <= p*_t) > 0.80 → suspend

    # Position sizing
    "kelly_fraction": 0.25,               # Start at 0.25x; max 0.50x until 50 resolved
    "kelly_revision_min_contracts": 50,

    # PSI monitoring
    "psi_window_days": 7,
    "psi_distribution_alert": 0.25,
    "psi_predictive_alert": 0.10,

    # Validation
    "train_split": 0.70,
    "val_split": 0.15,
    "test_split": 0.15,
    "temporal_gap_bars": 60,              # Purge gap = prediction horizon in bars
    "n_seeds": 5,
    "paper_trading_min_markets": 200,

    # Polymarket API
    "polymarket_clob_base": "https://clob.polymarket.com",
    "polymarket_fee_endpoint": "/fee-rate/{token_id}",
    "polymarket_book_endpoint": "/book?token_id={token_id}",
    "polymarket_rate_limit_books": 50,    # 50 req / 10s

    # Binance WebSocket
    "binance_ws_base": "wss://stream.binance.com:9443/ws",
    "binance_diff_depth_stream": "{symbol}@depth@100ms",
    "binance_partial_depth_stream": "{symbol}@depth10@100ms",
}

# Secrets — stored in .env, never committed
# POLYMARKET_API_KEY=
# POLYMARKET_API_SECRET=
# POLYMARKET_PASSPHRASE=
# POLYMARKET_PROXY_ADDRESS=   # USDC wallet for settlement

# ============================================================
# SECTION 3: INFRASTRUCTURE TASKS (BEFORE MODEL TRAINING)
# ============================================================

# ------------------------------------------------------------
# 3.1 BINANCE FEE CHANGE DATE IDENTIFICATION
# Run before any data collection. Split training data at each boundary.
# ------------------------------------------------------------
# Confirmed structural break: July 8 2022 — Binance zero-fee experiment
# (BTC/USDT and 11 other pairs zero-fee; reversed March 22 2023 00:00 UTC)
# (BTC/TUSD zero-fee retained until September 7 2023 00:00 UTC)
# Action: Query Binance announcement history for BTC, ETH, SOL, XRP
# Source: https://www.binance.com/en/support/announcement/fee-schedule
#
# For each fee change event:
#   - Record exact UTC date and time
#   - Mark as a structural break in TRAINING_BOUNDARIES
#   - Never train a model across a boundary
#
TRAINING_BOUNDARIES = [
    "2022-07-08",   # Confirmed: zero-fee START — 13 BTC spot pairs (14:00 UTC)
                    # Source: Binance announcement ID 10435147c55d4a40b64fcbf43cb46329
    "2023-03-22",   # Confirmed: zero-fee ENDS for BTC/USDT and 11 other pairs (00:00 UTC)
                    # BTC/TUSD retained zero fees after this date
                    # Source: Binance announcement last updated 2023-03-23
    "2023-09-07",   # Confirmed: BTC/TUSD zero-fee ENDS — full fee restoration
                    # NOTE: October 2022 (previous spec) was wrong — that date reflects
                    # Binance market-share impact from FTX collapse, not a fee regime change
]

class FeeRegimeChecker:
    """
    Connects TRAINING_BOUNDARIES to Stage 1 structural gate.
    Returns True if the current timestamp falls within a regime
    that the model was trained on.
    check_structural() requires fee_regime_active — this produces it.
    """
    def __init__(self, boundaries: list, trained_start: str, trained_end: str):
        import datetime
        self.boundaries = [datetime.date.fromisoformat(b) for b in sorted(boundaries)]
        self.trained_start = datetime.date.fromisoformat(trained_start)
        self.trained_end = datetime.date.fromisoformat(trained_end)

    def is_active(self, current_ts_utc) -> bool:
        """
        Returns True if current_ts_utc falls within the same fee regime
        as the training data. False if any boundary has been crossed since
        the regime the model was trained in began.

        Correct approach: find the last boundary at or before trained_start
        to identify which regime the training data belongs to. Using
        trained_end (previous version) conflates "what regime did training
        end in" with "what regime did training happen in" — fails when
        training data sits inside a middle window.

        IMPORTANT: with the current three-boundary structure, both approaches
        agree as long as training never crosses a boundary (which the spec
        forbids). The distinction only matters when TRAINING_BOUNDARIES is
        updated after a model is already deployed — i.e. a new fee event is
        discovered retroactively and a boundary is inserted.

        Example of the failure mode (retroactive boundary discovery):
          Model trained 2023-04-01 to 2023-08-01 (entirely in post-March regime).
          Later, fee event discovered on 2023-06-15 — boundary added retroactively.

          trained_end approach: last boundary <= 2023-08-01 is now 2023-06-15
            → anchors to 2023-06-15 as trained_regime_start (wrong —
              model was trained before this boundary was known to exist)

          trained_start approach: last boundary <= 2023-04-01 is 2023-03-22
            → anchors to 2023-03-22 (correct — unchanged by new boundary)

        This also explains why self.trained_end is stored but never used in
        the logic: it is accepted for interface completeness, but the correct
        anchor is always trained_start.

        current_ts_utc: datetime.datetime (UTC)
        """
        import datetime
        current_date = current_ts_utc.date()
        # Find the last boundary at or before trained_start — this is the
        # opening of the regime the model was actually trained in.
        trained_regime_start = datetime.date.min
        for b in self.boundaries:
            if b <= self.trained_start:
                trained_regime_start = b
        # Active if no boundary falls between trained_regime_start and
        # current_date that wasn't already present before trained_start.
        for b in self.boundaries:
            if trained_regime_start < b <= current_date:
                return False   # Regime has changed since training
        return True

# ------------------------------------------------------------
# 3.2 PROPAGATION LAG MEASUREMENT PIPELINE
# This is the critical path test. Build and run before anything else.
# ------------------------------------------------------------

class LagMeasurementCollector:
    """
    Runs during propagation lag measurement phase.
    No trained model exists yet. Uses lag_measurement_trigger_threshold
    as a proxy — normalised MLOFI exceeding this absolute value triggers
    a lag measurement event. Value fixed in CONFIG before first log entry.
    """
    def __init__(self, assets, kelly_fraction, bankroll_usdc,
                 trigger_threshold=None):
        self.assets = assets
        self.kelly_fraction = kelly_fraction
        self.bankroll_usdc = bankroll_usdc
        self.trigger_threshold = trigger_threshold or CONFIG["lag_measurement_trigger_threshold"]
        self.execution_viable_size = {}   # per asset, computed before run starts

    def compute_viable_size(self, asset, p_market=0.5):
        """
        Must be computed from CONFIG["kelly_fraction"] before measurement begins.
        Fixed for the entire measurement run — do not recompute per event.
        Call once per asset at startup.

        p_market defaults to 0.5 — the natural anchor for binary direction
        contracts, which open near 0.50 at each hourly reset. Do not substitute
        the live price at measurement time: if it departs from 0.50, viable
        size shifts proportionally and the pre-committed threshold is violated.
        The pre-commitment protocol requires this value be fixed before the
        first log entry; 0.5 is the explicit pre-committed choice.
        """
        position_usdc = self.kelly_fraction * self.bankroll_usdc
        self.execution_viable_size[asset] = position_usdc / p_market

    def should_trigger(self, mlofi_normalised: float) -> bool:
        """Proxy threshold — no model required."""
        return abs(mlofi_normalised) > self.trigger_threshold

    async def on_ofi_threshold_cross(self, asset, t_ofi_ms, mlofi_normalised):
        """Called when normalised MLOFI exceeds trigger_threshold."""
        event = {
            "asset": asset,
            "t_ofi_signal_ms": t_ofi_ms,
            "mlofi_normalised": mlofi_normalised,
            "t_ask_depth_drop_ms": None,
            "t_ask_price_move_ms": None,
            "ask_depth_at_signal": None,
            "ask_depth_viable_threshold": self.execution_viable_size.get(asset),
        }
        await self._monitor_polymarket_response(event)

    async def _monitor_polymarket_response(self, event, monitor_window_s=120):
        """Poll Polymarket at 1s resolution for up to 2 minutes after OFI signal."""
        # GET https://clob.polymarket.com/book?token_id={token_id}
        # Parse: asks[] → best ask price and size
        # Log t_ask_depth_drop_ms when asks[0].size < viable_threshold
        # Log t_ask_price_move_ms when asks[0].price > baseline * 1.005
        # operational_lag = t_ask_depth_drop_ms - t_ofi_signal_ms  (PRIMARY metric)
        pass

    def compute_operational_lag(self, event):
        """Primary metric: t_ask_depth_drop - t_ofi_signal"""
        if event["t_ask_depth_drop_ms"] and event["t_ofi_signal_ms"]:
            return event["t_ask_depth_drop_ms"] - event["t_ofi_signal_ms"]
        return None

# ------------------------------------------------------------
# 3.3 LAG MEASUREMENT SAMPLE SIZE AND STOPPING RULE
# ------------------------------------------------------------
# Stage 1 (Pilot): 40 events (10 per asset) over 1 week
#   Compute sigma_pilot = std(operational_lags)
#   n_required = (1.96 * sigma_pilot / 5) ** 2
#
# Stage 2 (Main): Collect n_required events, stratified equally across:
#   - Asian session:  00:00–08:00 UTC
#   - EU session:     08:00–16:00 UTC
#   - US session:     16:00–24:00 UTC
#
# Stopping rule: n >= n_required PER STRATUM
#   AND 95% CI on mean lag excludes both 10s and 30s thresholds
#   Stopping rule does not change based on pilot results.
#
# Estimated duration: 6 weeks at 30% OFI signal rate
#   (30% rate is an ASSUMPTION — actual duration may be longer)

# ------------------------------------------------------------
# 3.4 PRE-COMMITTED DECISION RULES
# These do not change based on measurement outcomes.
# ------------------------------------------------------------
LAG_DECISION_RULES = {
    "below_10s": {
        "action": "TERMINATE",
        "rationale": "Window structurally insufficient. Redirect to 400-600 min Polymarket markets.",
        "salvage_attempts": "NONE — do not attempt tighter gates or higher confidence thresholds",
    },
    "10_to_30s": {
        "action": "STRATIFIED_SECONDARY_ANALYSIS",
        "proceed_condition": "median lag > 30s in >30% of observations in at least ONE session stratum",
        "if_qualifies": "Build session + volatility regime condition into Stage 1 structural gates",
        "if_fails": "Treat as below_10s — terminate",
    },
    "above_30s": {
        "action": "PROCEED",
        "validation_required": "Confirm lag stability across sessions and volatility quintiles",
        "warning": "A 45s median that collapses to 8s in high-vol is NOT a stable 45s window",
    },
}

# ============================================================
# SECTION 4: FEATURE ENGINEERING
# ============================================================

import numpy as np
from collections import deque

class MLOFICalculator:
    """
    Multi-Level Order Flow Imbalance with w_k = 1/k weighting.
    Requires MAD normalisation — never z-score.
    Source: Maitrier et al. arXiv June 2025 (confirmed); Bieganowski & Ślepaczuk 2026
    (unverified as standalone paper — the confirmed Ślepaczuk group paper is
    arXiv:2412.18405 on GMADL; the MLOFI citation needs independent verification).

    IMPLEMENTATION NOTE — OBI vs OFI:
    compute_mlofi() computes a weighted sum of volume imbalances
    (v_bid - v_ask) / (v_bid + v_ask) at each level — this is technically
    Order Book Imbalance (OBI), a snapshot of current queue state.
    Traditional OFI (Cont et al. 2014) tracks *changes* in queue volumes
    between consecutive ticks. Both are legitimate predictors; this
    implementation uses the snapshot definition. If Maitrier et al. 2025
    uses the change-based definition, align accordingly before ablation
    comparisons — the two produce different values under the same label.
    """
    def __init__(self, levels=10, mad_window=1000):
        self.levels = levels
        self.history = deque(maxlen=mad_window)

    def compute_mlofi(self, bids, asks):
        """
        bids: list of (price, volume) sorted descending, len >= levels
        asks: list of (price, volume) sorted ascending, len >= levels
        Returns: raw MLOFI value (not yet normalised)
        """
        mlofi = 0.0
        for k in range(1, self.levels + 1):
            weight = 1.0 / k
            v_bid = bids[k-1][1] if k <= len(bids) else 0.0
            v_ask = asks[k-1][1] if k <= len(asks) else 0.0
            denom = v_bid + v_ask
            imbalance = (v_bid - v_ask) / denom if denom > 0 else 0.0
            mlofi += weight * imbalance
        return mlofi

    def normalise_mad(self, value):
        """
        MAD normalisation. Use this, not z-score.
        OFI has heavy-tailed distributions with slow-decaying kurtosis.
        Standard normalisation underestimates tail risk.
        """
        if len(self.history) < 10:
            self.history.append(value)
            return 0.0
        arr = np.array(self.history)
        median = np.median(arr)
        mad = np.median(np.abs(arr - median))
        self.history.append(value)
        if mad == 0:
            return 0.0
        return (value - median) / mad


class FeatureBuilder:
    """
    Computes all features at each 1-minute bar.
    Tiers: 1A (MLOFI), 1B (spread), 1C (VWAP-mid), 1D (time-to-res),
           2A (Roll), 2B (cross-asset OFI), 2C (VPIN), 3 (momentum)
    """

    def compute_relative_spread(self, bid, ask):
        """Tier 1B. Execution gate: suppress above 95th pct rolling threshold."""
        mid = (bid + ask) / 2
        return (ask - bid) / mid if mid > 0 else 0.0

    def compute_vwap_deviation(self, vwap_buy, vwap_sell, mid):
        """
        Tier 1C. Preprint source — include, validate with ablation.
        Asymmetric reversion mechanism.
        """
        if mid == 0:
            return 0.0, 0.0
        return (vwap_buy - mid) / mid, (vwap_sell - mid) / mid

    def compute_roll_measure(self, prices, window=15):
        """
        Tier 2A. Roll (1984) implied spread estimator.
        Formula: 2 * sqrt(|cov(delta_p_t, delta_p_{t-1})|)
        Source: Easley et al. SSRN 2024 — preprint, validate with ablation.
        NOTE: Easley et al. SSRN 2024 is unverified — the Easley/Lopez de Prado
        group has produced extensive VPIN and microstructure work but this
        specific 2024 SSRN paper could not be independently confirmed.
        Verify before citing.
        """
        if len(prices) < window + 1:
            return 0.0
        returns = np.diff(prices[-window-1:])
        cov = np.cov(returns[:-1], returns[1:])[0, 1]
        return 2 * np.sqrt(abs(cov))

    def compute_vpin(self, volume_buys, volume_sells, window=15):
        """
        Tier 2C. EMPIRICALLY CONTESTED — low prior weight.
        Andersen & Bondarenko dispute unresolved. Candidate feature only.
        Crypto VPIN ~0.45-0.47 vs ~0.22 for equities.
        Formula: (1/W) * sum(|V_sell - V_buy| / V_total)
        """
        if len(volume_buys) < window:
            return 0.5
        total_imbalance = sum(
            abs(volume_sells[i] - volume_buys[i]) / (volume_buys[i] + volume_sells[i])
            for i in range(-window, 0)
            if (volume_buys[i] + volume_sells[i]) > 0
        )
        return total_imbalance / window

    def compute_time_to_resolution(self, market_open_time, current_time, horizon_minutes):
        """Tier 1D. Structural gate — not a predictor."""
        resolution_time = market_open_time + horizon_minutes * 60
        return max(0, resolution_time - current_time)

    def compute_cross_asset_btc_features(self, btc_mlofi, btc_roll):
        """
        Tier 2B. Cross-asset features for ETH, SOL, XRP prediction.
        Source: Easley et al. SSRN 2024 — preprint, validate with ablation.
        NOTE: Same unverified citation as compute_roll_measure above —
        could not be independently confirmed. Verify before citing.
        """
        return {"btc_mlofi": btc_mlofi, "btc_roll": btc_roll}


# Feature selection:
# Use mutual information to select top 64 features for Track A.
# from sklearn.feature_selection import mutual_info_classif
# Fit on training fold only. Never fit on validation or test.

# ============================================================
# SECTION 5: EXECUTION ARCHITECTURE
# ============================================================

class ExecutionGates:
    """
    Four-stage gate structure. Replaces original seven correlated binary checks.
    Correlated gates fire together during high-volatility periods — exactly when
    edge may be strongest — creating asymmetric suppression.
    """

    # ----------------------------------------------------------
    # STAGE 1: STRUCTURAL GATES
    # ----------------------------------------------------------
    def check_structural(self, seconds_to_resolution, p_market, fee_regime_active):
        """
        fee_regime_active: computed by FeeRegimeChecker.is_active() — not hardcoded.
        Returns (pass: bool, reason: str | None)
        """
        if seconds_to_resolution < 90:
            return False, "t_remaining < 90s"
        if seconds_to_resolution < 180 and abs(p_market - 0.5) < 0.10:
            return False, "near_expiry_noise_zone"
        if not fee_regime_active:
            return False, "outside_trained_fee_regime"
        return True, None

    # ----------------------------------------------------------
    # STAGE 2: ADVERSE SELECTION COMPOSITE
    # Coefficients stored as instance state after calibration fit.
    # ----------------------------------------------------------

class AdverseSelectionModel:
    """
    Logistic regression on three inputs: D (depth change), S (spread change),
    B (Binance spread percentile). Fit once on calibration sample.
    Coefficients stored on instance — not passed at call site.

    Refit trigger: distribution PSI > 0.25 on MLOFI distribution.
    Suppression threshold: calibrated at 20% FPR on calibration sample.
    Minimum 30 adversely-selected events before first fit.

    Depth coefficient will dominate (Kavajecz ordering): depth withdrawal
    precedes spread movement. Captured empirically, not assumed.
    """
    def __init__(self):
        self.beta_0 = None
        self.beta_D = None
        self.beta_S = None
        self.beta_B = None
        self.fpr_threshold = None
        self.is_fitted = False

    def fit(self, depth_changes, spread_changes, binance_pcts, labels, fpr_target=0.20):
        """
        labels: 1 = adversely selected (resolved against position), 0 = clean
        Minimum 30 positive labels required before calling.
        Sets fpr_threshold at the score achieving fpr_target false positive rate.
        """
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_curve
        assert sum(labels) >= 30, "Minimum 30 adversely-selected events required before fitting"
        X = np.column_stack([depth_changes, spread_changes, binance_pcts])
        model = LogisticRegression()
        model.fit(X, labels)
        self.beta_0 = model.intercept_[0]
        self.beta_D, self.beta_S, self.beta_B = model.coef_[0]

        # Calibrate threshold at fpr_target on calibration sample
        scores = model.predict_proba(X)[:, 1]
        fpr, tpr, thresholds = roc_curve(labels, scores)
        # Find threshold where FPR <= fpr_target
        valid = fpr <= fpr_target
        self.fpr_threshold = float(thresholds[valid][-1]) if valid.any() else 0.5
        self.is_fitted = True

    def score(self, depth_change, spread_change, binance_pct):
        """Returns P(adverse) using stored coefficients."""
        assert self.is_fitted, "Call fit() before score()"
        logit = (self.beta_0
                 + self.beta_D * depth_change
                 + self.beta_S * spread_change
                 + self.beta_B * binance_pct)
        return 1.0 / (1.0 + np.exp(-logit))

    def check(self, depth_change, spread_change, binance_pct):
        composite = self.score(depth_change, spread_change, binance_pct)
        if composite >= self.fpr_threshold:
            return False, f"adverse_composite={composite:.3f}", composite
        return True, None, composite


    # ----------------------------------------------------------
    # STAGE 3: NET EDGE CONDITION
    # ----------------------------------------------------------
def compute_net_edge(p_model, p_market, payout, spread_t, fee_t):
    """
    NE_t = p_model * payout - (1 - p_model) - spread_t - fee_t
    where payout = (1 - p_market) / p_market  (b in Kelly notation)
    fee_t MUST be queried per-contract from Polymarket API — never hardcode.
    Under normal conditions, minimum divergence ~15-35 bps before any trade.
    Source: Barnett & Wheatcroft 2023 JRSS (Kelly framework transfer)
    NOTE: Barnett & Wheatcroft 2023 JRSS is unverified — Wheatcroft is a
    confirmed prediction-market researcher but this specific co-authored JRSS
    paper could not be independently confirmed. Verify before citing. The
    Kelly mathematical framework itself is not in dispute regardless of citation.
    """
    # Correct Kelly expected return per dollar staked:
    #   p_model * b - (1 - p_model) - spread_t - fee_t
    # where b = payout = (1 - p_market) / p_market
    # Previous formula (p_model - p_market) * b expanded to:
    #   p_model * b - (1 - p_market)
    # which used market probability as the loss rate instead of model probability.
    # Correct formula uses (1 - p_model). The additive error was (p_model - p_market):
    # old formula over-subtracted when p_model > p_market, understating NE_t.
    # Sign of NE_t was preserved — gate did not misfire — but logged magnitudes
    # were wrong. Any non-zero NE_t threshold calibrated against old values must
    # be reset.
    return p_model * payout - (1 - p_model) - spread_t - fee_t

def check_net_edge(p_model, p_market, payout, spread_t, fee_t):
    ne_t = compute_net_edge(p_model, p_market, payout, spread_t, fee_t)
    if ne_t <= 0:
        return False, f"NE_t={ne_t:.5f}", ne_t
    return True, None, ne_t


    # ----------------------------------------------------------
    # STAGE 4: BAYESIAN SANDERINK MODEL RELIABILITY GATE
    # ----------------------------------------------------------
class SanderinkGate:
    """
    Beta posterior on true win rate. Operational from day one
    (no minimum n required — unlike frequentist test which needs n≈152).
    Updated after each resolved contract, not per trade.
    """
    def __init__(self, prior_wins=52, prior_losses=48, suspend_threshold=0.80):
        self.alpha = prior_wins      # Beta(52, 48): encodes 51-56% literature baseline
        self.beta = prior_losses
        self.suspend_threshold = suspend_threshold
        self.n_observations = 0

    def update(self, won: bool):
        """Call after each contract resolution."""
        if won:
            self.alpha += 1
        else:
            self.beta += 1
        self.n_observations += 1

    def compute_breakeven_win_rate(self, fee_t, payout_t):
        """
        Dynamic break-even win rate per contract.
        payout_t = (1 - p_market) / p_market (net fractional odds)
        p*_t = (1 + fee_t) / (payout_t + 1)

        CRITICAL: fee_t must be expressed as a fraction of STAKE (e.g., 0.02
        for 2%), not as a fraction of payout. Polymarket's January 2026
        nonlinear fee structure means fee_t varies with p_market.
        Fee-only break-even at p ≈ 0.50 is approximately 50.8% (based on
        ~1.56% peak fee). With spread and slippage, practical break-even is
        closer to 51–52%. Query fee_t per-contract from get_fee_rate() and
        verify the API returns stake-fraction units before passing to this
        function.
        """
        return (1 + fee_t) / (payout_t + 1)

    def check(self, fee_t, payout_t):
        """
        Suspend if P(p_true <= p*_t | data) > suspend_threshold.
        Initial threshold: 0.80. Pre-committed revision triggers:
          → 0.85: gate fires but subsequent contracts recover within 5 days
          → 0.75: predictive PSI > 0.10 but gate has not triggered
        """
        from scipy.stats import beta as beta_dist
        p_star = self.compute_breakeven_win_rate(fee_t, payout_t)
        posterior = beta_dist(self.alpha, self.beta)
        prob_at_or_below = posterior.cdf(p_star)
        if prob_at_or_below > self.suspend_threshold:
            return False, f"sanderink={prob_at_or_below:.3f}", prob_at_or_below, p_star
        return True, None, prob_at_or_below, p_star


class PositionSizer:
    """
    Fractional Kelly: f* = (b*p - q) / b
    b = (1 - p_market) / p_market
    Use 0.25x-0.50x throughout paper trading.
    Revision requires 50 resolved contracts + documented recalibration.
    """
    def __init__(self, kelly_fraction=0.25):
        self.kelly_fraction = kelly_fraction

    def compute(self, p_model, p_market, bankroll_usdc):
        b = (1 - p_market) / p_market
        q = 1 - p_model
        f_full = (b * p_model - q) / b
        f_fractional = max(0.0, f_full * self.kelly_fraction)
        return {
            "kelly_full": f_full,
            "kelly_fractional": f_fractional,
            "position_usdc": f_fractional * bankroll_usdc,
        }

# ============================================================
# SECTION 6: POLYMARKET API INTEGRATION
# ============================================================

import aiohttp

POLYMARKET_CLOB_BASE = "https://clob.polymarket.com"

async def get_order_book(session, token_id):
    """
    GET /book?token_id={token_id}
    Returns bids[], asks[], market metadata.
    Rate limit: 50 req / 10s
    """
    url = f"{POLYMARKET_CLOB_BASE}/book"
    params = {"token_id": token_id}
    async with session.get(url, params=params) as resp:
        return await resp.json()

async def get_fee_rate(session, token_id):
    """
    GET /fee-rate/{token_id}
    Returns fee rate for this specific contract.
    MUST be called per-contract before execution. Never hardcode.
    5-minute and 15-minute crypto markets are fee-enabled.

    IMPORTANT — Polymarket January 2026 fee structure change:
    Taker fees on 15-minute crypto markets are probability-NONLINEAR.
    Taker fees peak around 1.5–1.6% at p ≈ 0.50 and decline toward 0% at
    extremes. The exact formula is not publicly specified — treat the
    get_fee_rate() API response as authoritative. Do not hardcode a curve
    into any logic.
    Scale: 100 shares @ p=0.10 → ~$0.20; @ p=0.50 → ~$1.56; @ p=0.99 → ~$0.0025
    A maker rebate program operates simultaneously, funded by taker fees.

    Practical implication for NE_t: suppression rates will be highest early in
    a contract's life when p_market ≈ 0.50 (hourly reset opening price).
    The per-contract API call already handles this correctly — do not flatten
    to a constant fee rate or the NE_t calculation will be wrong at open.
    """
    url = f"{POLYMARKET_CLOB_BASE}/fee-rate/{token_id}"
    async with session.get(url) as resp:
        data = await resp.json()
        return float(data["fee_rate"])

def parse_book(book_data):
    """
    Extracts execution-relevant fields from /book response.
    Returns: best_ask_price, best_ask_size, best_bid_price, best_bid_size,
             spread_bps, mid. Returns None on empty book.
    """
    asks = sorted(book_data.get("asks", []), key=lambda x: float(x["price"]))
    bids = sorted(book_data.get("bids", []), key=lambda x: float(x["price"]), reverse=True)
    if not asks or not bids:
        return None
    best_ask = float(asks[0]["price"])
    best_bid = float(bids[0]["price"])
    mid = (best_ask + best_bid) / 2
    spread_bps = ((best_ask - best_bid) / mid) * 10000 if mid > 0 else None
    return {
        "best_ask_price": best_ask,
        "best_ask_size": float(asks[0]["size"]),
        "best_bid_price": best_bid,
        "best_bid_size": float(bids[0]["size"]),
        "spread_bps": spread_bps,
        "mid": mid,
    }

# ============================================================
# SECTION 7: BINANCE WEBSOCKET INTEGRATION
# ============================================================

class BinanceOrderBookManager:
    """
    Maintains local order book from diff depth stream.
    Keys normalised to float at insert — not left as strings.
    Prevents subtle ordering bugs if string comparison is ever invoked.
    Sync protocol per official Binance documentation.
    """
    def __init__(self, symbol, levels=10):
        self.symbol = symbol
        self.levels = levels
        self.bids = {}   # float price -> float qty
        self.asks = {}   # float price -> float qty
        self.last_update_id = 0
        self.synced = False

    def apply_update(self, event):
        """Normalise to float keys at insert, not at read time."""
        for price_str, qty_str in event["b"]:
            price, qty = float(price_str), float(qty_str)
            if qty == 0.0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty
        for price_str, qty_str in event["a"]:
            price, qty = float(price_str), float(qty_str)
            if qty == 0.0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty
        self.last_update_id = event["u"]

    def get_top_levels(self):
        sorted_bids = sorted(self.bids.items(), reverse=True)[:self.levels]
        sorted_asks = sorted(self.asks.items())[:self.levels]
        return sorted_bids, sorted_asks

    def detect_gap(self, event):
        """Trigger resync if sequence gap detected."""
        return event["U"] > self.last_update_id + 1

# Sync protocol:
#   1. Open WebSocket, buffer diff depth events
#   2. GET /api/v3/depth?symbol={symbol}&limit=1000 for snapshot
#   3. Discard events where u < snapshot.lastUpdateId
#   4. Verify first remaining event: U <= lastUpdateId+1 <= u
#   5. Apply buffered events sequentially
#   6. On gap (event.U > local_update_id + 1): resync from step 2

# ============================================================
# SECTION 8: MODEL ARCHITECTURE
# ============================================================

import torch
import torch.nn as nn

class TrackA_MLP(nn.Module):
    """
    Track A: Tabular input.
    64 MI-selected features → [256 → 128 → 64] → sigmoid output.
    Architecture consistent with Kuznetsov et al. 2026
    (CEUR-WS proceedings — suggestive, not peer-reviewed validation).
    """
    def __init__(self, input_dim=64, dropout=0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        return self.net(x).squeeze(-1)


class TrackB_SequenceModel(nn.Module):
    """
    Track B: Sequence input — 1-min OHLCV + order book, 30-60 bar lookback.
    CNN → BiLSTM → Attention → sigmoid output.

    EVIDENCE CAVEAT: Architecture validated for crypto regression tasks.
    Binary direction at 5-15 min under walk-forward temporal splits NOT
    independently validated. Treat as architecture prior — validate in
    your own walk-forward before committing.

    num_layers=2 on BiLSTM: PyTorch silently ignores dropout when num_layers=1.
    Using 2 layers makes dropout active on the inter-layer connection.
    """
    def __init__(self, input_channels, seq_len=60, dropout=0.3):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(input_channels, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(64, 128, kernel_size=3, padding=1),
            nn.ReLU(),
        )
        # num_layers=2 required for dropout to be active between layers.
        # At num_layers=1 PyTorch issues a UserWarning and applies no dropout.
        self.bilstm = nn.LSTM(
            128, 64,
            num_layers=2,
            batch_first=True,
            bidirectional=True,
            dropout=dropout,
        )
        # Additive attention (scalar projection across full 128-dim BiLSTM output).
        # Functionally valid. Not scaled dot-product attention (Vaswani et al.).
        # If model underfits sequence structure, multi-head attention is the
        # natural upgrade — replace nn.Linear(128,1) with nn.MultiheadAttention.
        self.attention = nn.Linear(128, 1)
        self.fc = nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        x = self.conv(x.permute(0, 2, 1)).permute(0, 2, 1)
        lstm_out, _ = self.bilstm(x)
        attn_weights = torch.softmax(self.attention(lstm_out), dim=1)
        context = (attn_weights * lstm_out).sum(dim=1)
        return self.fc(context).squeeze(-1)


class MetaLearner:
    """
    Stacks Track A and Track B probability outputs.
    CRITICAL: Calibrate both tracks BEFORE fitting meta-learner.
    Calibration and meta-learner fit on validation set only.
    """
    def __init__(self):
        from sklearn.isotonic import IsotonicRegression
        from sklearn.linear_model import LogisticRegression
        self.calibrator_a = IsotonicRegression(out_of_bounds="clip")
        self.calibrator_b = IsotonicRegression(out_of_bounds="clip")
        self.meta = LogisticRegression()

    def fit(self, p_a_val, p_b_val, y_val):
        """
        Fit on validation set only. Dead parameters removed (p_a_test, p_b_test
        were previously accepted but never used).
        """
        p_a_cal = self.calibrator_a.fit_transform(p_a_val, y_val)
        p_b_cal = self.calibrator_b.fit_transform(p_b_val, y_val)
        X_meta = np.column_stack([p_a_cal, p_b_cal])
        self.meta.fit(X_meta, y_val)

    def predict(self, p_a, p_b):
        p_a_cal = self.calibrator_a.transform(p_a)
        p_b_cal = self.calibrator_b.transform(p_b)
        X = np.column_stack([p_a_cal, p_b_cal])
        return self.meta.predict_proba(X)[:, 1]


def gmadl_loss(returns_true, returns_pred, a=1.0, b=2.0):
    """
    GMADL loss function.
    Source: Bieganowski & Ślepaczuk arXiv:2412.18405
    CAVEAT: Same research group as primary feature source.
    Theoretical logic sound. Independent replication not yet available.

    INPUTS MUST BE RETURNS, NOT PROBABILITIES.
    Both tensors must be in the range [-1, 1] (or similar signed return scale).
    If calling with sigmoid outputs (0-1 range), convert first:
        returns_pred = 2 * p_model - 1
    An assertion enforces this — silent garbage from wrong input range
    would otherwise corrupt direction term computation.

    Formula: (1/N) * sum(|R_i - R_hat_i| * (1 - a*sign(R_i*R_hat_i))^b)
    Parameters a and b tune directional accuracy vs return magnitude balance.
    """
    # Only a range sanity check here — catching the raw-probability mistake belongs
    # in validate_gmadl_inputs(), called once at model init, not on every forward pass.
    # A batch where the model predicts "up" on every sample is legitimate (trending
    # market); a min() < 0 assertion would kill training precisely when the model
    # is most confident and correct.
    assert returns_true.abs().max() <= 2.0, \
        "returns_true out of expected range — probabilities? Expected returns in [-2, 2]"
    assert returns_pred.abs().max() <= 2.0, \
        "returns_pred out of expected range — pass 2*p-1, not raw p"

    direction_term = (1 - a * torch.sign(returns_true * returns_pred)).pow(b)
    return torch.mean(torch.abs(returns_true - returns_pred) * direction_term)


def validate_gmadl_inputs(returns_true, returns_pred):
    """
    Call ONCE during model initialisation with a representative warm-up batch.
    Do NOT call inside the training loop — a legitimately all-bullish batch
    (every p > 0.505 during a trending period) produces all-positive returns_pred,
    which would fail a min() < 0 check despite being valid input.

    Checking at init with a known mixed-direction sample catches the configuration
    mistake (passing raw probabilities instead of 2*p-1) without polluting the
    hot path or firing on legitimate batches.

    Usage:
        # Before training loop, with a small representative sample:
        sample_true = compute_returns(X_val[:32])
        sample_pred = 2 * model(X_val[:32]) - 1
        validate_gmadl_inputs(sample_true, sample_pred)
    """
    assert returns_pred.min() < -0.01, (
        "returns_pred contains no negative values in validation sample — "
        "pass 2*p-1, not raw probabilities. "
        f"Got min={returns_pred.min():.4f}, max={returns_pred.max():.4f}"
    )
    assert returns_true.abs().max() <= 2.0, (
        "returns_true out of expected range in validation sample — "
        "probabilities? Expected returns in [-2, 2]"
    )
    assert returns_pred.abs().max() <= 2.0, (
        "returns_pred out of expected range in validation sample — "
        f"got max abs = {returns_pred.abs().max():.4f}"
    )


# Class balancing: w_c = n_total / (2 * n_c)
# Required for near-50/50 binary distributions.
# Pass to BCEWithLogitsLoss via pos_weight, or use sample_weight in sklearn.

# Trainer initialisation stub (execution/executor.py)
# validate_gmadl_inputs MUST be called here — before the training loop starts.
# The function lives in loss.py; enforcement lives here so it cannot be skipped.

def init_trainer(sample_returns_true, sample_returns_pred,
                 prior_wins=52, prior_losses=48,
                 suspend_threshold=0.80, kelly_fraction=0.25):
    """
    Called once before the training loop with a pre-computed representative
    sample. Caller is responsible for model inference and the 2*p-1 conversion,
    which is model-track-specific:
      - TrackA: flat 64-dim input  → model(X_val_flat[:64])
      - TrackB: 3D sequence input  → model(X_val_seq[:64])
    Accepting pre-converted tensors here avoids a shape mismatch crash before
    validate_gmadl_inputs is ever reached (the check it is designed to enforce).

    Example call site (executor.py, before training loop):
        with torch.no_grad():
            probs_a = track_a(X_val_tabular[:64])
            probs_b = track_b(X_val_sequence[:64])
            probs   = meta_learner.predict(probs_a.numpy(), probs_b.numpy())
        returns_pred = torch.tensor(2 * probs - 1, dtype=torch.float32)
        returns_true = torch.tensor(
            [1.0 if y == 1 else -1.0 for y in y_val[:64]], dtype=torch.float32
        )
        sanderink, sizer = init_trainer(returns_true, returns_pred)
    """
    from models.loss import validate_gmadl_inputs
    # Raises AssertionError if raw probabilities passed instead of 2*p-1.
    # Fails loud at init — not silently mid-training.
    validate_gmadl_inputs(sample_returns_true, sample_returns_pred)
    sanderink = SanderinkGate(
        prior_wins=prior_wins,
        prior_losses=prior_losses,
        suspend_threshold=suspend_threshold,
    )
    sizer = PositionSizer(kelly_fraction=kelly_fraction)
    return sanderink, sizer


# ============================================================
# SECTION 9: VALIDATION PROTOCOL
# Non-negotiable. Any deviation invalidates results.
# ============================================================

VALIDATION_PROTOCOL = """
1. TEMPORAL SPLIT (chronological — never shuffle):
   - Train:      first 70% of data
   - Validation: next 15%  ← calibration, threshold opt, meta-learner only
   - Test:       final 15% ← held out until final evaluation

2. WALK-FORWARD:
   Rolling retraining. Temporal gap between train and val >= prediction horizon
   in bars. Prevents leakage from FAISS indexes, rolling normalisation stats,
   and any forward-filled features.

3. DATASET BOUNDARIES:
   Split at TRAINING_BOUNDARIES (Section 3.1). Never train across a Binance
   fee regime change.

4. MULTI-SEED ENSEMBLE:
   Train on 5 seeds. Average probability outputs.
   Seeds: [42, 137, 256, 512, 1024]

5. PAPER TRADING GATE:
   Minimum 200 resolved markets in production pipeline paper trading
   (real API calls, real data) before any live capital. Not backtesting.

6. LEAKAGE CHECK:
   Reported accuracy > 62%: treat as potential leakage.
   The 92.4% in some papers is almost certainly k-fold on time series.
"""

# ============================================================
# SECTION 10: LOGGING SCHEMA AND WRITER
# ============================================================

import sqlite3
import queue
import threading

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS candidate_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_ms                INTEGER NOT NULL,
    contract_id                 TEXT NOT NULL,
    market_open_time            TEXT NOT NULL,
    seconds_to_resolution       REAL NOT NULL,
    mlofi_value                 REAL,
    mlofi_raw_components        TEXT,
    binance_spread_percentile   REAL,
    polymarket_depth_best_ask   REAL,
    polymarket_depth_5s_change  REAL,
    polymarket_spread_5s_change REAL,
    adverse_selection_composite REAL,
    roll_15                     REAL,
    roll_50                     REAL,
    vpin_15                     REAL,
    vpin_50                     REAL,
    vwap_deviation_buy          REAL,
    vwap_deviation_sell         REAL,
    p_model                     REAL,
    p_market                    REAL,
    payout                      REAL,
    fee_t                       REAL,
    spread_t                    REAL,
    ne_t_computed               REAL,
    gate_structural_pass        INTEGER,
    gate_structural_reason      TEXT,
    gate_adverse_pass           INTEGER,
    gate_adverse_composite_value REAL,
    gate_adverse_fpr_threshold  REAL,
    gate_ne_positive            INTEGER,
    gate_sanderink_pass         INTEGER,
    gate_sanderink_pstar        REAL,
    gate_sanderink_posterior    REAL,
    gate_sanderink_n_obs        INTEGER,
    executed                    INTEGER,
    suppression_reason          TEXT,
    position_size_fraction      REAL,
    kelly_full                  REAL,
    kelly_fractional            REAL,
    t_ofi_signal_ms             INTEGER,
    t_ask_depth_drop_ms         INTEGER,
    t_ask_price_move_ms         INTEGER,
    ask_depth_at_signal         REAL,
    ask_depth_5s_after_signal   REAL,
    operational_lag_ms          INTEGER,
    mlofi_dist_psi_7d           REAL,
    mlofi_pred_psi_7d           REAL,
    feature_stability_alert     INTEGER,
    feature_stability_type      TEXT,
    resolved_direction          INTEGER,
    model_correct               INTEGER,
    ne_t_realised               REAL
);
CREATE INDEX IF NOT EXISTS idx_executed    ON candidate_trades(executed);
CREATE INDEX IF NOT EXISTS idx_suppression ON candidate_trades(suppression_reason);
CREATE INDEX IF NOT EXISTS idx_resolved    ON candidate_trades(resolved_direction);
"""

class LogWriter:
    """
    Persistent SQLite connection with background write queue.
    Eliminates per-insert open/close overhead at logging frequency.
    Thread-safe: producer calls enqueue(), background thread drains queue.
    Flush and close explicitly on shutdown.
    """
    def __init__(self, db_path: str, batch_size: int = 50):
        self.db_path = db_path
        self.batch_size = batch_size
        self._queue = queue.Queue()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA_SQL)  # execute() stops at first semicolon; executescript() runs all statements
        # No explicit commit needed: executescript() issues an implicit COMMIT
        # before executing, so the schema is already committed on return.
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def enqueue(self, record: dict):
        """Non-blocking. Call from execution loop."""
        self._queue.put(record)

    def _worker(self):
        batch = []
        while True:
            try:
                record = self._queue.get(timeout=1.0)
                if record is None:   # Shutdown sentinel
                    if batch:
                        self._flush(batch)
                    break
                batch.append(record)
                if len(batch) >= self.batch_size:
                    self._flush(batch)
                    batch = []
            except queue.Empty:
                if batch:
                    self._flush(batch)
                    batch = []

    def _flush(self, batch: list):
        if not batch:
            return
        keys = list(batch[0].keys())
        placeholders = ", ".join(["?"] * len(keys))
        col_str = ", ".join(keys)
        rows = [list(r.values()) for r in batch]
        self._conn.executemany(
            f"INSERT INTO candidate_trades ({col_str}) VALUES ({placeholders})", rows
        )
        self._conn.commit()

    def shutdown(self):
        """Flush remaining records and close connection cleanly."""
        self._queue.put(None)
        self._thread.join()
        self._conn.close()

# ============================================================
# SECTION 11: FEATURE STABILITY MONITORING
# ============================================================

def compute_psi(expected, actual, bins=10):
    """
    Population Stability Index.
    Standard thresholds:
      < 0.10: stable
      0.10-0.25: monitor
      > 0.25: suspend execution, investigate, retrain
    Note: For heavy-tailed OFI, PSI > 0.25 can be triggered by a mean shift
    of ~0.6 standard deviations. First alert = investigate, not auto-retrain.
    """
    min_val = min(min(expected), min(actual))
    max_val = max(max(expected), max(actual))
    if min_val == max_val:
        # Constant series: MLOFI stuck at one value, likely a data pipeline failure.
        # PSI cannot be computed (identical bin edges). Raise rather than return 0.0
        # because a flat MLOFI series warrants immediate investigation — it is a more
        # alarming signal than a high PSI value and should not be silently swallowed.
        raise ValueError(
            f"compute_psi: constant series detected (min=max={min_val}). "
            "Data pipeline failure likely — MLOFI should not be constant."
        )
    bin_edges = np.linspace(min_val, max_val, bins + 1)
    exp_counts = np.histogram(expected, bins=bin_edges)[0] + 1e-6
    act_counts = np.histogram(actual, bins=bin_edges)[0] + 1e-6
    exp_pct = exp_counts / exp_counts.sum()
    act_pct = act_counts / act_counts.sum()
    return float(np.sum((act_pct - exp_pct) * np.log(act_pct / exp_pct)))

def compute_predictive_psi(mlofi_values, outcomes, training_bucket_win_rates, bins=5):
    """
    Predictive PSI — tighter threshold (> 0.10 triggers alert).
    Detects relationship degradation without distributional shift.
    Predictive degradation is the more dangerous failure mode:
    it won't trigger distribution PSI while destroying model performance.
    """
    if len(training_bucket_win_rates) != bins:
        raise ValueError(
            f"compute_predictive_psi: training_bucket_win_rates has "
            f"{len(training_bucket_win_rates)} elements but bins={bins}. "
            "Recompute training baseline to match the configured bin count."
        )
    quantiles = np.quantile(mlofi_values, np.linspace(0, 1, bins + 1))
    unique_quantiles = np.unique(quantiles)
    if len(unique_quantiles) < len(quantiles):
        # Degenerate case: low-variance or repeated MLOFI values produce duplicate
        # boundaries. Occurs during early paper trading when history is short —
        # exactly when the predictive PSI monitor is most needed.
        # Recompute with fewer bins on unique boundaries rather than silently
        # misbucking observations near the duplicated value.
        bins = len(unique_quantiles) - 1
        quantiles = unique_quantiles
        if len(training_bucket_win_rates) != bins:
            raise ValueError(
                f"compute_predictive_psi: quantile deduplication reduced bins to {bins}, "
                f"but training_bucket_win_rates has {len(training_bucket_win_rates)} elements. "
                "Recompute training baseline at the reduced bin count. "
                "Silently truncating would make the PSI comparison apples-to-oranges."
            )
    bucket_win_rates = []
    for i in range(bins):
        # Last bucket uses <= so the maximum value is included.
        # Strict < on quantiles[bins] == max(mlofi_values) silently drops the
        # highest MLOFI observations — exactly the strong-signal tail.
        upper = (mlofi_values < quantiles[i+1]) if i < bins - 1 else (mlofi_values <= quantiles[i+1])
        mask = (mlofi_values >= quantiles[i]) & upper
        bucket_win_rates.append(np.mean(np.array(outcomes)[mask]) if mask.sum() > 0 else 0.5)
    bucket_win_rates = np.array(bucket_win_rates) + 1e-6
    training_rates = np.array(training_bucket_win_rates) + 1e-6
    return float(np.sum((bucket_win_rates - training_rates) * np.log(bucket_win_rates / training_rates)))

# ============================================================
# SECTION 12: PRE-COMMITMENT PROTOCOL
# ============================================================

PRE_COMMITMENT = {
    "1_lag_decision_rules": "See LAG_DECISION_RULES. Do not change based on data.",

    "2_adverse_composite": {
        "method": "logistic_regression",
        "inputs": ["depth_change_5s", "spread_change_5s", "binance_spread_pct"],
        "min_adverse_events_to_fit": 30,
        "fpr_target": 0.20,
        "refit_trigger": "distribution_psi > 0.25 on MLOFI",
        "revision_trigger": "gate attribution shows systematic suppression of high-NE_t correct trades",
    },

    "3_lag_sample_size": {
        "pilot_n": 40,
        "formula": "n = (1.96 * sigma_pilot / 5)**2",
        "stratification": "equal across asian / eu / us sessions",
        "stopping_rule": "n >= n_required per stratum AND 95CI excludes both 10s and 30s",
        "duration_estimate": "6 weeks lower bound at 30% signal rate",
        "WARNING": "30% signal rate is an assumption. Actual duration may be longer.",
    },

    "4_position_size": {
        "formula": "f* = (b*p - q) / b",
        "fraction": 0.25,
        "max_fraction": 0.50,
        "revision_trigger": "50 resolved contracts with documented recalibration",
    },

    "5_sanderink_threshold": {
        "initial": 0.80,
        "revision_to_085": "posterior crosses 0.80 but contracts recover within 5 days",
        "revision_to_075": "predictive_psi > 0.10 but gate has not triggered",
    },
}

# ============================================================
# SECTION 13: ANALYTICS QUERIES
# ============================================================

ANALYTICS_QUERIES = {

    "gate_suppression_rate": """
        SELECT suppression_reason, COUNT(*) AS n, AVG(ne_t_computed) AS avg_ne
        FROM candidate_trades
        WHERE executed = 0
        GROUP BY suppression_reason
        ORDER BY n DESC
    """,

    "counterfactual_ne_on_suppressed": """
        SELECT suppression_reason,
               AVG(ne_t_computed)     AS avg_counterfactual_ne,
               AVG(model_correct)     AS win_rate_if_executed
        FROM candidate_trades
        WHERE executed = 0 AND ne_t_computed > 0
        GROUP BY suppression_reason
    """,

    "adverse_selection_calibration": """
        SELECT
            ROUND(gate_adverse_composite_value, 1) AS composite_bucket,
            COUNT(*)          AS n,
            AVG(model_correct) AS win_rate,
            AVG(CASE WHEN gate_adverse_pass = 0 THEN 1.0 ELSE 0.0 END) AS suppression_rate
        FROM candidate_trades
        WHERE resolved_direction IS NOT NULL
        GROUP BY composite_bucket
        ORDER BY composite_bucket
    """,

    # Cross-tab: suppression reason vs NE_t sign.
    # Distinguishes correct suppression (no edge anyway) from miscalibration
    # (gate blocking trades where model had genuine edge).
    # High count in ne_positive=1 rows means a gate is destroying edge.
    # High count in ne_positive=0 rows means a gate is correctly filtering noise.
    "gate_cross_tab": """
        SELECT
            suppression_reason,
            CASE WHEN ne_t_computed > 0 THEN 1 ELSE 0 END AS ne_positive,
            COUNT(*)           AS n,
            AVG(model_correct) AS win_rate_on_suppressed,
            AVG(ne_t_computed) AS avg_ne
        FROM candidate_trades
        WHERE executed = 0
          AND resolved_direction IS NOT NULL
        GROUP BY suppression_reason,
                 CASE WHEN ne_t_computed > 0 THEN 1 ELSE 0 END
        ORDER BY suppression_reason, ne_positive DESC
    """,
}

# ============================================================
# SECTION 14: TEST SUITE REQUIREMENTS
# ============================================================

REQUIRED_TESTS = """
tests/
  test_mlofi.py
    - MLOFI weight sum: sum(1/k for k in 1..10) — not off-by-one (1/(k-1))
    - MAD normalisation returns 0 on constant series
    - MAD normalisation handles zero MAD without division error
    - Heavy tail: MAD < std for synthetic Cauchy-distributed series

  test_gates.py
    - Structural gate: blocks t_remaining < 90s
    - Structural gate: blocks near-expiry noise zone
    - Structural gate: blocks when FeeRegimeChecker.is_active() returns False
    - FeeRegimeChecker: returns False when current date crosses a post-training boundary
    - FeeRegimeChecker: returns True for dates within trained regime
    - NE_t = 0 when p_model == p_market and spread=fee=0
    - NE_t < 0 with realistic spread/fee and zero edge
    - Sanderink: suspends when posterior > 0.80
    - Sanderink: Beta(52,48) prior mean = 0.52 (correct literature encoding)
    - Sanderink: p*_t formula — verify break-even at zero NE_t
    - AdverseSelectionModel: raises AssertionError if fit called with < 30 adverse events
    - AdverseSelectionModel: fpr_threshold stored as instance state after fit
    - AdverseSelectionModel: score() raises AssertionError before fit

  test_order_book.py
    - BinanceOrderBookManager: keys stored as float after apply_update
    - BinanceOrderBookManager: gap detection triggers resync signal
    - BinanceOrderBookManager: apply_update removes zero-qty levels
    - Polymarket parse_book: handles empty book gracefully (returns None)

  test_logging.py
    - LogWriter: all candidate trades written (executed and suppressed)
    - LogWriter: ne_t_computed logged even when NE_t gate fails
    - LogWriter: resolution fields NULL until contract resolved
    - LogWriter: shutdown() flushes queue before closing connection
    - LogWriter: concurrent enqueue() calls are thread-safe
    - LogWriter: all three indices exist after init (idx_executed, idx_suppression,
      idx_resolved) — verifies executescript() not execute(); execute() silently
      drops every statement after the first semicolon

  test_gmadl.py
    - gmadl_loss: no assertion error on all-positive returns_pred (trending batch)
    - gmadl_loss: no assertion error on all-negative returns_pred (trending batch)
    - gmadl_loss: AssertionError when abs().max() > 2.0 (gross range violation)
    - gmadl_loss: penalises wrong-direction predictions more than magnitude errors
    - validate_gmadl_inputs: AssertionError when passed raw probabilities (all positive)
    - validate_gmadl_inputs: passes on mixed-sign returns_pred
    - Conversion: 2*p - 1 maps sigmoid output (0-1) to return range (-1, 1)
    - init_trainer: calls validate_gmadl_inputs (patch validate, assert called once)
    - init_trainer: propagates AssertionError from validate_gmadl_inputs to caller
    - init_trainer: accepts pre-converted tensors, not model + raw data
      (no torch inference inside init_trainer — shape coupling eliminated)

  test_psi.py
    - PSI = 0 for identical distributions
    - Distribution PSI alert fires at > 0.25
    - Predictive PSI alert fires at > 0.10
    - Predictive PSI < distribution PSI for same distributional shift
      (validates predictive monitor is more sensitive)
    - compute_psi raises ValueError on constant series (min == max)
    - ValueError message identifies failure as data pipeline issue, not drift
    - compute_predictive_psi: maximum MLOFI value is assigned to last bucket
      (verifies last-bucket <= boundary; previously dropped highest observations)
    - compute_predictive_psi: handles duplicate quantile boundaries gracefully
      (degenerate case: constant or near-constant MLOFI during early paper trading)
    - compute_predictive_psi: bin count reduces correctly when deduplication fires
    - compute_predictive_psi: raises ValueError when training_bucket_win_rates length
      does not match reduced bin count after deduplication
      (prevents apples-to-oranges PSI comparison at mismatched bin counts)
    - compute_predictive_psi: raises ValueError when training_bucket_win_rates length
      mismatches bins on the normal path (no deduplication required to trigger)
      (catches misconfiguration before cryptic numpy broadcast error mid-function)

  test_lag_measurement.py
    - t_ask_depth_drop logged BEFORE t_ask_price_move (Kavajecz ordering)
    - operational_lag = t_ask_depth_drop - t_ofi_signal (not price lag)
    - execution_viable_size fixed at startup, not computed per-event
    - trigger fires on |mlofi_normalised| > lag_measurement_trigger_threshold
    - trigger does NOT require a trained model

  test_meta_learner.py
    - MetaLearner.fit() accepts exactly 3 args (p_a_val, p_b_val, y_val)
    - predict() returns values in [0, 1]
    - calibration applied before meta-learner, not after
"""

# ============================================================
# SECTION 15: DIRECTORY STRUCTURE
# ============================================================

DIRECTORY_STRUCTURE = """
polymarket-ofi/
├── config.py                      # CONFIG, TRAINING_BOUNDARIES, LAG_DECISION_RULES
├── .env                           # API keys — never commit
├── .env.example                   # Template — commit this
│
├── api/
│   ├── binance.py                 # BinanceOrderBookManager + sync protocol
│   └── polymarket.py              # get_order_book, get_fee_rate, parse_book
│
├── lag_measurement/
│   ├── collector.py               # LagMeasurementCollector
│   ├── pilot_analysis.py          # sigma_pilot → n_required computation
│   └── decision.py                # LAG_DECISION_RULES evaluator
│
├── feature_engineering/
│   ├── mlofi.py                   # MLOFICalculator (MAD normalisation)
│   ├── features.py                # FeatureBuilder (all tiers)
│   └── selection.py               # MI-based 64-feature selector
│
├── models/
│   ├── track_a.py                 # TrackA_MLP
│   ├── track_b.py                 # TrackB_SequenceModel (num_layers=2)
│   ├── meta_learner.py            # MetaLearner (isotonic cal + LR stacking)
│   └── loss.py                    # gmadl_loss (with input assertion)
│
├── execution/
│   ├── gates.py                   # ExecutionGates, AdverseSelectionModel,
│   │                              # SanderinkGate, PositionSizer
│   ├── fee_regime.py              # FeeRegimeChecker
│   └── executor.py                # Main execution loop; init_trainer() calls validate_gmadl_inputs at startup
│
├── monitoring/
│   ├── psi.py                     # compute_psi, compute_predictive_psi
│   └── alerts.py                  # Alert routing + threshold constants
│
├── logging/
│   ├── schema.sql                 # SCHEMA_SQL
│   ├── writer.py                  # LogWriter (persistent conn + write queue)
│   └── analytics.py               # ANALYTICS_QUERIES (incl. gate_cross_tab)
│
├── validation/
│   ├── splitter.py                # Temporal split + boundary enforcement
│   ├── walk_forward.py            # Rolling retraining with purge gaps
│   └── leakage_check.py           # Flags accuracy > 62%
│
└── tests/
    ├── test_mlofi.py
    ├── test_gates.py
    ├── test_order_book.py
    ├── test_logging.py
    ├── test_gmadl.py
    ├── test_psi.py
    ├── test_lag_measurement.py
    └── test_meta_learner.py
"""
