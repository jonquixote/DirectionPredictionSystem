# v3 Frontend + API Unification Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Complete migration from v2 dashboard+API to v3, unifying all v2 features under v3 surfaces. Production user (https://bet.octavo.press) sees live predictions, trades, model registry, settings, and auth-protected actions — all served by v3 backend, all rendered by the rebuilt React frontend.

**Architecture target (after this plan):**

```
React SPA (/var/www/dps-dash on VPS)
    ↓ /api/kalshi/*       → 127.0.0.1:8080  (paper_trader runtime API, v2-compat for live kalshi state)
    ↓ /api/*              → 127.0.0.1:8081  (v3 dashboard FastAPI — all other routes)
    ↓ /                   → static SPA (React router handles client-side paths)

v3 dashboard (8081) reads from /data/v3.db (SQLite WAL)
v3-paper-trader (systemd) writes predictions/trades to /data/v3.db AND exposes runtime API on 8080
```

**Constraints (hard):**
- **DO NOT touch `v3-paper-trader.service`.** Fleet training is running as separate subprocess but paper_trader handles predictions and Kalshi state — restarting it costs 30-min MAD warmup.
- **DO NOT touch any process under `/data/models/fleet/` or `train_fleet.py`.** Fleet training in flight.
- Schema changes to `/data/v3.db` allowed only via additive `ALTER TABLE ADD COLUMN` — no destructive migrations.
- All endpoint additions go on **v3 backend (8081)** — never the v2 backend (`ofi-lab/dashboard_api`, deprecated post-cutover).
- Anything requiring paper_trader code edits → defer to after fleet finishes (~5h from start).

**Pre-state (verified 2026-05-11):**
- v3-dashboard live on 8081, v3-paper-trader live (kill switch off, MAD warming), nginx routing OK
- React rebuilt with `/models` route + ETHUSDT visibility combos + Models.tsx fetching `/api/models`
- Frontend at https://bet.octavo.press shows: kalshi balance OK; predictions/trades/models/live data **blank or wrong**
- User reports `/api/auth/login` 404 (no such endpoint anywhere)
- v3 dashboard creds: `admin` / `hNHsbNoiYIE7TEdF8lGE9x9CdoTnVNUH` (HTTP Basic via `DASHBOARD_USER`/`DASHBOARD_PASS`)

---

## File Structure

### Investigation outputs (Phase 1)
- `docs/frontend-api-audit-2026-05-11.md` — map of every React fetch + corresponding backend endpoint + data source + response shape

### Frontend (Phase 3)
- `dashboard/src/pages/Models.tsx` — replace stub with rich rendering (symbol, horizon, ewma_ev, psi, lifecycle_state, paper_active, live_eligible, generation, created_at)
- `dashboard/src/pages/Settings.tsx` — add Models nav link; remove broken login form OR convert to HTTP Basic prompt
- `dashboard/src/lib/auth.ts` — switch from Bearer-localStorage to HTTP Basic (or remove client-side state entirely; rely on browser native 401 prompt)
- `dashboard/src/lib/api.ts` — drop `Authorization: Bearer ...` header path; let browser supply Basic credentials
- `dashboard/src/pages/Predictions.tsx` — match v3 response shape
- `dashboard/src/pages/Trades.tsx` — match v3 response shape
- `dashboard/src/lib/visibility.ts` — verify ALL_COMBOS now spans every (model, symbol, duration) the user expects (post-fleet, 85+ entries — handled in deferred phase)

### Backend (Phase 3, v3 only)
- `ofi-lab-v3/dashboard_api/routers/predictions.py` — verify response shape matches what React expects (model_name, symbol, horizon, pred_proba, ts, decision, …)
- `ofi-lab-v3/dashboard_api/routers/trades.py` — same audit
- `ofi-lab-v3/dashboard_api/routers/status.py` — system status (running models, last boundary, kill switch state, kalshi state)
- `ofi-lab-v3/dashboard_api/routers/settings.py` *(new if missing)* — read/write of operator-tweakable knobs (confidence_gate, kelly_fraction, max_bet, suppress_hours, allow_list, visibility persisted server-side)
- New endpoint `GET /api/models/list` already in v3 (confirmed). React renamed call from `/api/models` → `/api/models/list`.

### Nginx (Phase 3)
- `ofi-lab-v3/deploy/nginx/v3-dashboard.conf` — already split (`/api/kalshi*` → 8080, rest → 8081). Verify after audit; add any new path overrides discovered.

### Deferred (Phase 4, after fleet)
- Refactor of paper_trader's runtime API on 8080 (eliminate redundant routes; consolidate to 8081)
- Schema additions if any settings need persistent storage tables
- Migration of `model_registry.json` → SQLite registry consolidation

---

