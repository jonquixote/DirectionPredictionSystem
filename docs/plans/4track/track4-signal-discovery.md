# Track 4 — Signal Discovery (Unconstrained, Hard-Gated)

**Mandate:** find a model with a genuine, executable signal. Free approach; three
non-negotiables: (1) target ≠ direction at 5–15min; (2) EV-gate vs executable
bid/ask before any claim; (3) pre-registered kill criterion before training.

## Recommended first hypothesis (justified; may be swapped with justification)
**P(|move| > x) into strike/range markets.** Rationale: vol clusters (predictable
physics) while sign is a near-martingale (paid lesson); Kalshi strike ladders were
illiquid at probe time but hourly/EOD BTC/ETH above/below markets carry real volume —
re-verify liquidity as step 0 of the MI screen. Fallbacks, in order:
1. **Cross-venue transient divergence:** PM vs Kalshi mid on the same 15m underlying —
   both calibrated individually; do they diverge >2c transiently, and does the wider
   venue revert? (Track 2's `ticks` table answers this for free — same-timestamp mids
   on both venues; no new collection.)
2. **Depth/imbalance → repricing-speed** (meta-signal for the Track-2 taker branch):
   predict WHICH windows reprice slowly, not which direction.
3. Short-horizon realized-vol → next-window contract mispricing of extremes.

## Mandatory process (gates, in order — each gate is a kill point)
```
Step 0  Liquidity check on the target market (executable spread + depth); if the
        instrument can't absorb $100 at <=2c spread, hypothesis dead before MI.
Step 1  MI/correlation screen: features vs target on HISTORICAL data (v3.db OFI
        features, logger books, Track-2 ticks). Threshold registered up front:
        MI < 0.005 bits (or |spearman| < 0.03) after date-block cross-validation
        -> hypothesis dead; document, next hypothesis. NO MODEL CODE before this passes.
Step 2  Pre-registration doc: hypothesis, target, features, model class, data split,
        kill criterion — one file, committed, frozen (pattern: prereg_track1.json).
Step 3  Week-1 checkpoint: EV vs EXECUTABLE bid/ask (not mid) on live forward data,
        cluster-robust CI. Negative or flat -> stop, restart at Step 0 with next
        hypothesis. No "one more week".
Step 4  Only after a positive week-1: full pipeline — provenance-tagged executable
        prices (source column, fail-loud, no fallback), settlement labels, dedup at
        write time, warmup excluded at schema level.
```

## Architecture sketch (Step 4, only if reached)
```
feeds:   venue websocket/poller (executable bid/ask, provenance col) + Coinbase spot
labels:  settlement backfill (Track-1 pattern) — never derived spot
store:   one row per (window, model) written at fire time with the executable
         price snapshot embedded; no post-hoc joins for EV
gate:    EV = p_model − executable_entry − fee, per-model, evaluated pre-fire;
         below-threshold predictions logged as no-fire (with reason)
report:  weekly cluster-robust EV vs executable, auto-generated
```

## Deliverables
(1) MI screen results, (2) prereg doc, (3) week-1 checkpoint report (EV vs
executable), (4) if continuing: model + live-probe plan.

## Timeline
Step 0–1: 2–3 days (mostly offline on existing data). Step 2: half day.
Step 3: +7 days calendar. Step 4: 1–2 weeks build if reached.

## Kill criteria (the track's own)
Three consecutive hypotheses dead at Step 1–3 → Track 4 pauses; program review.
The discipline IS the deliverable — a fast, documented "no" beats a slow artifact.
