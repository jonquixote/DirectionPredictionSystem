#!/usr/bin/env python3
"""
Retrain with mid_price_dev_1d (1-day EWM span = 1440 1-min bars).
Same as retrain_v3.py but with shorter EWM window.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import validation.retrain_v3 as v3

# Override EWM span to 1 day
v3.EWM_SPAN = 1440  # 1 day × 1440 minutes
v3.EWM_WARMUP_DAYS = 3  # only need 3 days warmup for 1-day EWM

print(f"EWM span: {v3.EWM_SPAN} (1 day)")
print(f"EWM warmup: {v3.EWM_WARMUP_DAYS} days")
print()

if "--output-dir" not in sys.argv:
    sys.argv.extend(["--output-dir", "/data/models_v3_1d"])

v3.main()
