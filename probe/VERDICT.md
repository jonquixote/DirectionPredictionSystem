# 10-DAY SIGNAL HUNT — VERDICT

**Sealed:** 2026-06-13. **Branch:** `probe/10day-signal-hunt`.
**Contract:** `probe/PREREGISTRATION.md` (Day-0, amended twice on the record — Gate 3
staleness/clip, B4 print-staleness — both proposed before any downstream result and
both made the gates *harder*).

---

## DECISION TABLE OUTCOME

| Gate A (copy-as-taker) | Gate B (true-OFI model) | Registered decision |
|---|---|---|
| **FAIL** | **FAIL** | **KILL** — registered strategies to zero-touch shadow; export; write-up is the salvage |

Both registered strategies are dead. This is the pre-registered both-fail row. It is a
legitimate, designed outcome of the probe, not a disappointment: the probe was built to
distinguish "no signal" from "signal we cannot capture," and it returned a clean answer.

---

## GATE B — Track B true-OFI model — FAILED (evidence verbatim)

Single-touch evaluation, test span `window_start >= 2026-05-24`, read once. Outcomes
scored against market `outcomePrices` (Gate-3 amendment). Primary population = fires
whose reference market print is ≤15s old (B4 staleness amendment).

```
candidate fires (pre-clustering): 797273
print-age distribution: p50=1.0s p90=12.0s  <=15s: 92.7%  15-60s: 6.8%  60-120s: 0.5%

EV by print-age bucket (clustered per bucket):
  <=15s:   n=27475  EV/share=$+0.0035  CI95=[-0.0024,+0.0093]  acc=50.84%  wilsonLB=50.24%
  15-60s:  n= 9704  EV/share=$+0.0114  CI95=[+0.0024,+0.0203]  acc=52.44%  wilsonLB=51.45%
  60-120s: n= 1830  EV/share=$+0.0053  CI95=[-0.0149,+0.0254]  acc=55.52%  wilsonLB=53.23%

PRIMARY (fresh <=15s, clustered, ALL pooled):
  n=27475  EV/share=$+0.0035  CI95=[-0.0023,+0.0094]  acc=50.84%  wilsonLB=50.24%
  window-open slice (t_rem>=0.97): n=10680  EV/share=$-0.0132  CI95=[-0.0227,-0.0037]  acc=48.55%

GATE B: EV>0 & EV_LB>0: False | accLB>=52%: False (50.24%) | n>=2000: True (27475)  -> FAIL
```

**Reading:** the only CI-positive "EV" lives in the *stale* (15-60s) print bucket — the
latency mirage made explicit: scoring against prices nobody offers anymore. The fresh
slice is indistinguishable from zero. The pure-signal slice (window-open, where OFI is
all the model has and mechanical features are neutral) is **CI-fully-negative**. True
Cont-2014 order flow, computed correctly (smoke: contemporaneous corr 0.726/0.734) from
100ms deltas, carries no tradeable edge against the market's own price at 5/15min
horizons. Combined with four prior months ruling out order-book *state*: **both state and
flow tested under a sound harness; neither carries tradeable signal at these horizons.**

Grid context: 16-config surface flat within 0.0015 AUC; fold AUC ~0.83/0.86 is
mid-window *conditional* structure (dist-from-open × time-remaining), which the market
prices too — not alpha. EV vs market price, fees in, was always the only number that
mattered, and it is zero.

---

## GATE A — copy-as-taker — FAILED (evidence verbatim)

53.4M trades across 64,504 markets (5m+15m × btc/eth/sol/xrp, ~6 weeks). A1 gates passed
as amended: 100% outcome join; window-mapping agreement 99.73% on |move|≥20bps.
W1/W2 split, W2 = most recent 12 days (28.8% volume). Eligibility ≥200 W1 / ≥50 W2
resolved. **Binding mode = exclude-wallets** (>15% W1 clip-exposure excluded: 5,364/8,938).

```
PRIMARY (exclude-wallets): eligible 3574 wallets
  Spearman(W1,W2): GROSS rho=0.2235 (p=1.0e-41) | NET rho=0.3071 (p=6.6e-79)
  top decile (k=357) W2:  net=$-38,632  CI95=[-183,755, +87,050]
  control            W2:  net=$-35,440  CI95=[ -75,051, +21,570]
  -> CI separation: OVERLAPPING  -> prong 1 FAILS
```

Gate A prong 1 (cohort beats random control, non-overlapping 95% CIs) fails on the
binding mode. Copy-as-taker is dead.

**Maker-blindness scope (registered):** this is "no persistent **taker** edge," not "no
one wins." Maker identity is invisible in the free feed.

---

## PRINCIPAL POSITIVE FINDING — skill persists; fees confiscate it

The sharpest result of the four-month effort. The top-decile taker cohort's gross W2 PnL
is large and positive; the 0.07·p(1−p) taker fee is larger:

