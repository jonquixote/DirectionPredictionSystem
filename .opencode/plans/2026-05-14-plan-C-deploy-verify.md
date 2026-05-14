# Plan C: Deploy, Verify, and Integrate

> **Goal:** After Plans A and B are complete, check both agents' work for correctness and compatibility, deploy to VPS, and verify end-to-end that trades flow, metrics populate, and the dashboard shows real data.

**Date:** 2026-05-14
**Executor:** Primary agent (after A+B complete)
**Depends on:** Plan A complete + Plan B complete

---

## Checklist Format

This plan is a sequential checklist — each item must pass before proceeding to the next.

---

## Phase 1: Pre-Deploy Code Review

### 1.1 Verify Plan A Changes

- [ ] `paper_trader.py:1358-1372` — `filter_ctx` now includes `calibrated_p`, `ev`, `utc_hour`, `blackout_hours`, `model_conflict`, `book_age_seconds`, `book_has_quotes`
- [ ] `filters/paper_filter.py:25` — EV gate uses `<` not `<=`
- [ ] `storage/schema.sql` — `filter_config_json` and `platform_active_json` columns added to `model_registry`
- [ ] `storage/schema.sql` — `model_selection` table created
- [ ] `trading/model_selector.py` — exists with `ModelSelector` class and `select()` method
- [ ] `trading/fleet_loader.py` — SELECT includes `filter_config_json`, `platform_active_json`
- [ ] `scripts/register_model.py` — INSERT includes new columns
- [ ] `scripts/seed_model_selection.py` — exists
- [ ] `dashboard_api/routers/models_admin.py` — filter config + model selection API endpoints added
- [ ] `_evaluate_paper_filters()` — reads `confidence_threshold` and `ev_threshold` from `ctx` dict
- [ ] Per-model filter overrides wired in `_run_predictions()` before `filter_ctx` construction
- [ ] `ModelSelector` wired into `_run_predictions()` — blocked models skipped
- [ ] `platform_active_json` wired into `kalshi_dispatch_eligible()`
- [ ] All new tests pass: `cd ofi-lab-v3 && python -m pytest tests/ -x --tb=short`

### 1.2 Verify Plan B Changes

- [ ] `models_admin.py` — detail endpoint JOINs `decay_metrics`, overlap query filtered, `calibration_summary` wired
- [ ] `routers/performance.py` — `/performance/portfolio`, `/performance/threshold-sweep`, `/performance/pareto` endpoints added
- [ ] `services/metrics.py` — `portfolio_metrics()`, `threshold_sweep()`, `pareto_frontier()` functions added
- [ ] `services/sqlite_store.py` — `get_resolved_predictions()` method added
- [ ] `dashboard/src/pages/Models.tsx` — detail card renders inline after clicked row with "View full details →" link
- [ ] `dashboard/src/pages/ModelDetail.tsx` — exists with performance sections, charts, audit trail
- [ ] `dashboard/src/pages/SymbolDetail.tsx` — exists with model comparison table
- [ ] `dashboard/src/App.tsx` — routes for `/models/:name` and `/symbols/:symbol` added
- [ ] `dashboard/src/components/TabBar.tsx` — models tab added
- [ ] `dashboard/src/lib/api.ts` — new API methods added
- [ ] `dashboard/package.json` — recharts + vitest + @testing-library/react added
- [ ] Frontend tests pass: `cd dashboard && npm test`
- [ ] Frontend builds: `cd dashboard && npm run build`

### 1.3 Cross-Plan Compatibility Checks

- [ ] Plan B's API endpoints don't assume `filter_config_json` column exists — graceful fallback if missing
- [ ] Plan B's frontend handles missing `platform_active_json` gracefully (`.?` chaining)
- [ ] Plan A's `model_selection` API endpoints don't conflict with Plan B's routes
- [ ] Both plans' changes to `models_admin.py` are merged correctly (no overwrite conflicts)
- [ ] Schema changes from Plan A don't break Plan B's existing queries

---

## Phase 2: Local Integration Testing

### 2.1 Backend Integration

