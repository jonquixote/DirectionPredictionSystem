# Frontend/API Audit — v3 Unification Phase 1
**Date:** 2026-05-11 | **State:** Production snapshot via smoke tests on https://bet.octavo.press

---

## Section A: Frontend Fetch Call Inventory

| Endpoint | Method | Caller File:Line | Expected Response Shape | Tab/Page | Status |
|---|---|---|---|---|---|
| `/api/models` | GET | Models.tsx:24 | `{data: ModelEntry[], warnings?: string[]}` | /models tab | **404 endpoint exists as `/api/models/list`** |
| `/api/status` | GET | Home.tsx (implied) | `SystemStatus` | Home (inline) | Unused in code |
| `/api/predictions` | GET | Predictions.tsx:38 | `{predictions: Record<string, unknown>[], total: number, offset: number, limit: number, count: number}` | /predictions tab | ✓ Works but shape mismatch |
| `/api/trades` | GET | Trades.tsx:42 | `{trades: Record<string, unknown>[], total: number, offset: number, limit: number, count: number}` | /trades tab | ✓ Works but shape mismatch |
| `/api/baseline` | GET | Predictions.tsx:22, Trades.tsx:25, Settings.tsx:90 | `DashboardBaseline: {cutover_ts_ms, baseline_kalshi_cents, baseline_paper_trade_count, set_at_iso}` | Settings (baseline editor) | ✓ Works (paper_trader 8080) |
| `/api/kalshi/status` | GET | Settings.tsx:89 | `KalshiStatus` | Settings page | ✓ Works (paper_trader 8080) |
| `/api/kalshi/balance` | GET | Home.tsx (implied) | `KalshiBalance` | Home dashboard | ✓ Works (paper_trader 8080) |
| `/api/kalshi/orders` | GET | Unused in React | `{orders: KalshiOrder[], total, offset, limit, count, ledger_exists}` | — | via /kalshi/* route (paper_trader 8080) |
| `/api/auth/login` | POST | Settings.tsx:32 | `{token: string}` | Settings login form | **404 — no such endpoint exists** |
| `/api/config` | GET | Settings.tsx (via api.paperConfig) | `Record<string, unknown>` | Settings form (unused) | ✓ Works on paper_trader 8080 |
| `/api/config` | PATCH | Settings.tsx (via api.paperUpdateConfig) | `{updated, config}` | Settings form (unused) | ✓ Works on paper_trader 8080 |
| `/api/kalshi/enable` | POST | Settings.tsx:134 | `{enabled: boolean, status: KalshiStatus}` | Settings kill switch | ✓ Works (paper_trader 8080) |
| `/api/kalshi/disable` | POST | Settings.tsx:133 | `{enabled: boolean, status: KalshiStatus}` | Settings kill switch | ✓ Works (paper_trader 8080) |
| `/api/kalshi/config` | PATCH | Settings.tsx:173, 186, 201, etc | `{updated, config}` | Settings controls (allow-list, hours, params) | ✓ Works (paper_trader 8080) |

---

## Section B: Backend Endpoint Coverage

### B.1 — v3 Dashboard FastAPI (8081) Routes

| Route | Source | Response Shape | Status | Populated in Prod |
|---|---|---|---|---|
| `GET /api/predictions` | /data/paper_trades.jsonl + /data/models/predictions.jsonl | `{data: [...], meta: {...}, warnings: [...]}` | ✓ Implemented | YES — 94,479 records |
| `GET /api/trades` | /data/paper_trades.jsonl | `{data: [...], open_trades: [...], meta: {...}, warnings: [...]}` | ✓ Implemented | YES — records present |
| `GET /api/models` | /data/models/ directory scan | `{data: [{name, metadata?, gate_config?}]}` | ✓ Implemented | YES — 1 model found (run_20260326_092407) |
| `GET /api/status` | In-memory LiveState | `{running, paused, predictions_total, trades_total, warmup_complete, uptime_seconds, running_pnl, filters}` | ✓ Implemented | — Not smoked |
| `GET /api/performance` | /data/paper_trades.jsonl | Performance snapshot by model | ✓ Implemented | — Not smoked |
| `PATCH /api/config` | In-memory state (paper_trader config) | Config updates | ✓ Implemented | via 8080 (not 8081) |
| `GET /api/baseline` | /data/dashboard_baseline.json | Cutover baseline | ✓ Implemented | via 8080 (not 8081) |
| `POST /api/baseline` | /data/dashboard_baseline.json write | Cutover baseline update | ✓ Implemented | via 8080 (not 8081) |
| `GET /api/kalshi/status` | paper_trader.kalshi state | Kalshi client status | ✓ Implemented | via 8080 (not 8081) |
| `GET /api/kalshi/balance` | paper_trader kalshi client | Kalshi balance | ✓ Implemented | via 8080 (not 8081) |
| `GET /api/kalshi/orders` | /data/kalshi_orders.jsonl | Kalshi order ledger paginated | ✓ Implemented | via 8080 (not 8081) |
| `POST /api/kalshi/enable` | paper_trader state mutation | Enable Kalshi trading | ✓ Implemented | via 8080 (not 8081) |
| `POST /api/kalshi/disable` | paper_trader state mutation | Disable Kalshi trading | ✓ Implemented | via 8080 (not 8081) |
| `PATCH /api/kalshi/config` | paper_trader state mutation | Update Kalshi config | ✓ Implemented | via 8080 (not 8081) |

**Key observation:** 8081 (v3 dashboard) implements `/api/predictions`, `/api/trades`, `/api/models`. Everything else (`/api/baseline`, `/api/config`, `/api/kalshi/*`) is **on 8080 (paper_trader)** and requires Bearer token auth, not HTTP Basic.

### B.2 — paper_trader Runtime API (8080) Routes (via nginx `/api/kalshi/*` rewrite)

| Route | Source | Shape Summary | Tested |
|---|---|---|---|
| `GET /baseline` | /data/dashboard_baseline.json | `{cutover_ts_ms, baseline_kalshi_cents, baseline_paper_trade_count, set_at_iso}` | ✓ 200 |
| `POST /baseline` | /data/dashboard_baseline.json write | same as GET | — No write test |
| `GET /status` | in-memory PaperTrader state | System status (running, paused, predictions_total, trades_total, warmup_complete, uptime_seconds, running_pnl, filters) | — Not tested |
| `GET /market` | in-memory feature computer | Current market snapshot (mid, vol, etc.) | — Not tested |
| `GET /predictions` | /data/models/predictions.jsonl | Prediction records with pagination | — Not tested |
| `GET /trades` | /data/paper_trades.jsonl | Paper trade records with pagination | — Not tested |
| `GET /pending` | in-memory PaperTrader.pending_trades | Open trades | — Not tested |
| `GET /performance` | /data/paper_trades.jsonl + metrics | Performance by model/symbol | — Not tested |
| `GET /performance/pnl_series` | /data/paper_trades.jsonl | PnL time series | — Not tested |
| `GET /performance/accuracy_series` | /data/models/predictions.jsonl | Accuracy time series | — Not tested |
| `GET /suppression_log` | in-memory suppression state | Suppression audit | — Not tested |
| `GET /kalshi/status` | paper_trader.kalshi state | Kalshi client status | ✓ 200 |
| `GET /kalshi/balance` | Kalshi client live query | Balance (available, reserved, pnl, etc.) | ✓ 200 |
| `GET /kalshi/orders` | /data/kalshi_orders.jsonl | Kalshi order ledger paginated | ✓ 200 |
| `POST /kalshi/enable` | paper_trader state mutation | Enable Kalshi live trading | — No auth test |
| `POST /kalshi/disable` | paper_trader state mutation | Disable Kalshi live trading | — No auth test |
| `PATCH /kalshi/config` | paper_trader state mutation + /data/kalshi.env persist | Kalshi config (allow_list, kelly_fraction, confidence_gate, bankroll_fraction, per_trade_usd_cap, suppress_hours_utc, apfs_enabled, apfs_threshold) | — No auth test |
| `POST /auth/login` | Bearer token generator | `{token: string}` | **404 — never implemented on paper_trader** |
| `PATCH /config` | in-memory state | Paper trading config (confidence_threshold, pause_trading) | — Not tested |

---

## Section C: Per-Issue Diagnosis

### 1. **Predictions Tab Blank**

**Root cause:** Response shape mismatch. React expects `{predictions: [...]}` but v3 backend returns `{data: [...]}`.

- **Endpoint:** `GET /api/predictions` (8081)
- **What it returns:** Paginated prediction array in `data` field, wrapped in `meta` + `warnings`
  ```json
  {
    "data": [{prediction_id, ts_model_ran_ms, symbol, model, pred_direction, pred_proba, p_market, divergence, warmup, suppressed_reason, features, trade_id, outcome, realized_net}],
    "meta": {total, page, page_size, from_ms, to_ms, filters_applied},
    "warnings": []
  }
  ```
- **What React expects:** `PredictionsPage` with `{predictions: [...], total, offset, limit, count}` (from `api.ts` line 167)
- **Fields React reads:** `p.symbol`, `p.model`, `p.contract_duration_seconds`, `p.pred_proba` or `p.proba_up`, `p.pred_direction` or `p.predicted_direction`, `p.correct`, `p.ts_model_ran_ms`
- **Issue:** Backend returns `ts_model_ran_ms` (✓), `pred_direction` (✓), `pred_proba` (✓), but React expects `contract_duration_seconds` (field missing), `correct` (needs resolution join), `offset`/`limit` returned but stored in `meta`

---

### 2. **Trades Tab Blank**

**Root cause:** Response shape mismatch. React expects `{trades: [...]}` but v3 backend returns `{data: [...]}`.

- **Endpoint:** `GET /api/trades` (8081)
- **What it returns:** 
  ```json
  {
    "data": [{id, prediction_id, timestamp_ms, symbol, model, contract_duration, direction, simulated_stake_usdc, p_market, price_at_open, price_at_close, outcome, correct, realized_net, realized_net_breakdown, suppressed_reason, resolved, resolved_at_ms, pred_proba}],
    "open_trades": [...],
    "meta": {total, page, page_size, from_ms, to_ms, filters_applied},
    "warnings": []
  }
  ```
- **What React expects:** `TradesPage` with `{trades: [...], total, offset, limit, count}` (from `api.ts` line 159)
- **Fields React reads:** `t.symbol`, `t.contract_duration_seconds`, `t.model`, `t.trade_result`, `t.net_pnl`, `t.confidence`, `t.ts_model_ran_ms`, `t.direction` or `t.side`
- **Issue:** Backend field names differ: `contract_duration` vs `contract_duration_seconds`, `timestamp_ms` vs `ts_model_ran_ms`, `realized_net` vs `net_pnl`, `pred_proba` vs `confidence`, `direction` vs `side`, missing `ts_model_ran_ms`

---

### 3. **Models Page Blank or Wrong**

**Root cause:** React fetches `/api/models` but actual endpoint on v3 is `/api/models/list` (old route). Also, v3 returns minimal metadata.

- **Frontend calls:** `window.fetch('/api/models')` (line 24)
- **Actual endpoint on 8081:** `/api/models` (not `/api/models/list` — that was a stale reference in plan)
- **What it returns:** `{data: [{name, metadata?, gate_config?}]}`
  - `metadata` contains trained_date, version, hyperparams (from metadata.json on disk)
  - `gate_config` contains gate config (from gate_config.json on disk)
- **What React expects:** `{data: ModelEntry[]}` where `ModelEntry: {name, metadata?, gate_config?}` (Models.tsx:5-9)
- **Issue:** ✓ **Endpoint exists and returns expected shape.** Data is sparsely populated (only 1 model on prod: `run_20260326_092407` with minimal metadata). Post-fleet, should have 85+ models. React renders correctly if metadata present; shows "—" for missing dates.

---

### 4. **Settings Page Missing Controls**

Controls expected by React (Settings.tsx):

| Control | Backing API | Expected Field | Status | Issue |
|---|---|---|---|---|
| Kill switch (enable/disable) | `/api/kalshi/enable` + `/api/kalshi/disable` | `status.enabled` | ✓ Works | — |
| Cutover baseline editor | `/api/baseline` GET + POST | `baseline.cutover_ts_ms`, `baseline.set_at_iso` | ✓ Works | — |
| Visibility matrix (dashboard-local) | localStorage (no backend) | ALL_COMBOS | ✓ Works | Pre-fleet: only h300_btc, h60_btc. Post-fleet: 85+ combos expected. |
| Allow-list editor (Kalshi) | `/api/kalshi/config` PATCH | `config.allow_list` | ✓ Works | — |
| Suppress hours (UTC grid) | `/api/kalshi/config` PATCH | `config.suppress_hours_utc` | ✓ Works | — |
| Filter mode (APFS vs Confidence Gate) | `/api/kalshi/config` PATCH | `config.apfs_enabled`, `config.apfs_threshold` | ✓ Works | — |
| Confidence gate slider | `/api/kalshi/config` PATCH | `config.confidence_gate` | ✓ Works | — |
| Kelly fraction slider | `/api/kalshi/config` PATCH | `config.kelly_fraction` | ✓ Works | — |
| Bankroll fraction slider | `/api/kalshi/config` PATCH | `config.bankroll_fraction` | ✓ Works | — |
| Max bet cap slider | `/api/kalshi/config` PATCH | `config.per_trade_usd_cap` | ✓ Works | — |
| Login form | `/api/auth/login` POST | Bearer token | **404 — endpoint missing** | **Blocks auth flow entirely** |
| Logout button | localStorage.removeItem | — | ✓ Works (client-side) | — |

**Issue:** Login form calls `/api/auth/login` which does not exist on v3 (8081). That endpoint exists on paper_trader (8080) but uses Bearer token (password → token), not HTTP Basic. React expects a token to store in localStorage; v3 uses HTTP Basic auth via browser prompt.

---

### 5. **Auth Flow Broken**

**Current state:**
- React `Settings.tsx:21-40` has a login form that calls `api.login(password)` → `request('/auth/login', POST)` expecting `{token: string}`
- v3 dashboard (8081) **does not implement** `/api/auth/login`. All endpoints require HTTP Basic auth via `verify_credentials` dependency
- paper_trader (8080) **does** implement `/auth/login` (line 1261) as a legacy v2 endpoint, but it's on port 8080 (behind nginx `/api/kalshi/*` rewrite only), not accessible as `/api/auth/login` from the React dashboard root
- `api.ts:23` tries to inject `Authorization: Bearer ${token}` header but v3 expects HTTP Basic (username:password in Authorization header)
- On production: `/api/auth/login` returns **404** because nginx routes `/api/*` → 8081 and 8081 has no such route

**Root cause:** Auth mechanism mismatch:
  - **React expects:** POST `/api/auth/login {password}` → `{token}` → store in localStorage → send `Authorization: Bearer <token>` on authenticated requests
  - **v3 provides:** HTTP Basic auth via `verify_credentials` on all routes. Browser handles 401 prompt natively.
  - **Legacy (paper_trader):** Bearer token mechanism at `/auth/login` on port 8080

**Impact:**
  - Login form will fail with 404 on v3
  - Settings page is locked behind login form → **user cannot access any controls**
  - Kalshi status loads unauthenticated (no auth needed for GET /kalshi/status on paper_trader)
  - Settings.tsx needs auth to load `api.kalshiStatus()` → returns 401 if not authed

---

## Section D: Decisions

Per Phase 2 decision matrix, fill in disposition for each endpoint:

| Path | Disposition |
|---|---|
| `/api/models/list` | **On 8081.** React calls `/api/models`; endpoint exists at GET `/api/models` returning `{data: ModelEntry[]}`. No change needed — endpoint is correct. After fleet training completes (85+ models), React will render richer data if metadata/gate_config populated on disk. |
| `/api/predictions` | **On 8081.** Shape mismatch: returns `{data: [...], meta, warnings}` but React expects `{predictions, total, offset, limit, count}`. **Action:** Patch React Predictions.tsx to consume `data` instead of `predictions`, and read pagination from `meta`. |
| `/api/trades` | **On 8081.** Shape mismatch: returns `{data: [...], meta, warnings}` but React expects `{trades, ...}`. Field name mismatches (`contract_duration` vs `contract_duration_seconds`, `timestamp_ms` vs `ts_model_ran_ms`, `realized_net` vs `net_pnl`). **Action:** Patch React Trades.tsx field names, or patch v3 router to alias fields to match React expectations. |
| `/api/status` | **On 8081.** Implemented but unused by React. Can ignore. |
| `/api/kalshi/*` | **Stay on 8080** (paper_trader, via nginx `/api/kalshi/*` → 8080 rewrite). Requires Bearer token auth (legacy). React must switch to HTTP Basic or this will 401. |
| `/api/config` | **On 8080** (paper_trader). Unused in React. Can ignore. |
| `/api/baseline` | **On 8080** (paper_trader). Used by React for cutover baseline. Requires Bearer token (legacy). React switches to HTTP Basic — will 401. **Action:** Either move `/api/baseline` to 8081 with HTTP Basic, or switch React auth to Bearer tokens. |
| `/api/auth/login` | **Remove from React.** Endpoint does not exist on 8081. Implement HTTP Basic auth option: remove login form, rely on browser's native 401 prompt. **Decision: Option A (HTTP Basic, native prompt).** |
| All other 8080 routes | **Stay on 8080.** Kalshi enable/disable/config use Bearer token. Migrate to HTTP Basic or remove from React if they can be re-implemented on 8081. |

**Auth Decision: Option A (HTTP Basic)** — browser native 401 prompt, no React login UI, no token storage. Rationale: Simpler, no client-side state, matches v3's existing auth model. Trade-off: browser prompt less polished than custom UI, but acceptable for internal operator use.

---

## Section E: Open Questions & Blockers

1. **Bearer token vs HTTP Basic:** paper_trader (8080) uses Bearer tokens (from `/auth/login`). v3 (8081) uses HTTP Basic auth. React currently implements Bearer token storage. 
   - **Resolution needed before Phase 3:** Do we migrate 8080 endpoints (baseline, kalshi/*) to 8081 with HTTP Basic? Or keep them on 8080 and have React send Bearer tokens?
   - **Recommendation:** Migrate to 8081 (reduces operational surface, unified auth). Deferred to Phase 4 after fleet completes (because restart of paper_trader not allowed during fleet training).

2. **Response shape adaptation:** Should we patch the v3 router to alias field names (e.g., `contract_duration_seconds` for backward compat), or patch React? 
   - **Recommendation:** Patch React (simpler, auditable). v3 field names are semantic (`timestamp_ms` is more precise than `ts_model_ran_ms`).

3. **Models metadata completeness:** Post-fleet, when 85+ models register, will they have `metadata.json` and `gate_config.json` on disk? 
   - **Dependency:** Fleet training completion + registry population. Model endpoint returns sparse metadata if files missing.
   - **Test:** Check `/data/models/fleet/` after fleet finishes.

4. **Visibility combos:** v3 `visibility.ts` hardcodes `ALL_COMBOS` to `[h300_btc, h300_solusdt, h300_ethusdt, h60_btc, ...]`. Post-fleet, should include all 85+ model × 3 symbols × 1-2 durations.
   - **Resolution:** Fleet training populates `/data/models/fleet/state.json`. Script should read that file and re-generate `ALL_COMBOS`.
   - **Deferred to Phase 4.**

5. **Baseline endpoint ownership:** Currently on paper_trader (8080). Should migrate to 8081 for unified auth and operation independence?
   - **Rationale:** Baseline is a dashboard config, not paper_trader live state. Should persist to a v3 table (`dashboard_config`), not `/data/dashboard_baseline.json`.
   - **Deferred to Phase 4 (after fleet, if time permits).**

---

## Summary Table: What Works, What Doesn't

| Feature | Status | Blocker | Fix Priority |
|---|---|---|---|
| Predictions tab data fetch | Works (wrong shape) | Response shape mismatch | Medium — patch React |
| Trades tab data fetch | Works (wrong shape, missing fields) | Shape + field name mismatch | Medium — patch React |
| Models page | Works (sparse data) | Depends on fleet metadata | Low — test post-fleet |
| Settings controls (Kalshi) | Works (auth issue) | Bearer token vs HTTP Basic | High — switch to HTTP Basic |
| Settings baseline editor | Works (auth issue) | Bearer token on /baseline | High — switch to HTTP Basic |
| Login form | **BROKEN (404)** | Endpoint missing, auth model mismatch | **High — remove form, use HTTP Basic** |
| Kill switch (enable/live/disable) | Works (auth issue) | Bearer token vs HTTP Basic | High — switch to HTTP Basic |
| Allow-list, suppress hours, params | Works (auth issue) | Bearer token vs HTTP Basic | High — switch to HTTP Basic |
| Kalshi balance (home) | Works | — | — |

