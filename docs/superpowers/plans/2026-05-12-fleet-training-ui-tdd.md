# Fleet Training UI — TDD Execution Plan (Phases 2 & 3)

> **Goal:** Build a FastAPI training dispatch router + React "Fleet Training" page accessible from the Settings navigation menu.

**Date:** 2026-05-12  
**Status:** ✅ ALL STEPS COMPLETE

---

## Architecture

### Backend: `dashboard_api/routers/training.py`
- `POST /api/training/dispatch` — Accept training config, launch `train_fleet.py` in background
- `GET /api/training/status` — Return current job status + last 50 lines of log
- `GET /api/training/data-status` — Return per-symbol feature data date ranges

### Frontend: `dashboard/src/pages/FleetTraining.tsx`
- Accessible via Settings page menu (alongside Models link)
- Checkboxes for symbols, horizons, training windows
- Walk-forward toggle (defaults off)
- Custom training window input (arbitrary days)
- Train-end date picker
- Dispatch button + live log viewer

---

## Test Results

### Test 1: `test_status_returns_idle_when_no_job` ✅ PASS
### Test 2: `test_dispatch_creates_background_job` ✅ PASS
### Test 3: `test_dispatch_rejects_empty_symbols` ✅ PASS
### Test 4: `test_dispatch_rejects_unknown_symbol` ✅ PASS
### Test 5: `test_data_status_returns_date_ranges` ✅ PASS

```
tests/test_training_dispatch.py   5 passed in 0.75s
```

---

## CLI Verification

### Custom Training Window (46 days, 900s horizon):
```
$ python -m validation.retrain --dry-run --horizon 900 --symbol BTCUSDT \
    --feature-version v3 --train-days 46 --train-end 2026-04-28 ...
train: 2026-03-14 → 2026-04-28
auto_name=900s_btcusdt_v3_20260428
```

### Walk-Forward Toggle:
```
$ python -m scripts.train_fleet --walk-forward --symbols BTCUSDT --horizons 900 \
    --train-days 46,90 --train-end 2026-04-28
```

### Fleet Enumeration:
```
h900_btc_v3_46d, h900_btc_v3_90d  (2 models for 1 symbol × 1 horizon × 2 windows)
```

---

## Files Changed

| File | Type | Description |
|------|------|-------------|
| `dashboard_api/routers/training.py` | NEW | FastAPI router with dispatch/status/data-status endpoints |
| `dashboard_api/main.py` | MODIFIED | Registered training router |
| `dashboard/src/pages/FleetTraining.tsx` | NEW | React fleet training command center page |
| `dashboard/src/App.tsx` | MODIFIED | Added /fleet-training route |
| `dashboard/src/pages/Settings.tsx` | MODIFIED | Added training link to settings nav menu |
| `tests/test_training_dispatch.py` | NEW | 5 TDD tests for training router |
| `deploy/cron/v3-daily-features.sh` | NEW | Daily cron script for feature pipeline |

---

## Data Pipeline Status

- **Orderbook download:** ✅ Complete (13 days × 4 symbols = 52 files)
- **Feature v1 → v2 → v3 pipeline:** 🔄 Running in background on VPS
- **Daily cron job:** ✅ Installed (00:30 UTC daily)