- [ ] `cd ofi-lab-v3 && python -m pytest tests/ -x --tb=short` — all backend tests pass
- [ ] Manual test: start paper trader with `--fleet` flag, verify filter pipeline doesn't block valid predictions
- [ ] Manual test: call `GET /api/models/list` — all 84 models returned with `ewma_ev`, `ewma_brier`, `psi` (will be NULL until trades flow, but keys must exist)
- [ ] Manual test: call `GET /api/models/{name}` — detail includes decay_metrics fields, filtered overlap, real calibration_summary
- [ ] Manual test: call `GET /api/performance/portfolio` — returns valid structure (empty until trades flow)
- [ ] Manual test: call `GET /api/performance/threshold-sweep` — returns sweep structure
- [ ] Manual test: call `GET /api/model-selection` — returns default rows
- [ ] Manual test: call `PUT /api/model-selection/BTCUSDT/300` with `{"strategy":"best_ev"}` — returns `{"ok":true}`

### 2.2 Frontend Integration

- [ ] `cd dashboard && npm run build` — builds successfully
- [ ] `cd dashboard && npm test` — all frontend tests pass
- [ ] Manual test: navigate to `/models` — list renders, clicking row shows inline detail
- [ ] Manual test: click "View full details →" — navigates to `/models/{name}` page
- [ ] Manual test: navigate to `/symbols/BTCUSDT` — model comparison table renders
- [ ] Manual test: TabBar shows "models" tab

---

## Phase 3: VPS Deployment

### 3.1 Pre-Deploy Snapshot

- [ ] SSH to VPS: `ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48`
- [ ] Record current service status: `systemctl status v3-ws-feed v3-paper-trader v3-dashboard`
- [ ] Record current DB row counts:
  ```bash
  python3 -c "
  import sqlite3
  conn = sqlite3.connect('/data/v3.db')
  for t in ['predictions','paper_trades','decay_metrics','model_overlap','model_selection']:
      try: print(f'{t}: {conn.execute(f\"SELECT COUNT(*) FROM {t}\").fetchone()[0]}')
      except: print(f'{t}: N/A')
  conn.close()
  "
  ```
- [ ] Backup DB: `cp /data/v3.db /data/v3.db.pre_deploy_20260514`

### 3.2 Deploy Backend

- [ ] Rsync backend code (exclude `.venv`):
  ```bash
  rsync -avz --delete --exclude='.venv' --exclude='__pycache__' --exclude='.git' \
    /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3/ \
    johnny@34.67.75.48:/home/johnny/ofi-lab-v3/
  ```
- [ ] Clear pycache on VPS:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "find /home/johnny/ofi-lab-v3/ -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null; \
     find /home/johnny/ofi-lab-v3/ -name '*.pyc' -delete 2>/dev/null"
  ```
- [ ] Run schema migration on VPS:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "cd /home/johnny/ofi-lab-v3 && python3 -c \"
  import sqlite3
  from storage.init_db import init_db
  conn = sqlite3.connect('/data/v3.db')
  init_db(conn)
  conn.commit()
  conn.close()
  print('Schema migration complete')
  \""
  ```
  This will add `filter_config_json`, `platform_active_json` columns and `model_selection` table.
- [ ] Seed model_selection defaults:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "cd /home/johnny/ofi-lab-v3 && python3 scripts/seed_model_selection.py"
  ```
- [ ] Clear stale predictions/trades (from before the fix):
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "python3 -c \"
  import sqlite3
  conn = sqlite3.connect('/data/v3.db')
  conn.execute('DELETE FROM predictions WHERE created_at < '2026-05-14T04:00:00')
  conn.execute('DELETE FROM paper_trades')
  conn.execute('DELETE FROM decision_traces')
  conn.execute('DELETE FROM calibration_outcomes')
  conn.commit()
  conn.close()
  print('Stale data cleared')
  \""
  ```
  Note: Keep predictions from after the fix for verification. Clear paper_trades since no valid trades were generated before the fix.

### 3.3 Deploy Frontend

- [ ] Build frontend locally:
  ```bash
  cd /Users/johnny/Code/DirectionPredictionSystem/dashboard && npm run build
  ```
