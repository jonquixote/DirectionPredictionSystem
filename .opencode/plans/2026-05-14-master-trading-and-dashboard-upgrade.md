# Master Plan: Trading System + Dashboard Upgrade

> **Goal:** Fix the paper trader to generate trades from 84 fleet models, upgrade the filtering architecture to per-model control with committee selection, fix and extend the frontend model pages, and build performance analysis tools.

**Date:** 2026-05-14

---

## Current State

- **84 models predicting** at every 5-min boundary — 6720 predictions in ~4.3 hrs
- **0 paper trades** — the paper filter pipeline has a key mismatch bug that blocks 100% of predictions
- **0 decay_metrics** — no trades = no decay snapshots (chicken-and-egg)
- **Overall accuracy ~48.8%** — BTC 45.6%, ETH 52.2%, SOL 48.3%, XRP 39.3%
- Frontend model detail card renders above list (not inline), with missing data
- No per-model filter config, no committee selection, no threshold optimization tools

## Root Cause: Paper Trader Blockage

Two bugs in the paper filter pipeline prevent any trades:

1. **`calibrated_p` key mismatch** — `paper_filter.py` reads `ctx.get("calibrated_p", 0.0)` but `filter_ctx` provides `"pred_proba_calibrated"`. Default `0.0` is always below threshold → 100% block.
2. **`ev` key missing** — `ctx.get("ev", 0.0)` never set in `filter_ctx`. With `ev_threshold=0.0`, the check `0.0 <= 0.0` blocks everything.

Secondary issues:
- `utc_hour` and `blackout_hours` not wired in `filter_ctx` → blackout gate never fires
- `model_conflict` not wired in `filter_ctx` → conflict gate never fires
- `ev_threshold` defaults to `0.0` with `<=` operator → zero-EV predictions blocked

---

## Three Sub-Plans

| Plan | File | Executor | Scope |
|------|------|----------|-------|
| A | `plan-A-trader-filter-backend.md` | This agent | Paper trader bug fixes + filtering architecture (Steps 1-6) |
| B | `plan-B-api-frontend.md` | Separate coder | API fixes + frontend pages + performance endpoints (Steps 7-14) |
| C | `plan-C-deploy-verify.md` | After A+B complete | Integration verification + deployment + smoke testing |

## Execution Order

```
Plan A ──────────────────────────┐
                                  ├──→ Plan C (deploy + verify)
Plan B ──────────────────────────┘
```

- Plans A and B execute in **parallel**
- Plan C starts only after **both** A and B are complete
- Plan C checks both agents' work, deploys, and validates end-to-end

## Key Constraints

- **No Kalshi/Polymarket live trading yet** — platforms must be prepared but not enabled
- All 84 models remain `paper_active=1` — no model should be disabled by default
- ETHUSDT stays excluded from `TRADE_SYMBOLS` (predictions only)
- VPS: `34.67.75.48`, SSH key `~/.ssh/id_vps_n2`, user `johnny`
- VPS services: `v3-ws-feed`, `v3-paper-trader` (port 8080), `v3-dashboard` (port 8081)
- Backend: `/home/johnny/ofi-lab-v3/`, DB: `/data/v3.db`, env: `/etc/v3/env` + `/data/kalshi.env`
- Frontend: `/var/www/dps-dash/` via nginx (bet.octavo.press)
- Must clear `__pycache__/*.pyc` after rsync; `.venv` excluded from rsync
- Test framework: pytest >=7.4, `tests/` directory, `conftest.py` with `tiny_model_path` + `synthetic_minute_bars_path`
- No CI — all testing is manual `pytest`

## Files Modified Across All Plans

### Plan A (trader + filters)
- `ofi-lab-v3/trading/paper_trader.py` — fix `filter_ctx`, add EV computation, wire `model_conflict`
- `ofi-lab-v3/filters/paper_filter.py` — fix EV gate `<=` → `<`
- `ofi-lab-v3/storage/schema.sql` — add `filter_config_json`, `model_selection` table, `platform_active_json`
- `ofi-lab-v3/trading/fleet_loader.py` — read `filter_config_json` from registry
- `ofi-lab-v3/scripts/register_model.py` — add `filter_config_json` to INSERT
- `ofi-lab-v3/storage/registry_state.py` — add `set_filter_config()` method
- `ofi-lab-v3/dashboard_api/routers/models_admin.py` — add filter config endpoints
- `ofi-lab-v3/trading/paper_trader.py` — per-model filter override logic + committee selection
- `tests/test_paper_filter_wiring.py` — expand with `calibrated_p` key test
- `tests/test_filter_pipeline.py` — add EV gate boundary test
- `tests/test_filter_pipeline_integration.py` — expand with real `filter_ctx` keys
- `tests/test_model_selection.py` — NEW: committee selection tests
- `tests/test_fleet_loader.py` — add `filter_config_json` column test
- `tests/test_register_model.py` — add `filter_config_json` column test

### Plan B (API + frontend)
- `ofi-lab-v3/dashboard_api/routers/models_admin.py` — fix overlap query, add `decay_metrics` JOIN, wire `calibration_summary`
- `ofi-lab-v3/dashboard_api/routers/performance.py` — add threshold-sweep, portfolio, pareto endpoints
- `ofi-lab-v3/dashboard_api/services/metrics.py` — add portfolio metrics, threshold sweep, bootstrap ROI CI
- `dashboard/src/pages/Models.tsx` — inline detail card, add full-page link
- `dashboard/src/pages/ModelDetail.tsx` — NEW: per-model detail page
- `dashboard/src/pages/SymbolDetail.tsx` — NEW: per-symbol page
- `dashboard/src/App.tsx` — add `/models/:name` and `/symbols/:symbol` routes
- `dashboard/src/lib/api.ts` — add new API methods
- `dashboard/package.json` — add chart library (recharts)
- `dashboard/src/components/TabBar.tsx` — add models tab
- Tests for new API endpoints + new frontend components

### Plan C (deploy + verify)
- No new code files — only deployment scripts and verification commands