# Phase 1 — Investigation (haiku-driven, no service touch)

## Task U1: Map frontend fetch calls

**Files:** Read-only of `/Users/johnny/Code/DirectionPredictionSystem/dashboard/src/`.

- [ ] **Step 1: Enumerate fetches**

```bash
grep -rohE "fetch\(['\"`][^'\"`)]+|api\.(get|post|patch|delete)\(['\"][^'\"]+" /Users/johnny/Code/DirectionPredictionSystem/dashboard/src 2>/dev/null | sort -u > /tmp/frontend_calls.txt
wc -l /tmp/frontend_calls.txt
cat /tmp/frontend_calls.txt
```

- [ ] **Step 2: For each call, identify caller page + expected response shape**

Grep each endpoint reference back to its caller component. Note what fields the component renders from the response.

- [ ] **Step 3: Write `docs/frontend-api-audit-2026-05-11.md` table**

Columns: `endpoint | method | caller | expected_shape | data_source_needed`

## Task U2: Map v3 backend (8081) endpoints

**Files:** Read-only of `ofi-lab-v3/dashboard_api/`.

- [ ] **Step 1: Enumerate router methods**

```bash
grep -rhnE "@router\.(get|post|patch|delete)\(['\"][^'\"]+['\"]|@app\.(get|post|patch|delete)\(['\"][^'\"]+" /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3/dashboard_api/routers/*.py | sort -u
```

- [ ] **Step 2: For each, note source SQL / file / variable**

Read each handler. Trace to whether it queries `/data/v3.db`, reads a JSONL, hits in-memory state, etc.

- [ ] **Step 3: Smoke each endpoint on prod**

For every GET endpoint:
```bash
curl -fsS -u admin:hNHsbNoiYIE7TEdF8lGE9x9CdoTnVNUH https://bet.octavo.press/api/<path> | python3 -c 'import sys,json; d=json.load(sys.stdin); print(type(d).__name__, list(d.keys()) if isinstance(d,dict) else f"len={len(d)}")'
```
Tabulate which return populated vs empty.

## Task U3: Map paper_trader runtime API (8080)

**Files:** Read-only of `ofi-lab-v3/trading/api_server.py`.

- [ ] **Step 1: List all `app.router.add_*` lines** — already done earlier (kalshi routes + status/predictions/trades). Confirm + extend if anything missed.

- [ ] **Step 2: Source attribution** — for each runtime API endpoint, note whether it reads from in-memory PaperTrader state or from `/data/v3.db`.

- [ ] **Step 3: Determine necessity** — for each runtime endpoint, decide: keep on 8080 (needs live state) OR re-implement on v3 dashboard (reads from DB).

## Task U4: Auth state truth-check

- [ ] **Step 1: Find every reference to `/api/auth/login` in repo**

```bash
grep -rn "auth/login\|Bearer\|DASHBOARD_USER\|DASHBOARD_PASS\|verify_credentials" /Users/johnny/Code/DirectionPredictionSystem/dashboard /Users/johnny/Code/DirectionPredictionSystem/ofi-lab-v3 2>/dev/null | head -50
```

- [ ] **Step 2: Determine actual auth mechanism active on 8081**

```bash
# unauthenticated request
curl -s -o /dev/null -w "%{http_code}\n" https://bet.octavo.press/api/models/list
# authenticated
curl -s -o /dev/null -w "%{http_code}\n" -u admin:hNHsbNoiYIE7TEdF8lGE9x9CdoTnVNUH https://bet.octavo.press/api/models/list
```

Determine whether v3 dashboard rejects unauth or runs in dev-mode-no-auth.

## Task U5: Settings page UX audit

**Files:** Read-only of `dashboard/src/pages/Settings.tsx` (also called Controls).

- [ ] **Step 1: Enumerate every control + its backing endpoint** — kill switch, cutover, visibility, allow-list, suppress-hours, filter-mode, confidence-gate, kelly, bankroll, max-bet, etc.

- [ ] **Step 2: For each, test the endpoint**

```bash
# example
curl -fsS -u admin:... https://bet.octavo.press/api/config 2>&1 | head -c 300
```

- [ ] **Step 3: Note which 404 (need backend additions) vs work but show no data**

## Deliverable for Phase 1

`docs/frontend-api-audit-2026-05-11.md` with two tables:

**Table A — Frontend calls:**
| endpoint | method | caller | expected_shape | actual_response | status |

**Table B — Backend coverage:**
| endpoint | port | source | populated_in_prod | matches_frontend_shape |

Plus a one-line diagnosis per user-visible issue (predictions blank, trades blank, models page blank, settings missing controls, auth broken).

---

# Phase 2 — Decision Matrix (opus, post-investigation)

After Phase 1 returns, opus decides per endpoint:

| Path | Disposition |
|------|-------------|
| `/api/models/list` | Already on 8081. Update React `Models.tsx` to consume rich shape. |
| `/api/predictions` | Audit shape. If 8081 shape matches React → done. Otherwise either patch backend response OR patch React. |
| `/api/trades` | Same as above. |
| `/api/status` | Same. Likely need to extend with current model count, last boundary, kill switch state. |
| `/api/kalshi/*` | Stays on 8080 (paper_trader live state). No change. |
| `/api/config` (if exists) | 8081 read/write for operator knobs. May need new router. |
| `/api/auth/login` | **Remove from React.** Replace with HTTP Basic prompt via browser. |
| `/api/baseline`, `/api/visibility` etc | TBD per investigation. |

Opus also picks auth flow:
- **Option A (default):** HTTP Basic everywhere. Browser handles 401 prompt natively. Remove all login UI + token storage.
- **Option B:** Keep login UI, send Basic creds explicitly on every fetch via `Authorization: Basic btoa(user:pass)`. Slightly more complex but lets us style the prompt.

## Task U6: Decision write-up

- [ ] **Step 1: Append to `docs/frontend-api-audit-2026-05-11.md`** a `## Decisions` section with the disposition table above filled in.

- [ ] **Step 2: Pick auth Option A vs B.** Default A.

---

# Phase 3 — Implementation (haiku batches)

## Task U7: React fetch adjustments

**Files:** `dashboard/src/lib/api.ts`, `dashboard/src/pages/Models.tsx`, `dashboard/src/pages/Predictions.tsx`, `dashboard/src/pages/Trades.tsx`, `dashboard/src/pages/Settings.tsx`.

For each endpoint where shape differs, update the React component's TypeScript interface + render code.

For `Models.tsx`:

```typescript
interface ModelEntry {
  name: string;
  symbol: string;
  horizon: number;  // training_horizon_seconds
  generation: number;
  ewma_ev: number | null;
  ewma_brier: number | null;
  psi: number | null;
  is_baseline: 0 | 1;
  lifecycle_state: string;
  paper_active: 0 | 1;
  live_eligible: 0 | 1;
  created_at: string;
}
```

Fetch URL: `/api/models/list` (response wrapped in `{models: [...]}`).

- [ ] **Step 1: For each affected page, write a failing test** *(if Vitest infra exists in `dashboard/`)*. If not, smoke-render in dev (`npm run dev`) and visually verify.
- [ ] **Step 2: Implement.** Reference Phase 1 audit table.
- [ ] **Step 3: `npm run build`** — must succeed.

## Task U8: Auth simplification (default Option A)

- [ ] **Step 1: Remove login form** from `Settings.tsx` (or whichever component holds it).
- [ ] **Step 2: Remove `auth.ts` Bearer logic.** Strip `Authorization: Bearer ...` from `api.ts`.
- [ ] **Step 3: Verify** browser shows native 401 prompt on first navigation to a protected route.
- [ ] **Step 4: `npm run build`** — must succeed.

## Task U9: Backend endpoint shape patches (v3 only, no paper_trader)

For any endpoint where v3 response doesn't match React's expectation:

- [ ] **Step 1: Patch the v3 router** to return the expected shape. Reuse existing data sources (SQLite queries). Do NOT modify paper_trader runtime.
- [ ] **Step 2: Add new v3 router** only if functionality currently lives only on 8080 and shouldn't require paper_trader restart. Example: a server-side `/api/visibility` config persisted to a new `dashboard_config` SQLite table.

If a feature **requires** paper_trader to participate (e.g., toggle live mode via PaperTrader.kalshi_enabled), defer to Phase 4.

- [ ] **Step 3: Restart v3-dashboard only** (safe, no warmup penalty):

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'sudo systemctl restart v3-dashboard && sleep 4 && sudo systemctl status v3-dashboard --no-pager | head -8'
```

## Task U10: Deploy frontend

- [ ] **Step 1: Build local**

```bash
cd /Users/johnny/Code/DirectionPredictionSystem/dashboard
npm run build
ls -la dist/
```

- [ ] **Step 2: Stage + sudo install on VPS**

```bash
rsync -avz -e "ssh -i ~/.ssh/id_vps_n2" dist/ johnny@34.67.75.48:/home/johnny/dps-dash-new/
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 '
  sudo rsync -av --delete /home/johnny/dps-dash-new/ /var/www/dps-dash/
  sudo chown -R www-data:www-data /var/www/dps-dash
'
```

- [ ] **Step 3: Smoke**

```bash
for p in / /models /audit /settings /api/models/list /api/status /api/predictions /api/trades /api/kalshi/balance /api/kill_switch; do
  code=$(curl -s -o /dev/null -w "%{http_code}" -u admin:hNHsbNoiYIE7TEdF8lGE9x9CdoTnVNUH https://bet.octavo.press$p)
  echo "$code $p"
done
```

All 200. If `/api/predictions` or `/api/trades` returns `[]` despite paper_trader emitting, that's a v3 query bug — fix in Task U9.

## Task U11: Browser e2e

- [ ] **Step 1: Operator visits `https://bet.octavo.press/`** → React loads. Native 401 prompt → enter creds → home screen.
- [ ] **Step 2: Navigate to `/models`** → list shows h300_btc baseline + any fleet models registered so far.
- [ ] **Step 3: Navigate to `/predictions`** → see recent rows (after paper trader warmup; native h300 boundary every 15 min).
- [ ] **Step 4: Navigate to `/trades`** → see paper trades if filter pipeline emitted any.
- [ ] **Step 5: Navigate to `/settings`** → see all expected controls. Toggle a low-risk control (e.g., kalshi disable) → verify backend received it (audit row in `/api/audit`).

---

# Phase 4 — Deferred until fleet training completes

Wait for `/data/models/fleet/state.json` to show `len(completed) == 84`. Estimated ~5h from current state.

## Task U12: Restart v3-paper-trader to pick up registry-driven fleet

After fleet completes, paper_trader needs to reload to score all 85 models via `fleet_loader`. Currently running with hardcoded `h60_btc` + `h300_btc` only.

- [ ] **Step 1: Kill switch engage**

```bash
TOK=$(curl -fsS -u admin:... -X POST -H 'Content-Type: application/json' https://bet.octavo.press/api/admin/confirm_intent -d '{"action":"engage_kill","target":"global","by":"deploy"}' | jq -r .token)
# kill switch engage doesn't need confirmation
curl -fsS -u admin:... -X POST -H 'Content-Type: application/json' https://bet.octavo.press/api/kill_switch -d '{"reason":"fleet_cutover","by":"deploy"}'
```

- [ ] **Step 2: Restart paper_trader**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'sudo systemctl restart v3-paper-trader'
```

- [ ] **Step 3: Verify fleet mode logs**

```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48 'sudo journalctl -u v3-paper-trader --since "1 min ago" --no-pager | grep -E "Fleet mode|Loaded model" | head -90'
```

Expected: "Fleet mode: loading 85 models from registry" + 85 load lines.

- [ ] **Step 4: 30-min MAD warmup, then kill switch resume.**

## Task U13: Consolidate auth implementations

Remove `ofi-lab/dashboard_api/services/auth.py` (v2). v3 keeps the single source of truth.

## Task U14: Optional — server-persisted visibility/settings

Add `dashboard_config` table to v3 schema:

```sql
CREATE TABLE IF NOT EXISTS dashboard_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_by TEXT
);
```

Migrate visibility combo list from localStorage to server-side so all browsers share state.

## Task U15: Final tag

```bash
git tag v3-frontend-unified
git push origin v3-frontend-unified
```

Update `MEMORY.md`.

---

# User-facing questions (answer before Phase 1 dispatch)

1. **Missing settings controls:** user mentioned "many more settings now gone." User question: which specific controls? (Confidence gate + model selector confirmed present; what else?)
2. **Auth preference:** Option A (HTTP Basic, browser prompt, no React login UI) vs Option B (React login form sends Basic explicitly)? Default A unless objection.
3. **Acceptable to drop user-facing `/dashboard` route entirely (already confirmed)?** Yes per user note.
4. **Visibility combos:** post-fleet, 85 models × 3 windows = 255 combo entries. UI should default to baseline + top-N performers; allow operator to toggle. Phase 4 server-persist?

---

## Self-Review

**Spec coverage:**
- Unify v2 → v3 with feature parity → Phases 1-3 ✓
- Don't touch paper_trader during fleet → Constraint stated; Phase 3 limited to v3-dashboard + React only; paper_trader edits all in Phase 4 ✓
- Frontend live data (predictions, trades) → U7 + U9 ✓
- Settings page restore → U7 + U9 ✓
- Auth fix → U8 ✓

**Risks:**
- v3 dashboard response shapes may differ from React's expectations in subtle ways. Mitigation: Phase 1 audit table catches mismatches before code edits.
- Settings page may need new server endpoints v3 doesn't have. Mitigation: add to v3 router (no paper_trader touch).
- Removing login UI changes UX (browser native prompt looks rough). Acceptable per Option A. If rejected, fall back to Option B with explicit Basic header.
- Fleet training subprocess on VPS may compete with v3-dashboard restart during Task U9. Mitigation: dashboard restart is <5s, fleet workers don't block.

**Placeholder scan:** No "TBD" left untriaged — every TBD has a Phase 1 task that resolves it (e.g., missing controls → U5, response shapes → U1).