```
top decile (primary), W2:  gross +$565,720   fees -$604,352   net -$38,632
```

Rank persistence is real and strong (NET rho 0.307 at p≈10⁻⁷⁹; GROSS rho 0.224). The
January 2026 taker-fee change did not merely kill latency bots — it priced the entire
*visible taker alpha pool* into net losses. The probe found signal we cannot capture
**as a taker**, not absence of signal.

(Sensitivity, exclude-markets mode — NOT the gate: top decile net **+$174,377**, CIs
non-overlapping vs control. Edge survives fees in the thinner, non-btc-5m markets. This
is a flagged phenomenon for the next probe, not a build trigger; promoting it to the gate
post-result would violate the amendment standard.)

---

## A3 FINGERPRINT — stated at true confidence

Top-20 W1 wallets, entry latency vs last ≥5bps 1s-mid move:

- **None classify LATENCY** by the registered ≤3s test. Median entry latencies 43-453s;
  %<3s mostly <13%. High-volume persistent winners (n=100k-900k) sit at **mid-odds
  (~0.50), mid-window (wpos ~0.4-0.5)**.
- **True-confidence statement:** the persistent winners are *consistent with a maker
  profile and show no latency signature at 1s resolution.* NOT "they are makers." Trade
  timestamps are 1s-resolution; against "last 5bps move" a fast taker and a slow quoter
  are not distinguishable at this granularity. The maker reading is a hypothesis the data
  fails to *contradict*, not one it confirms.

---

## MAKER SEAT — a measured bound, not a green light

The one seat the fee structure *subsidizes* rather than taxes: be the counterparty
collecting spread, not the taker paying the toll. Kalshi maker rate 0.0175·p(1−p) is a
quarter of the taker toll, and working Kalshi maker rails already exist.

Adverse-selection cost an uninformed maker bleeds to informed flow, from this probe:

| | informed share of taker vol | informed gross edge | **adverse-selection cost** |
|---|---|---|---|
| primary | 20.2% | 148 bps/$ | **~30 bps** |
| exclude-markets | 35.6% | 193 bps/$ | **~69 bps** |

Break-even: spread capture must exceed adverse-selection + maker fee (~44 bps at mid) =
**~74-113 bps**. A 1-2¢ spread at 50¢ ≈ 100-200 bps — **overlaps the break-even band,
does not clear it with room.**

**This is the only candidate that survived the probe, sitting at break-even ± its own
measurement error, on a venue this probe could not observe directly.** Three caveats that
keep it a bound, not a signal:

1. **Favorable and unfavorable terms are correlated in odds-space.** Maker fee and
   adverse-selection both peak at mid (p(1−p)) — exactly where spreads are widest *and*
   informed flow concentrates. You cannot harvest wide mid-spreads without facing peak
   adverse selection. The net at mid is the whole question.
2. **Polymarket-flow-as-proxy-for-Kalshi is a gap, not a footnote.** Kalshi is off-chain,
   regulated, different participant mix (possibly more institutional/informed, possibly
   more retail/uninformed). The 20-36% informed share could move materially either way.
   The bound is measured; its transfer to Kalshi is assumed.
3. **Adverse selection here is a floor.** Computed from *realized winner* edge — it omits
   makers who got picked off and never reached the persistent-winner set. True cost to a
   new maker could exceed 30-69 bps.

**The next step is measurement, not trading.** Whether the seat clears depends on
Kalshi-specific flow composition that only live small-size quoting can establish.

---

## DISPOSITION

- DirectionPredictionSystem prediction fleet → **zero-touch shadow mode.** No new capital,
  no new model work. Predictions + feature logging continue as instrumentation.
- Registered strategies (taker-copy, OFI-direction model) → **closed.**
- Next probe (separate, after this is sealed): **Kalshi maker-viability mini-probe** — own
  one-page pre-registration, gates written before any quoting, strict kill criterion,
  judged on realized spread-capture minus realized adverse-selection at small size within
  the remaining server window.

The probe did its job: a clean answer, on time. Not in the strategies run for four months;
possibly in the one seat not yet tried, pending one more honest measurement.

---

## SALVAGE INVENTORY (the institutional value)

- Train/serve forensics: pipeline proven clean end-to-end (alignment assertion now
  non-tautological; per-prediction `features_json` observability permanent).
- Honest scoring: flat=UP per contract; model-lens vs trading-lens separated.
- Evaluation discipline: temporal-split selection, single-touch test, Wilson/bootstrap
  bounds, pre-registered amendable-only-harder gates, latency-mirage diagnostic.
- "No one beats these markets slowly, post-fees" — measured, with the mechanism
  (0.07·p(1−p) > gross edge) identified.
- 53.4M-trade wallet database + 408-day 100ms-OFI feature store, exported per manifest.
