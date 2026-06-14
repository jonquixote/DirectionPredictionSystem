# INTRA-WINDOW INVERSION PROBE — VERDICT

**Sealed:** 2026-06-14. **Branch:** `probe/inversion`.
**Contract:** `INVERSION_PREREGISTRATION.md`.
**Outcome:** Stage 1 PASS (physics real) → **Stage 2 KILL** (not harvestable), earned on the
most-favorable ground.

## Stage 1 — structure exists (physics real)
Flip histogram, 4/4 symbols + pooled PASS: 5.18 flips/window in first-66%, terminal dampening
0.37 (≤0.5). Price genuinely oscillates around window-open and dampens late. Caveat logged:
the flip metric counts sub-cost jitter; harvestability is entirely Stage 2.

## Stage 2 — round-trip backtest, KILL (verbatim)
Real Polymarket prints, through-level fill realism, maker fee + 30/69bps adverse haircut,
completed-vs-incomplete-leg cost, per-window-attempted EV. **Pointed at the early-window
slice** (deciles 0-1, the one flicker fair-value Stage 1 left standing).
```
--- early slice (first leg fills in deciles 0-1) ---
  d=1c H=0.30%: n=63169 EV/win=$-0.0647 bootLB=$-0.0661 complete_rt=85%
  d=2c H=0.30%: n=63104 EV/win=$-0.0635 bootLB=$-0.0651 complete_rt=82%
  d=3c H=0.30%: n=62938 EV/win=$-0.0649 bootLB=$-0.0666 complete_rt=78%
  (H=0.69% ~0.003 worse across the board; "all" slice ~identical)
best early-slice delta=2c: n=63104 EV=$-0.0635 bootLB=$-0.0651
VERDICT: KILL — earned on the most-favorable ground (early slice, best delta)
```

## Why it dies despite real oscillation
78-85% of round-trips DO complete — Stage 1's physics holds. But completing nets only
~2δ − fees − haircut (≈ +$0.008/round-trip at δ=1¢), while the 15-22% **incomplete** legs
carry to resolution as directional holds that lose large. A leg fails to round-trip exactly
when price trends through your resting quote and keeps going — i.e. when you've been adversely
selected. The oscillation pays pennies on reversion and costs dollars on continuation; the net
is −$0.064/window. n=63,104 ≫ 2000, bootstrap LB deeply negative, on the most-favorable slice
and best δ. A genuine kill on the best available ground, not a kill by neglect.

## Disposition
Inversion seat: **closed.** This was the sixth and final mechanism in the sweep — see
`PROBE_SWEEP_VERDICT.md`.