- [ ] Rsync built frontend to VPS:
  ```bash
  rsync -avz --delete \
    /Users/johnny/Code/DirectionPredictionSystem/dashboard/dist/ \
    johnny@34.67.75.48:/var/www/dps-dash/
  ```

### 3.4 Restart Services

- [ ] Restart paper trader (picks up code changes + schema changes):
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "sudo systemctl restart v3-paper-trader && sleep 5 && systemctl is-active v3-paper-trader"
  ```
- [ ] Restart dashboard API:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "sudo systemctl restart v3-dashboard && sleep 5 && systemctl is-active v3-dashboard"
  ```
- [ ] Verify WS feed still running:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "systemctl is-active v3-ws-feed"
  ```

---

## Phase 4: Post-Deploy Verification

### 4.1 Immediate Checks (within 2 minutes)

- [ ] Paper trader process is running and healthy:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "journalctl -u v3-paper-trader --since '2 minutes ago' --no-pager | tail -20"
  ```
  Look for: "Paper Trader starting", "Models: [...]" (should list 84 models), "Filters: ..."
- [ ] No crash errors in logs
- [ ] Dashboard API responds:
  ```bash
  curl -s http://34.67.75.48:8081/api/models/list | python3 -m json.tool | head -20
  ```
- [ ] Frontend loads: `curl -s -o /dev/null -w "%{http_code}" https://bet.octavo.press/`

### 4.2 Trade Generation Verification (within 10 minutes)

