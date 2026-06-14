# FAIR-VALUE DEVIATION PROBE — VERDICT

**Sealed:** 2026-06-14. **Branch:** `probe/fairvalue`.
**Contract:** `FAIRVALUE_PREREGISTRATION.md` (GATE 0 + 5 controls, all frozen pre-result).
**Outcome:** **Stage-1 KILL — killed by the concentration control.**

## Result (n = 50,802,919 Polymarket prints, verbatim)
```
[Control 5] dual-fee supra-cost (Gaussian-EWMA dev):
  Polymarket(0.07): 83.94%   Kalshi(0.0175): 91.96%
[Control 1] sigma robustness (Kalshi fee): EWMA 91.96 / trailing-1d 92.20 / oracle 91.82%
  mean|dev_gauss| = 0.0836
[Control 2] empirical fair value: supra-cost 94.96%, mean|dev_emp| = 0.1136
[concentration] ~10% per elapsed decile (FLAT); signed-bias +0.15 (dec0) decaying to 0.00
  most-concentrated decile = 8 holds 11.0%  (need >= 60%)
[Control 3] OOS: early 11.0% / late 10.8%
VERDICT: NO STRUCTURE — KILL.  KILLED BY: concentration >= 60%
```

## What it means — the most important measurement in the project
**92% of prints deviate from arithmetic fair value by more than cost.** That headline is a
screaming false positive, and four independent controls strangled it before it could become
a trade:
- **Not directional:** signed bias ≈ 0 across the window (max +0.15 in decile 0, decaying to
  0). You cannot predict WHICH WAY the contract is mispriced.
- **Not timed:** ~10% of excess in every elapsed decile, uniform. You cannot predict WHEN.
- **Mostly our own model error, not market mispricing:** `mean|dev| vs empirical (0.114) >
  vs Gaussian (0.084)`. The gap is substantially the gap between our Φ-arithmetic and reality,
  not between the market's price and reality. Φ is a mediocre binary pricer; Control 2 existed
  precisely to catch this confound and it caught it.

A deviation that is symmetric, unbiased, uniform, and partly self-inflicted is unharvestable:
fading it is a coin flip after fees. **A sloppy probe reports "92% supra-cost deviation =
massive edge" and lights money on fire. This one reported the truth.** Best single
demonstration in the whole arc of why the controls were worth the care.

## Disposition
Fair-value-deviation seat: **closed.** The one residual flicker — faint positive bias in
elapsed deciles 0-1 — is handed to inversion Stage 2 as its single best lead (round-trips
operate exactly there). If Stage 2 dies on that slice too, the probe sweep is complete.

## Operational note (cost paid, lesson logged)
Stage 1 OOM'd the VPS twice before the bounded rewrite: v1 held ~50M prints × 12 fields in
Python lists (~18GB); v2 cached all 4 symbols' 1s arrays + fetchall'd 12M-row cells. v3
(per-symbol, chunked cursor→numpy, scipy ndtr/searchsorted, 10GB ulimit guard) ran in minutes
at ~3GB peak. The box was hard-reset once (snapshot `vps-n2-pre-reset-20260613` taken first;
all data + services recovered). **Lesson: size memory against the 50M-row store before
launching; guard long jobs with `ulimit -v`.**
