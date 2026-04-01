#!/usr/bin/env python3
"""
Retrain H60 model without mid_price (v2).

Removes `mid_price` from FEATURE_COLS to eliminate price-level trend leakage.
Also removes raw `spread` (price-level dependent) — keeps `relative_spread`.

Outputs to /data/models_v2/ with symlink latest_h60_v2.
"""
import sys
import os

# Monkey-patch FEATURE_COLS before importing run_training
# This is intentional — we want the exact same pipeline with different features
import validation.run_training as training

V2_FEATURE_COLS = [f for f in training.FEATURE_COLS
                   if f not in ("mid_price", "spread")]

print(f"V1 features ({len(training.FEATURE_COLS)}): {training.FEATURE_COLS}")
print(f"V2 features ({len(V2_FEATURE_COLS)}): {V2_FEATURE_COLS}")
print(f"Removed: {set(training.FEATURE_COLS) - set(V2_FEATURE_COLS)}")
print()

# Override
training.FEATURE_COLS = V2_FEATURE_COLS

# Override output dir via sys.argv
# Usage: python -m validation.retrain_v2 [--horizon 300]
if "--output-dir" not in sys.argv:
    sys.argv.extend(["--output-dir", "/data/models_v2"])

training.main()
