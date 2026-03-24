"""
Polymarket Crypto Direction Prediction System — Configuration
Spec v2.6, Sections 2, 3.1, 3.4, 12
"""

CONFIG = {
    # Scope
    "assets": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
    "market_type": "5min_15min_binary_direction",

    # Bybit spot data
    "exchange": "bybit_spot",
    "exchange_testnet": True,  # flip to False for live
    "bybit_depth_levels": 10,
    "bybit_ws_channel": "orderbook.200.{symbol}",
    "bybit_ws_channel_type": "spot",

    # MLOFI
    "mlofi_weights": "inverse_depth",
    "mlofi_normalisation": "MAD",
    "mlofi_mad_window": 1000,

    # Lag measurement trigger (proxy threshold — used BEFORE model is trained)
    "lag_measurement_trigger_threshold": 1.0,

    # Execution gates
    "spread_gate_percentile": 95,
    "spread_gate_window": 1000,
    "suppress_seconds_to_resolution": 90,
    "suppress_near_expiry_seconds": 180,
    "suppress_near_expiry_delta": 0.10,

    # Adverse selection composite
    "adverse_depth_window_s": 10,
    "adverse_spread_window_s": 10,
    "adverse_fpr_target": 0.20,
    "adverse_min_events_to_fit": 30,

    # Sanderink gate (Bayesian)
    "sanderink_prior_wins": 52,
    "sanderink_prior_losses": 48,
    "sanderink_suspend_threshold": 0.80,

    # Position sizing
    "kelly_fraction": 0.25,
    "kelly_revision_min_contracts": 50,

    # PSI monitoring
    "psi_window_days": 7,
    "psi_distribution_alert": 0.25,
    "psi_predictive_alert": 0.10,

    # Validation
    "train_split": 0.70,
    "val_split": 0.15,
    "test_split": 0.15,
    "temporal_gap_bars": 60,
    "n_seeds": 5,
    "paper_trading_min_markets": 200,

    # Polymarket API
    "polymarket_clob_base": "https://clob.polymarket.com",
    "polymarket_fee_endpoint": "/fee-rate/{token_id}",
    "polymarket_book_endpoint": "/book?token_id={token_id}",
    "polymarket_rate_limit_books": 50,

    # Bybit WebSocket
    "bybit_ws_spot": "wss://stream.bybit.com/v5/public/spot",
    "bybit_ws_spot_testnet": "wss://stream-testnet.bybit.com/v5/public/spot",
    "bybit_kline_channel": "kline.{interval}.{symbol}",

    # Bybit REST
    "bybit_rest_base": "https://api.bybit.com",
    "bybit_rest_testnet": "https://api-testnet.bybit.com",
    "bybit_kline_endpoint": "/v5/market/kline",
    "bybit_orderbook_endpoint": "/v5/market/orderbook",
}

# Training data boundaries — never train across these dates.
# Bybit spot: no identified fee regime changes or structural events
# in the Apr 2025 – Mar 2026 training window. Standard 0.1% maker/taker
# throughout. USDC fee cuts (Mar 2026) affect only USDC pairs, not USDT.
TRAINING_BOUNDARIES = []

# Pre-committed decision rules for lag measurement outcomes.
# These do not change based on measurement results.
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

# Pre-commitment protocol (Section 12)
PRE_COMMITMENT = {
    "1_lag_decision_rules": "See LAG_DECISION_RULES. Do not change based on data.",

    "2_adverse_composite": {
        "method": "logistic_regression",
        "inputs": ["depth_change_5s", "spread_change_5s", "bybit_spread_pct"],
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

# Validation protocol (Section 9)
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
   Split at TRAINING_BOUNDARIES (Section 3.1). Never train across an exchange
   fee regime change. Bybit spot: no boundaries in current training window.

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

ENSEMBLE_SEEDS = [42, 137, 256, 512, 1024]
