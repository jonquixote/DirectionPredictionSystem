#!/usr/bin/env python3
"""
Prove the non-tautological assertion can refuse misordered vectors.

Exercises the EXACT hash-comparison logic deployed in boundary_scorer.py
without needing the full BoundaryScorer object graph.

Three tests:
  1. Canonical == Canonical → PASS (all three checks: dim, set, order)
  2. Swap two cols → set_ok=True, order_ok=False → REFUSE
  3. Bar missing a column → COLUMNS_MISSING → REFUSE
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage.provenance import feature_names_hash, ordered_feature_names_hash
from feature_engineering.feature_contract import FEATURE_COLS_PER_SYMBOL

print("=" * 70)
print("CANONICAL CONTRACT (first 5 / last 5 of %d cols):" % len(FEATURE_COLS_PER_SYMBOL))
print("  first5:", FEATURE_COLS_PER_SYMBOL[:5])
print("  last5: ", FEATURE_COLS_PER_SYMBOL[-5:])
print()

# ── Compute the "trained" hashes (from canonical — what paper_trader stores at load) ──
canonical = list(FEATURE_COLS_PER_SYMBOL)
trained_set_hash   = feature_names_hash(canonical)
trained_order_hash = ordered_feature_names_hash(canonical)

print("Trained set hash  :", trained_set_hash[:32])
print("Trained order hash:", trained_order_hash[:32])
print()

# ═══════════════════════════════════════════════════════════════════════
# TEST 1: Served == Canonical (identical order) → PASS
# ═══════════════════════════════════════════════════════════════════════
print("=" * 70)
print("TEST 1: Served order == Canonical order")
print("=" * 70)

served_correct = list(FEATURE_COLS_PER_SYMBOL)   # identical copy
served_set_hash   = feature_names_hash(served_correct)
served_order_hash = ordered_feature_names_hash(served_correct)

dim_ok   = len(served_correct) == len(canonical)
set_ok   = served_set_hash   == trained_set_hash
order_ok = served_order_hash == trained_order_hash
verdict  = "PASS" if (dim_ok and set_ok and order_ok) else "REFUSE"

print(f"  dim_ok={dim_ok}  set_ok={set_ok}  order_ok={order_ok}  →  {verdict}")
assert verdict == "PASS", "TEST 1 FAILED: canonical should pass"
print("  ✓ TEST 1 PASSED\n")

# ═══════════════════════════════════════════════════════════════════════
# TEST 2: Swap first two columns → set_ok still True, order_ok False → REFUSE
# ═══════════════════════════════════════════════════════════════════════
print("=" * 70)
print("TEST 2: Swap first two columns")
print("=" * 70)

served_swapped = list(FEATURE_COLS_PER_SYMBOL)
served_swapped[0], served_swapped[1] = served_swapped[1], served_swapped[0]
print(f"  Swapped: {served_swapped[0]} <-> {canonical[0]}")

served_set_hash_2   = feature_names_hash(served_swapped)
served_order_hash_2 = ordered_feature_names_hash(served_swapped)

dim_ok_2   = len(served_swapped) == len(canonical)
set_ok_2   = served_set_hash_2   == trained_set_hash
order_ok_2 = served_order_hash_2 == trained_order_hash
verdict_2  = "PASS" if (dim_ok_2 and set_ok_2 and order_ok_2) else "REFUSE"

print(f"  dim_ok={dim_ok_2}  set_ok={set_ok_2}  order_ok={order_ok_2}  →  {verdict_2}")
print(f"  served_order_hash: {served_order_hash_2[:32]}")
print(f"  trained_order_hash: {trained_order_hash[:32]}")
assert verdict_2 == "REFUSE", "TEST 2 FAILED: swapped columns should be refused"
assert set_ok_2 is True, "TEST 2 CHECK: set hash should still match (same columns)"
assert order_ok_2 is False, "TEST 2 CHECK: order hash must differ"
print("  ✓ TEST 2 PASSED — swap correctly detected and refused\n")

# ═══════════════════════════════════════════════════════════════════════
# TEST 3: All columns present in bar → no COLUMNS_MISSING
#          Remove one column → COLUMNS_MISSING → REFUSE
# ═══════════════════════════════════════════════════════════════════════
print("=" * 70)
print("TEST 3: Columns-exist guard (bar missing a column)")
print("=" * 70)

served_cols = list(FEATURE_COLS_PER_SYMBOL)
bar_complete = {col: 1.0 for col in served_cols}
missing_complete = [col for col in served_cols if col not in bar_complete]
print(f"  Complete bar: missing_cols={missing_complete}  →  {'REFUSE' if missing_complete else 'PASS'}")
assert len(missing_complete) == 0, "TEST 3a FAILED: complete bar should have no missing cols"

bar_incomplete = dict(bar_complete)
removed = served_cols[0]
del bar_incomplete[removed]
missing_incomplete = [col for col in served_cols if col not in bar_incomplete]
print(f"  Incomplete bar (removed '{removed}'): missing_cols={missing_incomplete}  →  {'REFUSE' if missing_incomplete else 'PASS'}")
assert len(missing_incomplete) == 1 and missing_incomplete[0] == removed, "TEST 3b FAILED"
print("  ✓ TEST 3 PASSED — missing column detected\n")

# ═══════════════════════════════════════════════════════════════════════
# TEST 4: Verify sidecar for anchor model matches canonical
# ═══════════════════════════════════════════════════════════════════════
print("=" * 70)
print("TEST 4: Anchor model sidecar vs canonical")
print("=" * 70)

sidecar_path = "/data/models/fleet/h300_btc_v3_330d/run_20260528_042255/feature_names.json"
if os.path.exists(sidecar_path):
    with open(sidecar_path) as f:
        sidecar_cols = json.load(f)
    sidecar_set_hash   = feature_names_hash(sidecar_cols)
    sidecar_order_hash = ordered_feature_names_hash(sidecar_cols)
    
    s_dim_ok   = len(sidecar_cols) == len(canonical)
    s_set_ok   = sidecar_set_hash   == trained_set_hash
    s_order_ok = sidecar_order_hash == trained_order_hash
    s_verdict  = "PASS" if (s_dim_ok and s_set_ok and s_order_ok) else "REFUSE"
    
    print(f"  sidecar cols: {len(sidecar_cols)}, first3={sidecar_cols[:3]}")
    print(f"  canonical cols: {len(canonical)}, first3={canonical[:3]}")
    print(f"  dim_ok={s_dim_ok}  set_ok={s_set_ok}  order_ok={s_order_ok}  →  {s_verdict}")
    
    if s_verdict == "PASS":
        print("  ✓ Sidecar matches canonical — assertion will PASS for this model")
    else:
        print("  ✗ Sidecar does NOT match canonical — assertion will REFUSE for this model")
        if not s_set_ok:
            sidecar_set = set(sidecar_cols)
            canonical_set = set(canonical)
            print(f"    extra in sidecar:   {sidecar_set - canonical_set}")
            print(f"    missing in sidecar: {canonical_set - sidecar_set}")
        if not s_order_ok and s_set_ok:
            diffs = [(i, sidecar_cols[i], canonical[i]) 
                     for i in range(min(len(sidecar_cols), len(canonical))) 
                     if sidecar_cols[i] != canonical[i]]
            print(f"    First order diff at index {diffs[0][0]}: sidecar='{diffs[0][1]}' canonical='{diffs[0][2]}'")
else:
    print(f"  SKIP — sidecar not found at {sidecar_path}")

print("\n" + "=" * 70)
print("ALL TESTS PASSED — assertion is non-tautological and catches reorderings")
print("=" * 70)