- [ ] Wait for at least 2 contract boundaries (10 minutes)
- [ ] Check that `paper_trades` table now has rows:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "python3 -c \"
  import sqlite3
  conn = sqlite3.connect('/data/v3.db')
  c = conn.cursor()
  c.execute('SELECT COUNT(*) FROM paper_trades')
  print(f'paper_trades: {c.fetchone()[0]}')
  c.execute('SELECT decision_outcome, COUNT(*) FROM paper_trades GROUP BY decision_outcome')
  for row in c.fetchall():
      print(f'  {row[0]}: {row[1]}')
  c.execute('SELECT COUNT(*) FROM paper_trades WHERE decision_outcome=\\\"executed\\\"')
  print(f'executed trades: {c.fetchone()[0]}')
  conn.close()
  \""
  ```
  **Expected:** `executed` count > 0 (the fix should allow high-confidence predictions through)
- [ ] If `executed` count is still 0, check what's blocking:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "python3 -c \"
  import sqlite3
  conn = sqlite3.connect('/data/v3.db')
  c = conn.cursor()
  c.execute('SELECT decision_outcome, decision_reason, COUNT(*) FROM paper_trades GROUP BY decision_outcome, decision_reason ORDER BY COUNT(*) DESC')
  for row in c.fetchall():
      print(f'{row[0]:15s} {row[1] or \"-\":30s} {row[2]}')
  conn.close()
  \""
  ```
  Common reasons: `suppressed` (contract_mismatch, non_15m_boundary — expected for some durations), `below_confidence` (if models aren't confident enough)

### 4.3 Decay Metrics Verification (within 25 minutes)

- [ ] After 4+ boundaries with trades, `decay_metrics` should start populating:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "python3 -c \"
  import sqlite3
  conn = sqlite3.connect('/data/v3.db')
  c = conn.cursor()
  c.execute('SELECT COUNT(*) FROM decay_metrics')
  print(f'decay_metrics: {c.fetchone()[0]}')
  c.execute('SELECT COUNT(DISTINCT model_name) FROM decay_metrics')
  print(f'distinct models with decay: {c.fetchone()[0]}')
  conn.close()
  \""
  ```
  **Expected:** > 0 rows, > 0 distinct models

### 4.4 Dashboard Verification

- [ ] `GET /api/models/list` returns `ewma_ev`, `ewma_brier`, `psi` for models with decay metrics
- [ ] `GET /api/models/{name}` returns decay_metrics + filtered overlap + real calibration_summary
- [ ] Frontend `/models` page shows inline detail card on click
- [ ] Frontend `/models/{name}` page renders performance data
- [ ] Frontend `/symbols/{symbol}` page renders model comparison
- [ ] TabBar shows "models" tab

### 4.5 Model Selection Verification

- [ ] `GET /api/model-selection` returns 12 rows (4 symbols × 3 horizons) with `strategy="all"`
- [ ] Try changing a strategy:
  ```bash
  curl -X PUT http://34.67.75.48:8081/api/model-selection/BTCUSDT/300 \
    -H "Content-Type: application/json" \
    -d '{"strategy":"best_ev"}'
  ```
- [ ] Verify the change persists in DB:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "python3 -c \"
  import sqlite3
  conn = sqlite3.connect('/data/v3.db')
  row = conn.execute('SELECT strategy, selected_model_name FROM model_selection WHERE symbol=? AND market_window_seconds=?', ('BTCUSDT', 300)).fetchone()
  print(f'BTCUSDT:300 strategy={row[0]}, selected={row[1]}')
  conn.close()
  \""
  ```
- [ ] Reset to `"all"` for backward compatibility

### 4.6 Performance Endpoints Verification

- [ ] `GET /api/performance/portfolio` returns valid JSON with `total_trades`, `total_roi_pct`, etc.
- [ ] `GET /api/performance/threshold-sweep` returns sweep array
- [ ] `GET /api/performance/pareto` returns frontier array
- [ ] Frontend threshold sweep chart renders on ModelDetail page

---

## Phase 5: Kalshi/Polymarket Preparation (No Live Trading)

### 5.1 Verify Platform Controls Exist

- [ ] `platform_active_json` column exists in `model_registry` on VPS
- [ ] All 84 models have `platform_active_json` = `'{"paper":true,"kalshi":false,"polymarket":false}'`
- [ ] Frontend ModelDetail page shows platform status section (paper=active, kalshi=prepared, polymarket=prepared)
- [ ] `kalshi_dispatch_eligible()` respects `platform_active_json`

### 5.2 Verify Kalshi Config Present But Disabled

- [ ] `/data/kalshi.env` has `KALSHI_LIVE_ENABLED=true` (existing — leave as-is, the platform_active_json per-model gate provides the actual control)
- [ ] No models have `kalshi:true` in `platform_active_json` — so Kalshi dispatch is blocked at the model level
- [ ] Verify no live Kalshi orders are placed:
  ```bash
  ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
    "journalctl -u v3-paper-trader --since '10 minutes ago' --no-pager | grep -i kalshi | head -5"
  ```
  Should show "kalshi_dispatch blocked" or no Kalshi dispatch lines

### 5.3 To Enable Kalshi Later

When ready to enable Kalshi for specific models:
```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 \
  "python3 -c \"
import sqlite3, json
conn = sqlite3.connect('/data/v3.db')
conn.execute('UPDATE model_registry SET platform_active_json=? WHERE name=?',
    (json.dumps({'paper':True,'kalshi':True,'polymarket':False}), 'h300_btc_179d'))
conn.commit()
conn.close()
\""
# Then restart paper trader to pick up the change
sudo systemctl restart v3-paper-trader
```

---

## Phase 6: Documentation & Wrap-Up

### 6.1 Update Deployment Guide

- [ ] Update `.opencode/plans/vps_deployment_guide.md` with:
  - New schema columns (`filter_config_json`, `platform_active_json`)
  - New `model_selection` table
  - New API endpoints
  - New frontend routes
  - Model selection configuration instructions
  - Platform activation instructions

### 6.2 Create Migration Log

- [ ] Create `docs/2026-05-14-trading-system-upgrade.md` documenting:
  - Paper trader bug fixes (calibrated_p, ev, ev_threshold operator)
  - Per-model filter configuration
  - Committee selection system
  - Platform-level controls
  - New performance analysis endpoints
  - Frontend page additions

### 6.3 Final Verification

- [ ] All VPS services active: `systemctl is-active v3-ws-feed v3-paper-trader v3-dashboard`
- [ ] Paper trades flowing: `paper_trades` table has new rows with `decision_outcome='executed'`
- [ ] Decay metrics populating: `decay_metrics` table has new rows
- [ ] Dashboard shows real data: models list with metrics, model detail pages, symbol pages
- [ ] No Kalshi/Polymarket live orders being placed
- [ ] All 84 models still predicting at every 5-min boundary
