# OFI-LAB: CLONE SETUP & DEVELOPMENT GUIDE
**Repository name:** `ofi-lab`
**Branched from:** `polymarket-ofi` (frozen 2026-04-28)
**Purpose:** The development lab for retraining and improving h300_v1.
             The original `polymarket-ofi` container is the control.

---

## WHY WE RENAMED IT

The original project lives in `/app/DirectionPredictionSystem/polymarket-ofi/`
and runs as `polymarket-ofi-paper-trader`. This is the **control** — it must
not be confused with the lab work. Any time you see `polymarket-ofi`, it means
the frozen original. Any time you see `ofi-lab`, it means the development fork.

Container naming convention:
- `polymarket-ofi-paper-trader` → **ORIGINAL CONTROL** (read-only in spirit)
- `ofi-lab-paper-trader-{model_name}` → lab experiments
- `golden-goose-h300v1:frozen-20260428` → frozen archive (never run in prod)

---

## CLONE SETUP (Run Once)

```bash
# 1. Clone the codebase from the frozen snapshot to local
cp -r ~/Code/DirectionPredictionSystem/freeze-20260428/polymarket-ofi-frozen-20260428/ \
      ~/Code/DirectionPredictionSystem/ofi-lab/

cd ~/Code/DirectionPredictionSystem/ofi-lab

# 2. Initialize git (the original has no git — we add it now)
git init
git add .
git commit -m "chore: initial commit from frozen polymarket-ofi 2026-04-28

This is a byte-for-byte clone of polymarket-ofi as it existed on 2026-04-28.
The h300_v1 model (MD5: 8c25ce5c0e56fec14989a130094af0f9) was the golden goose:
BTCUSDT @ 900s, gate 0.560, 67.44% win rate, peak $13,986 from $10.

FROZEN ARCHIVE: freeze-20260428/ — never modify.
ALL CHANGES: document in CHANGE_LOG_AND_RULES.md before executing."

# 3. Create the lab data directory structure (separate from original /data)
ssh polymarket-server "mkdir -p /data/lab/models_lab /data/lab/logs_lab /data/lab/features_v3"
# features_v3 is shared read-only — do not duplicate unless running experiments
```

---

## PROJECT STRUCTURE

```
ofi-lab/
├── .git/
├── README.md                          ← This file
├── CHANGE_LOG_AND_RULES.md            ← MUST READ before touching anything
├── GOLDEN_GOOSE_FREEZE_PROTOCOL.md    ← Theory of operation + freeze procedure
├── requirements.txt                   ← py-clob-client-v2 (updated from v1)
│
├── data/
│   ├── download_orderbook.py          ← Step 0: fetch Bybit L2 snapshots
│   ├── build_features.py              ← Step 1: base MLOFI/OFI/VWAP features
│   ├── add_rolling_features.py        ← Step 2: rolling windows (30s/60s)
│   └── build_features_v3.py           ← Step 3: VWAP + mlofi_momentum + cross-asset
│
├── validation/
│   └── run_training.py                ← The canonical training script
│                                        --horizon 300 --feature-dir /data/features_v3
│
├── trading/
│   ├── paper_trader.py                ← Inference + order placement
│   │                                    H300_SUPPRESS_DURATIONS = {"h300": {300}}
│   └── polymarket_discovery.py        ← Contract discovery (CLOB v2 after migration)
│
├── api/
│   └── polymarket.py                  ← CLOB client wrapper (v2 after migration)
│
├── feature_engineering/               ← Shared feature computation modules
│   └── ...
│
├── models/
│   └── ...                            ← Model architecture definitions
│
└── tests/                             ← TDD gate — all must pass before deployment
    ├── conftest.py
    ├── test_feature_schema.py         ← GATE 1
    ├── test_model_determinism.py      ← GATE 2
    ├── test_gate_behavior.py          ← GATE 3
    ├── test_suppress_durations.py     ← GATE 4
    ├── test_pipeline_integrity.py     ← GATE 5
    └── test_clob_v2_smoke.py          ← GATE 6 (new — CLOB v2 migration)
```

---

## TDD TEST STUBS

Copy these into `tests/` and complete the implementation before any changes.

### tests/test_feature_schema.py
```python
"""
GATE 1: Feature Schema Integrity
Verifies that the output of the v3 feature pipeline exactly matches
the 33-column schema that h300_v1 was trained on.
"""
import pytest
import pandas as pd
import json
from pathlib import Path

FEATURE_NAMES_PATH = Path("/data/models/latest_h300/feature_names.json")

def get_expected_feature_cols():
    with open(FEATURE_NAMES_PATH) as f:
        return json.load(f)  # list of 33 column names

def test_features_v3_schema_matches_h300v1():
    """
    Build features for a single known date and verify the column schema
    exactly matches the 33 features h300_v1 was trained on.
    """
    expected_cols = get_expected_feature_cols()
    assert len(expected_cols) == 33, f"Expected 33 feature cols, got {len(expected_cols)}"

    # Load one features_v3 parquet file
    sample_file = next(Path("/data/features_v3/BTCUSDT/").glob("*.parquet"))
    df = pd.read_parquet(sample_file)

    # Must have exactly 33 feature cols + cts = 34 total
    assert "cts" in df.columns, "Missing 'cts' timestamp column"
    feature_cols = [c for c in df.columns if c != "cts"]
    assert sorted(feature_cols) == sorted(expected_cols), (
        f"Schema mismatch!\n"
        f"Missing from file: {set(expected_cols) - set(feature_cols)}\n"
        f"Extra in file:     {set(feature_cols) - set(expected_cols)}"
    )

def test_features_v3_no_nulls_in_feature_cols():
    """
    Feature columns must have no NaN values after rolling windows are applied.
    NaNs silently degrade model performance.
    """
    expected_cols = get_expected_feature_cols()
    sample_file = next(Path("/data/features_v3/BTCUSDT/").glob("*.parquet"))
    df = pd.read_parquet(sample_file)
    for col in expected_cols:
        null_count = df[col].isnull().sum()
        assert null_count == 0, f"Column {col} has {null_count} NaN values"
```

