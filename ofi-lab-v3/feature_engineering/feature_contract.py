from __future__ import annotations
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Training truth — the ONLY canonical feature lists.

# Joint model base features (33 columns, includes mid_price, spread, symbol_cat)
FEATURE_COLS = [
    # Point-in-time features (vpin dropped — SHAP-confirmed noise)
    "mlofi", "ofi", "mid_price", "spread", "relative_spread",
    "vwap_deviation", "roll",
    "mlofi_1", "mlofi_2", "mlofi_3", "mlofi_4", "mlofi_5",
    "mlofi_6", "mlofi_7", "mlofi_8", "mlofi_9", "mlofi_10",
    # Rolling window features (mlofi_120s_mean dropped — diminishing returns)
    "mlofi_30s_mean", "mlofi_60s_mean",
    "ofi_30s_mean", "ofi_60s_mean",
    "mlofi_30s_std", "mlofi_60s_std",
    "ofi_60s_std",
    "spread_5m_pct",
    # VWAP enrichment (strengthen rank-1 SHAP signal)
    "vwap_2m_deviation", "vwap_dev_velocity", "vwap_dev_30s_std",
    # Order flow enrichment (strengthen rank-2/4 SHAP signal)
    "mlofi_momentum",
    # Cross-asset features
    "btc_vwap_deviation", "btc_mlofi_30s_mean", "eth_mlofi_30s_mean",
    # Categorical
    "symbol_cat",
]

# Per-symbol model base features (32 columns, drops symbol_cat)
FEATURE_COLS_PER_SYMBOL = [c for c in FEATURE_COLS if c != "symbol_cat"]

# Joint model stationary features (32 columns, mid_price replaced by mid_price_dev_30d, spread dropped)
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

# Per-symbol model stationary features (31 columns, drops symbol_cat)
V3_MODEL_FEATURE_COLS_PER_SYMBOL = [c for c in V3_MODEL_FEATURE_COLS if c != "symbol_cat"]

def resolve_model_feature_contract(booster, sidecar_path: Path | None) -> list[str]:
    """Resolve a model's trained feature contract.

    These lists define what training produces. Serving reads each model's
    own resolved contract from resolve_model_feature_contract(), never from
    a hardcoded serving list.

    Priority:
      1. booster.feature_name() — if non-generic (no Column_* prefix)
      2. sidecar feature_names.json — if exists and parses
      3. Raise RuntimeError — refuse to serve with unknown contract
    """
    try:
        names = booster.feature_name()
    except Exception as e:
        logger.warning("Failed to call booster.feature_name(): %s", e)
        names = None

    if names and all(isinstance(n, str) for n in names) and len(names) > 0:
        if not any(n.startswith("Column_") for n in names):
            return list(names)
        else:
            logger.warning("Booster has generic Column_* names; checking sidecar feature contract.")

    if sidecar_path is not None:
        sidecar_path = Path(sidecar_path)
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r") as f:
                    sidecar_names = json.load(f)
                if isinstance(sidecar_names, list) and all(isinstance(n, str) for n in sidecar_names):
                    return sidecar_names
            except Exception as e:
                logger.error("Failed to parse sidecar %s: %s", sidecar_path, e)

    raise RuntimeError(f"Could not resolve feature contract (booster names: {names}, sidecar: {sidecar_path})")
