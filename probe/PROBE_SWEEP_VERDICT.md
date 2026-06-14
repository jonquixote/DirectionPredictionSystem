# PROBE SWEEP — MASTER VERDICT

**Sealed:** 2026-06-14. **Question:** is there a capturable edge in 5-30min crypto up/down
prediction markets (Polymarket/Kalshi, BTC/ETH/SOL/XRP) reachable by a solo operator from
public data?

**Answer: NO. Six structurally-different mechanisms tested, six kills, on instruments that
proved they could fail.** The convergence is the finding.

## The six kills

| # | Mechanism | How it died | Verdict doc |
|---|-----------|-------------|-------------|
| 1 | Price-state (OBI/"MLOFI") | ~zero signal at these horizons; offline features reproduce live sub-50 (49.4 vs 49.7%). | Phase-1 forensics |
| 2 | Price-flow (true Cont-2014 OFI) | Gate B: fresh-slice EV CI crosses zero (50.84%, LB 50.24%); pure-OFI window-open slice CI-negative. | `VERDICT.md` |
| 3 | Copy-the-winners (taker) | Gross skill persists (rho 0.31, p≈10⁻⁷⁹) but 0.07·p(1−p) fee confiscates 100%+; cohort CIs overlap control. | `VERDICT.md` |
| 4 | Maker seat (Kalshi) | §0 volume wall: 4 markets, 384 windows/day, deep queues → $300/day bar structurally unreachable. | `KALSHI_MAKER_VERDICT.md` |
| 5 | Fair-value deviation | 92% supra-cost deviation but symmetric/unbiased/uniform + partly own model error → unharvestable. Concentration control killed it. | `FAIRVALUE_VERDICT.md` |
| 6 | Intra-window inversion (round-trip) | Oscillation real (85% round-trips complete) but incomplete legs = adverse selection; EV −$0.064/window, LB −$0.065, n=63k, on the best slice. | `INVERSION_VERDICT.md` |

## The unifying finding
In these markets the price is **efficient against everything a solo operator can compute from
public data**, and the **fee structure (0.07·p(1−p) taker) confiscates whatever visible skill
persists**. The January 2026 taker-fee change priced out the visible alpha pool — gross skill
is real and persistent, but net-negative after fees. The maker seat (the one fee-subsidized
counterparty role) is structurally too thin in addressable volume to matter. These are not six
attempts that happened to fail; they are six independent doors into the same room, all opening
onto the same wall.

## Disposition (decision-table KILL row)
- **System → zero-touch shadow.** No new capital, no new model work. The v3 fleet keeps
  predicting + logging `features_json` purely as instrumentation.
- **Owner reallocates active time to Branch B.**
- **Salvage value (the real deliverable):**
  - Methodology: pre-registered amendable-only-harder gates, controls-always, single-touch
    test, latency-mirage diagnostic, anchored scale-up bars, evidence-verbatim. The fair-value
    probe's 92%-false-positive strangled by four controls is the canonical demonstration.
  - Datasets: 53.4M-trade Polymarket wallet DB; 408-day 100ms Cont-2014 OFI feature store;
    per-prediction feature log. Compressed on VPS (`probe_track_a.db.gz` 8.4G,
    `probe_ofi.tar.gz` 5.2G), `feature_log.parquet` pulled local.
  - Proven-clean serving pipeline + permanent feature observability (Phase 1).
  - The "no one beats these markets slowly, post-fees" result, with the mechanism identified.

## What would change the answer (not pursued now)
- A latency seat (sub-second infra) — explicitly out of scope (can't be reached at human
  timescale; the fee change was designed to tax exactly this).
- Kalshi-direct measurement (this used Polymarket flow as proxy) — recommend standing up
  Kalshi book logging if ever revisited; would convert the maker/fair-value proxy bounds into
  venue-measured numbers.
- Markets with structurally more addressable volume / wider spreads than 4×15-min crypto.

## Export manifest (Day-10, execute on/before server end ~2026-06-30)
- [x] `feature_log.parquet` → local repo (`probe/`, gitignored, off-server copy).
- [x] `probe_track_a.db.gz` (8.4G), `probe_ofi.tar.gz` (5.2G) → compressed on VPS
      `/data/probe_exports/`. TODO: pull to cold storage before server end.
- [x] All registrations + verdicts + scripts → git (branches `probe/*`).
- [x] Handoff: `docs/2026-06-13-signal-hunt-handoff.md`.
- [ ] Pull the two compressed DBs to local/cold storage (non-urgent, before 2026-06-30).
- [ ] Snapshot `vps-n2-pre-reset-20260613` — retain or delete per owner.

**The probe did exactly what it was built to do: returned a clean, defensible, evidence-backed
NO, on time, with instruments watched proving they could fail. Most operators of these systems
never get a clean answer — they bleed slowly and quit confused. This one has the answer.**