### tests/test_model_determinism.py
```python"""
GATE 2: Model Determinism
Verifies that retraining with identical data, params, and random_state=42
produces the exact same model binary (same MD5 hash).
This test is the proof that we can reliably retrain h300_v1.
"""
import pytest
import hashlib
import subprocess
from pathlib import Path

GOLDEN_MODEL_MD5 = "8c25ce5c0e56fec14989a130094af0f9"
GOLDEN_MODEL_PATH = Path("/data/models/latest_h300/model.lgb")

def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def test_golden_model_md5_unchanged():
    """
    The original frozen model binary must never change.
    If this test fails, the golden goose has been tampered with.
    """
    actual_md5 = md5_file(GOLDEN_MODEL_PATH)
    assert actual_md5 == GOLDEN_MODEL_MD5, (
        f"GOLDEN GOOSE TAMPERED!\n"
        f"Expected MD5: {GOLDEN_MODEL_MD5}\n"
        f"Actual MD5:   {actual_md5}\n"
        f"The model binary has been modified. Restore from freeze-20260428/."
    )
```

### tests/test_suppress_durations.py
```python
"""
GATE 4: H300_SUPPRESS_DURATIONS Enforcement
Verifies that h300 model NEVER fires on 300s contracts.
This is the configuration that makes the golden goose work.
"""
import pytest
from trading.paper_trader import H300_SUPPRESS_DURATIONS

def test_h300_suppresses_300s_duration():
    """
    h300 must be suppressed on 300s contracts.
    It ONLY trades on 900s contracts. This is intentional and sacred.
    """
    assert "h300" in H300_SUPPRESS_DURATIONS, (
        "H300_SUPPRESS_DURATIONS must contain 'h300' key"
    )
    assert 300 in H300_SUPPRESS_DURATIONS["h300"], (
        "h300 must suppress 300s contracts. "
        "This config is what makes h300 work on 900s. DO NOT REMOVE."
    )

def test_h300_suppress_does_not_suppress_900s():
    """
    h300 must NOT be suppressed on 900s contracts — that is the golden duration.
    """
    if "h300" in H300_SUPPRESS_DURATIONS:
        assert 900 not in H300_SUPPRESS_DURATIONS["h300"], (
            "h300 must NOT suppress 900s contracts — "
            "900s is the golden goose deployment duration."
        )
```

---

## INLINE DOCUMENTATION STANDARDS

All functions in the lab codebase must follow this docstring format:

```python
def build_features_v3(input_dir: str, output_dir: str, symbols: list) -> None:
    """
    Pipeline Step 3: V3 Feature Enrichment.

    Reads parquet files from features_v2/ and adds:
      - vwap_2m_deviation: VWAP deviation over 2-minute rolling window
      - vwap_dev_velocity: rate of change of vwap_deviation
      - vwap_dev_30s_std: 30-second rolling std of vwap_deviation
      - mlofi_momentum: rate of change of MLOFI signal
      - cross_asset_*: BTC/ETH/SOL/XRP correlation features

    OUTPUT SCHEMA: 33 feature columns + cts = 34 total columns.
    This schema MUST match feature_names.json in the h300_v1 model directory.

    CHANGE HISTORY:
      - v3 created 2026-03-22 (frozen in polymarket-ofi)
      - Any modification constitutes a new version (features_v4/)
        and must be logged in CHANGE_LOG_AND_RULES.md before execution.

    Args:
        input_dir: Path to features_v2/ directory
        output_dir: Path to write features_v3/ parquet files
        symbols: List of symbols, e.g. ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]

    Raises:
        SchemaValidationError: if output does not have exactly 34 columns
        FileNotFoundError: if input_dir does not exist or contains no parquet files
    """
```

---

## THE ONLY PERMITTED RETRAIN COMMAND

This is the canonical command that reproduces h300_v1. Only the date
arguments may change for a standard retrain. Everything else is frozen.

```bash
# Standard retrain (only TRAIN_END and VAL_END change)
python validation/run_training.py \
  --horizon 300 \
  --feature-dir /data/features_v3 \
  --output-dir /data/lab/models_lab \
  --train-end YYYY-MM-DD \
  --val-end YYYY-MM-DD
```

Any invocation that changes `--horizon`, `--feature-dir` to anything
other than `features_v3`, or adds new hyperparameter flags constitutes
a new experiment and must be branched and logged.
