import pytest

from regime.tagger import compute_regime, RegimeTags


SAMPLE_THRESHOLDS = {
    "BTCUSDT": {
        "vwap_dev_30s_std": {"p25": 0.00004, "p50": 0.00009, "p75": 0.00016},
        "mlofi_60s_std":    {"p25": 0.0002,  "p50": 0.0005,  "p75": 0.0009},
        "relative_spread":  {"p25": 0.00008, "p50": 0.00018, "p75": 0.00031},
        "spread_5m_pct":    {"p25": 0.20,    "p50": 0.50,    "p75": 0.80},
        "mlofi_momentum":   {"p25": -0.0003, "p50": 0.0001,  "p75": 0.0004},
        "vwap_2m_deviation":{"p25": -0.0008, "p50": 0.0,     "p75": 0.0008},
    }
}


def test_low_volatility_tags_low():
    feature_row = {
        "vwap_dev_30s_std": 0.00002,
        "mlofi_60s_std": 0.0001,
        "relative_spread": 0.00009,
        "spread_5m_pct": 0.30,
        "mlofi_momentum": 0.0,
        "vwap_2m_deviation": 0.0,
    }
    tags = compute_regime("BTCUSDT", feature_row, SAMPLE_THRESHOLDS)
    assert tags.volatility == "low"


def test_high_volatility_tags_high():
    feature_row = {
        "vwap_dev_30s_std": 0.00020,
        "mlofi_60s_std": 0.0012,
        "relative_spread": 0.00040,
        "spread_5m_pct": 0.85,
        "mlofi_momentum": 0.0005,
        "vwap_2m_deviation": 0.001,
    }
    tags = compute_regime("BTCUSDT", feature_row, SAMPLE_THRESHOLDS)
    assert tags.volatility == "high"
    assert tags.liquidity == "thin"


def test_normal_liquidity_tags_normal():
    feature_row = {
        "vwap_dev_30s_std": 0.00009,
        "mlofi_60s_std": 0.0005,
        "relative_spread": 0.00018,
        "spread_5m_pct": 0.50,
        "mlofi_momentum": 0.0001,
        "vwap_2m_deviation": 0.0,
    }
    tags = compute_regime("BTCUSDT", feature_row, SAMPLE_THRESHOLDS)
    assert tags.liquidity == "normal"


def test_trending_when_momentum_and_deviation_align():
    feature_row = {
        "vwap_dev_30s_std": 0.00009,
        "mlofi_60s_std": 0.0005,
        "relative_spread": 0.00018,
        "spread_5m_pct": 0.50,
        "mlofi_momentum": 0.001,    # well above p75
        "vwap_2m_deviation": 0.002,  # well above p75
    }
    tags = compute_regime("BTCUSDT", feature_row, SAMPLE_THRESHOLDS)
    assert tags.trend == "trending"


def test_unknown_symbol_returns_unknown_tags():
    tags = compute_regime("ZZZUSDT", {}, SAMPLE_THRESHOLDS)
    assert tags == RegimeTags(volatility="unknown", liquidity="unknown",
                                trend="unknown")
