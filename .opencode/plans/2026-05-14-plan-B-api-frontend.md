# Plan B: API & Frontend (TDD)

> **Goal:** Fix the model detail endpoint data gaps, add model detail card inline positioning with full-page link, create per-model and per-symbol detail pages, add performance analysis endpoints (threshold sweep, portfolio metrics, Pareto frontier), and build optimization UI components.

**Date:** 2026-05-14
**Executor:** Separate coder (parallel with Plan A)
**Runs in parallel with:** Plan A (Trader & Filter Backend)

---

## Key Context for the Executor

- **Project root:** `/Users/johnny/Code/DirectionPredictionSystem/`
- **Backend:** `ofi-lab-v3/` — Python 3.12, FastAPI, SQLite
- **Frontend:** `dashboard/` — React 19, TypeScript, Vite, Tailwind CSS v4
- **VPS:** `34.67.75.48`, SSH key `~/.ssh/id_vps_n2`, user `johnny`
- **Test runner (backend):** `cd ofi-lab-v3 && python -m pytest tests/ -x`
- **Test runner (frontend):** None yet — set up vitest
- **No chart library yet**
- **Frontend styling:** Tailwind v4 with "paper aesthetic" theme (warm beige, dark ink)
- **Max width:** `max-w-md` (448px, mobile-first)
- **No Kalshi/Polymarket live trading** — platforms prepared but not enabled
- **API proxy:** Vite dev proxies `/api` to `localhost:8080`
- **Existing test patterns:** See `tests/test_filter_pipeline.py` for filter tests, `tests/test_dashboard_models_admin.py` for FastAPI TestClient usage

---

## TDD Protocol

Every step follows Red-Green-Refactor:
1. **RED**: Write a failing test that defines the desired behavior
2. **GREEN**: Write the minimum code to make the test pass
3. **REFACTOR**: Clean up while keeping tests green
4. **GATE**: All relevant tests must pass before moving to the next step

---

## Step 7: Fix Model Detail Endpoint Data Gaps

### Current State

`GET /api/models/{name}` has three problems:
1. **No `decay_metrics` JOIN** — detail view loses `ewma_ev`, `ewma_brier`, `psi` that the list view shows
2. **`overlap` query lacks `WHERE model_name = ?`** — returns global overlap data, not model-specific
3. **`calibration_summary` hardcoded to `[]`** — real data exists in `calibration_summary` table

### 7.1 RED — Test that detail endpoint returns decay metrics

**File:** `tests/test_dashboard_models_admin.py` — add test

```python
def test_model_detail_includes_decay_metrics(tmp_path, monkeypatch):
    """GET /api/models/{name} should return ewma_ev, ewma_brier, psi from decay_metrics."""
    db_path = tmp_path / "v3.db"
    monkeypatch.setenv("STORAGE_DB_PATH", str(db_path))
    from storage.init_db import init_db
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    init_db(conn)
    conn.execute(
        "INSERT INTO model_registry (name, symbol, training_horizon_seconds, artifact_path, paper_active) "
        "VALUES (?,?,?,?,1)",
        ("h300_btc_test", "BTCUSDT", 300, "/tmp/model.lgb"),
    )
    conn.execute(
        "INSERT INTO decay_metrics (ts, model_name, symbol, market_window_seconds, "
        "window_size, rolling_ev, recency_weighted_ev, rolling_win_rate, "
        "brier_score, calibration_error, sample_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("2026-05-14T00:00:00Z", "h300_btc_test", "BTCUSDT", 300,
         100, 0.02, 0.025, 0.53, 0.24, 0.03, 100),
    )
    conn.commit()

    from fastapi.testclient import TestClient
    from dashboard_api.main import create_app
    app = create_app()
    client = TestClient(app)
    # Override get_db dependency
    from dashboard_api.deps import get_db
    app.dependency_overrides[get_db] = lambda: conn
    resp = client.get("/api/models/h300_btc_test")
    assert resp.status_code == 200
    data = resp.json()
    assert "ewma_ev" in data
    assert data["ewma_ev"] is not None
    assert abs(data["ewma_ev"] - 0.025) < 0.001  # recency_weighted_ev
    assert "ewma_brier" in data
    assert "psi" in data
    conn.close()
```

### 7.2 GREEN — Add `decay_metrics` JOIN to detail endpoint

In `dashboard_api/routers/models_admin.py`, `GET /api/models/{name}` handler:

Add a LEFT JOIN to `decay_metrics` using the same rank-1 subquery pattern as the list endpoint:
```python
row = conn.execute("""
    SELECT mr.*,
           dm.recency_weighted_ev as ewma_ev,
           dm.brier_score as ewma_brier,
           dm.calibration_error as psi
    FROM model_registry mr
    LEFT JOIN (
        SELECT model_name, recency_weighted_ev, brier_score, calibration_error,
               ROW_NUMBER() OVER (PARTITION BY model_name ORDER BY ts DESC) as rn
        FROM decay_metrics
    ) dm ON dm.model_name = mr.name AND dm.rn = 1
    WHERE mr.name = ?
""", (name,)).fetchone()
```

### 7.3 RED — Test that overlap query is model-specific

```python
def test_model_detail_overlap_is_model_specific(tmp_path, monkeypatch):
    """GET /api/models/{name} overlap array should be filtered by model_name."""
    # ... setup DB with model_overlap rows for two different models ...
    # ... verify only the requested model's rows appear ...
```

### 7.4 GREEN — Fix overlap query to add `WHERE model_name = ?`

Change:
```python
conn.execute("SELECT * FROM model_overlap ORDER BY ts_contract_open_ms DESC LIMIT 50")
```
to:
```python
conn.execute("SELECT * FROM model_overlap WHERE symbol = ? ORDER BY ts_contract_open_ms DESC LIMIT 50", (row["symbol"],))
```

Note: `model_overlap` doesn't have a `model_name` column — it records per-(boundary, symbol, window) with `models_scored_json`. So filter by the model's symbol instead, or parse `models_scored_json` to filter.

**Better approach:** Since `model_overlap` is per-(boundary, symbol, window), filter by `symbol` matching the model's symbol. The frontend can then check if the model appears in `models_scored_json`.

### 7.5 RED — Test that calibration_summary returns real data

```python
def test_model_detail_calibration_summary_real_data(tmp_path, monkeypatch):
    """GET /api/models/{name} should return calibration bins from calibration_bins table."""
    # ... insert calibration_bins rows for the model ...
    # ... verify the detail endpoint returns them ...
```

### 7.6 GREEN — Wire calibration_summary to real query

Replace the hardcoded `[]`:
```python
cal_rows = conn.execute(
    "SELECT bin_lo, bin_hi, observed_freq, n FROM calibration_bins "
    "WHERE model_name = ? ORDER BY bin_lo", (name,)
).fetchall()
calibration_summary = [dict(r) for r in cal_rows]
```

### 7.7 GATE — `pytest tests/test_dashboard_models_admin.py -x`

---

## Step 8: Fix Model Detail Card Position — Inline After Clicked Row

### Current State

In `dashboard/src/pages/Models.tsx`:
- `ModelDetailCard` (inline JSX block, lines 88-193) renders **above** the `<ul>` list
- Clicking a model row sets `selected` state → detail card appears at top
- User wants: inline expansion below the clicked row + "View full details" link

### 8.1 RED — Set up frontend test infrastructure

**No frontend tests exist yet.** Set up vitest:

```bash
cd dashboard && npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom
```

Create `dashboard/vitest.config.ts`:
```typescript
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    globals: true,
  },
})
```

Create `dashboard/src/test-setup.ts`:
```typescript
import '@testing-library/jest-dom'
```

Add script to `package.json`:
```json
"test": "vitest run",
"test:watch": "vitest"
```

### 8.2 RED — Test that detail card renders after clicked row

**File:** `dashboard/src/pages/__tests__/Models.test.tsx`

```typescript
import { render, screen, fireEvent } from '@testing-library/react'
import { BrowserRouter } from 'react-router-dom'
import { Models } from '../Models'

// Mock fetch
const mockModels = [
  { name: 'h300_btc_89d', symbol: 'BTCUSDT', horizon: 300, ewma_ev: 0.02, ewma_brier: 0.24, psi: 0.03, is_baseline: 0, lifecycle_state: 'active', paper_active: 1, live_eligible: 0, created_at: '2026-05-13' },
  { name: 'h300_btc_179d', symbol: 'BTCUSDT', horizon: 300, ewma_ev: 0.03, ewma_brier: 0.22, psi: 0.02, is_baseline: 0, lifecycle_state: 'active', paper_active: 1, live_eligible: 0, created_at: '2026-05-13' },
]

beforeEach(() => {
  global.fetch = vi.fn().mockResolvedValue({
    json: () => Promise.resolve({ models: mockModels }),
  })
})

test('detail card appears inline after clicked model row', async () => {
  render(<BrowserRouter><Models /></BrowserRouter>)
  const rows = await screen.findAllByRole('listitem')
  expect(rows).toHaveLength(2)
  // Click the first row
  fireEvent.click(rows[0])
  // The detail card should be a DOM sibling immediately after the first row
  // (not at the top of the list)
  const detailCard = await screen.findByText(/h300_btc_89d/i)
  // Verify the detail card is positioned after the first row in DOM order
  const listItems = document.querySelectorAll('li')
  const firstRow = listItems[0]
  const detailSection = firstRow.nextElementSibling
  expect(detailSection).toBeTruthy()
  expect(detailSection?.tagName).toBe('LI')  // detail card is also an <li>
})
```

### 8.3 GREEN — Refactor Models.tsx to render detail inline

Change the rendering from:
```tsx
{selected && <section>...detail card...</section>}
<ul>{data.map(m => <ModelRow .../>)}</ul>
```

to:
```tsx
<ul>
  {data.map(m => (
    <Fragment key={m.name}>
      <ModelRow ... />
      {selected === m.name && (
        <li className="...">
          <ModelDetailCardInline model={m} detail={detail} onClose={() => setSelected(null)} />
        </li>
      )}
    </Fragment>
  ))}
</ul>
```

Extract the inline detail card into a `ModelDetailCardInline` component (or just inline JSX within the `<li>`). Include a "View full details →" link:
```tsx
<Link to={`/models/${m.name}`} className="text-ink-muted text-sm">
  View full details →
</Link>
```

### 8.4 GATE — `cd dashboard && npm test`

---

## Step 9: Create Per-Model Detail Page (`/models/:name`)

### 9.1 RED — Test that `/models/:name` route renders model detail

**File:** `dashboard/src/pages/__tests__/ModelDetail.test.tsx`

```typescript
test('ModelDetail page renders model name and performance data', async () => {
  // Mock fetch for /api/models/{name} and /api/performance/summary
  render(
    <BrowserRouter>
      <Routes>
        <Route path="/models/:name" element={<ModelDetail />} />
      </Routes>
    </BrowserRouter>,
    { route: '/models/h300_btc_89d' }
  )
  expect(await screen.findByText('h300_btc_89d')).toBeTruthy()
  // Performance section should be visible
  expect(await screen.findByText(/win rate/i)).toBeTruthy()
})
```

### 9.2 GREEN — Add route to App.tsx

```tsx
import ModelDetail from './pages/ModelDetail'
// ...
<Route path="/models/:name" element={<ModelDetail />} />
```

### 9.3 GREEN — Create `ModelDetail.tsx`

**File:** `dashboard/src/pages/ModelDetail.tsx`

Page structure with sections:
1. **Header** — model name, symbol badge, horizon badge, lifecycle state, back button
2. **Metrics row** — ewma_ev, ewma_brier, psi (from `/api/models/{name}`)
3. **Performance summary** — win rate + Wilson CI, ROI%, NE_t total/per-trade, direction breakdown (from `/api/performance/summary?model=X`)
4. **Rolling accuracy** — line chart from `/api/performance/rolling?model=X` (recharts)
5. **Calibration curve** — chart from `/api/performance/calibration?model=X` (recharts)
6. **Divergence buckets** — table from `/api/performance/by-divergence?model=X`
7. **Hour/Day heatmap** — grid from `/api/performance/heatmap?model=X`
8. **Filter config** — current thresholds, per-platform toggles (read-only for now)
9. **Audit trail** — table from `recent_audit`

Data fetching: `usePoll` hook with 60s interval, or single fetch on mount.

**Key:** Install recharts first:
```bash
cd dashboard && npm install recharts
```

### 9.4 GREEN — Add API methods to `lib/api.ts`

```typescript
async performanceSummary(model?: string, symbol?: string): Promise<any> {
  return this.request(`/performance/summary?${new URLSearchParams({model: model || '', symbol: symbol || ''})}`)
}
async performanceRolling(model: string, n?: number): Promise<any> {
  return this.request(`/performance/rolling?model=${model}&n=${n || 50}`)
}
async performanceCalibration(model: string): Promise<any> {
  return this.request(`/performance/calibration?model=${model}`)
}
async performanceByDivergence(model: string): Promise<any> {
  return this.request(`/performance/by-divergence?model=${model}`)
}
async performanceHeatmap(model: string): Promise<any> {
  return this.request(`/performance/heatmap?model=${model}`)
}
```

### 9.5 GATE — `cd dashboard && npm test`

---

## Step 10: Create Per-Symbol Page (`/symbols/:symbol`)

### 10.1 RED — Test that `/symbols/:symbol` renders model comparison

**File:** `dashboard/src/pages/__tests__/SymbolDetail.test.tsx`

```typescript
test('SymbolDetail page renders all models for the symbol', async () => {
  // ... mock fetches ...
  render(/* SymbolDetail at /symbols/BTCUSDT */)
  expect(await screen.findByText(/BTCUSDT/i)).toBeTruthy()
  // Should show 21 models (7 horizons × 3 train windows)
  const rows = await screen.findAllByRole('row')
  expect(rows.length).toBeGreaterThan(0)
})
```

### 10.2 GREEN — Add route to App.tsx

```tsx
import SymbolDetail from './pages/SymbolDetail'
<Route path="/symbols/:symbol" element={<SymbolDetail />} />
```

### 10.3 GREEN — Create `SymbolDetail.tsx`

**File:** `dashboard/src/pages/SymbolDetail.tsx`

Page structure:
1. **Header** — symbol name, price, back button
2. **Model comparison table** — all models for this symbol sorted by horizon then train_days
   - Columns: model name, horizon, train_days, ewma_ev, accuracy, ROI%, win rate
3. **Horizon comparison** — group by horizon, show best model per horizon
4. **Train window comparison** — 90d vs 180d vs 330d at each horizon
5. **Cross-model correlation** — matrix from `/api/performance/cross-symbol-correlation?model=X`

### 10.4 GATE — `cd dashboard && npm test`

---

## Step 11: Add Performance Analysis Endpoints

### 11.1 RED — Test portfolio-level metrics

**File:** `tests/test_dashboard_performance.py` — NEW file

```python
def test_portfolio_metrics_endpoint(tmp_path, monkeypatch):
    """GET /api/performance/portfolio should return aggregate metrics."""
    # ... setup DB with resolved trades ...
    # ... call /api/performance/portfolio ...
    data = resp.json()
    assert "total_roi_pct" in data
    assert "profit_factor" in data
    assert "max_drawdown" in data
    assert "sharpe_ratio" in data
    assert "total_trades" in data
```

### 11.2 GREEN — Add portfolio metrics function to `services/metrics.py`

```python
def portfolio_metrics(trades: list[dict]) -> dict:
    """Compute portfolio-level performance metrics from resolved trades."""
    if not trades:
        return {"total_trades": 0, "total_roi_pct": 0.0, "profit_factor": 0.0,
                "max_drawdown": 0.0, "sharpe_ratio": None, "sortino_ratio": None}
    # Compute cumulative P&L series
    net_values = []
    for t in trades:
        direction = t.get("pred_direction", "up")
        correct = t.get("prediction_correct")
        p_market = t.get("p_market", 0.5)
        ne = compute_realized_net(direction, correct, p_market)
        net_values.append(ne if ne is not None else 0.0)

    total_ne = sum(net_values)
    n = len(net_values)
    roi_pct = (total_ne / n) * 100 if n > 0 else 0.0

    # Profit factor: gross_wins / abs(gross_losses)
    wins = sum(v for v in net_values if v > 0)
    losses = sum(abs(v) for v in net_values if v < 0)
    profit_factor = wins / losses if losses > 0 else float('inf') if wins > 0 else 0.0

    # Max drawdown from cumulative series
    cumulative = []
    running = 0.0
    for v in net_values:
        running += v
        cumulative.append(running)
    peak = 0.0
    max_dd = 0.0
    for c in cumulative:
        if c > peak:
            peak = c
        dd = peak - c
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (annualized, assuming 1 trade per 5 min = 105,120/yr)
    import numpy as np
    if len(net_values) > 1:
        mean_ne = np.mean(net_values)
        std_ne = np.std(net_values, ddof=1)
        trades_per_year = 105120  # 84 models * 12 boundaries/hr * 24hr * 365d / 1000 (rough)
        sharpe = (mean_ne / std_ne) * np.sqrt(trades_per_year) if std_ne > 0 else None
        downside = [v for v in net_values if v < 0]
        downside_std = np.std(downside, ddof=1) if len(downside) > 1 else std_ne
        sortino = (mean_ne / downside_std) * np.sqrt(trades_per_year) if downside_std > 0 else None
    else:
        sharpe = None
        sortino = None

    return {
        "total_trades": n,
        "total_ne": round(total_ne, 6),
        "total_roi_pct": round(roi_pct, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float('inf') else None,
        "max_drawdown": round(max_dd, 6),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "sortino_ratio": round(sortino, 4) if sortino is not None else None,
        "win_rate": round(sum(1 for v in net_values if v > 0) / n, 4) if n > 0 else 0.0,
        "avg_trade_ne": round(total_ne / n, 6) if n > 0 else 0.0,
    }
```

### 11.3 GREEN — Add endpoint to `routers/performance.py`

```python
@router.get("/performance/portfolio")
async def portfolio_performance(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    conn=Depends(get_db),
):
    resolved = store.get_resolved_trades(model=model, symbol=symbol, page_size=100000)
    if from_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) >= from_ms]
    if to_ms:
        resolved = [t for t in resolved if t.get("ts_model_ran_ms", 0) <= to_ms]
    return portfolio_metrics(resolved)
```

### 11.4 RED — Test threshold sweep endpoint

```python
def test_threshold_sweep_endpoint(tmp_path, monkeypatch):
    """GET /api/performance/threshold-sweep should return win%/ROI at each confidence level."""
    # ... setup ...
    data = resp.json()
    assert "sweep" in data
    assert len(data["sweep"]) > 0
    # Each point should have threshold, win_rate, roi_pct, n_trades
    point = data["sweep"][0]
    assert "threshold" in point
    assert "win_rate" in point
    assert "roi_pct" in point
    assert "n_trades" in point
```

### 11.5 GREEN — Add threshold sweep function to `metrics.py`

```python
def threshold_sweep(trades: list[dict], thresholds: list[float] = None) -> list[dict]:
    """Compute win_rate and ROI at each confidence threshold."""
    if thresholds is None:
        thresholds = [0.50 + i * 0.01 for i in range(31)]  # 0.50 to 0.80
    results = []
    for t in thresholds:
        # Filter trades where calibrated_p >= threshold
        filtered = [tr for tr in trades
                    if max(tr.get("pred_proba_calibrated", 0.5),
                           1 - tr.get("pred_proba_calibrated", 0.5)) >= t]
        if not filtered:
            results.append({"threshold": t, "win_rate": None, "roi_pct": None, "n_trades": 0})
            continue
        net_values = []
        wins = 0
        for tr in filtered:
            direction = tr.get("pred_direction", "up")
            correct = tr.get("prediction_correct")
            p_market = tr.get("p_market", 0.5)
            ne = compute_realized_net(direction, correct, p_market)
            if ne is not None:
                net_values.append(ne)
                if ne > 0:
                    wins += 1
        n = len(net_values)
        wr = wins / n if n > 0 else 0.0
        roi = (sum(net_values) / n) * 100 if n > 0 else 0.0
        results.append({
            "threshold": t,
            "win_rate": round(wr, 4),
            "roi_pct": round(roi, 4),
            "n_trades": n,
        })
    return results
```

### 11.6 GREEN — Add endpoint

```python
@router.get("/performance/threshold-sweep")
async def threshold_sweep_performance(
    model: str | None = None,
    symbol: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    conn=Depends(get_db),
):
    resolved = store.get_resolved_trades(model=model, symbol=symbol, page_size=100000)
    # ... time filtering ...
    # Use predictions (not trades) for threshold sweep — we want all predictions,
    # not just those that passed a gate
    # Actually, we need predictions with prediction_correct set
    from dashboard_api.services.metrics import threshold_sweep
    return {"sweep": threshold_sweep(resolved)}
```

**Note:** The threshold sweep needs ALL resolved predictions (not just trades), because trades are already filtered by confidence. We need the full prediction population to see what happens at each threshold. This requires a new store method: `get_resolved_predictions()`.

### 11.7 RED — Test Pareto frontier endpoint

```python
def test_pareto_endpoint_returns_frontier(tmp_path, monkeypatch):
    """GET /api/performance/pareto should return the efficient frontier."""
    data = resp.json()
    assert "frontier" in data
    # Frontier points should be non-dominated
    for i, p1 in enumerate(data["frontier"]):
        for j, p2 in enumerate(data["frontier"]):
            if i != j:
                # No point should strictly dominate another on the frontier
                assert not (p2["win_rate"] >= p1["win_rate"] and p2["roi_pct"] >= p1["roi_pct"] and
                           (p2["win_rate"] > p1["win_rate"] or p2["roi_pct"] > p1["roi_pct"]))
```

### 11.8 GREEN — Add Pareto frontier computation

```python
def pareto_frontier(sweep_results: list[dict]) -> list[dict]:
    """Extract non-dominated points from threshold sweep results."""
    valid = [p for p in sweep_results if p["n_trades"] > 0 and p["win_rate"] is not None]
    if not valid:
        return []
    # Sort by win_rate ascending
    valid.sort(key=lambda p: p["win_rate"])
    frontier = []
    max_roi = -float('inf')
    for p in valid:
        if p["roi_pct"] > max_roi:
            frontier.append(p)
            max_roi = p["roi_pct"]
    return frontier
```

### 11.9 GATE — `pytest tests/test_dashboard_performance.py -x`

---

## Step 12: Frontend Optimization UI

### 12.1 RED — Test that ModelDetail page has threshold sweep chart

**File:** `dashboard/src/pages/__tests__/ModelDetail.test.tsx` — add test

```typescript
test('ModelDetail page renders threshold sweep chart', async () => {
  // ... mock /api/performance/threshold-sweep ...
  render(/* ModelDetail */)
  // Should show a chart with threshold on x-axis, win_rate + roi on y-axis
  expect(await screen.findByText(/confidence threshold/i)).toBeTruthy()
})
```

### 12.2 GREEN — Add threshold sweep chart to ModelDetail

Use recharts `ComposedChart` with dual Y-axes:
- X-axis: confidence threshold (0.50 to 0.80)
- Left Y-axis: win rate (0% to 70%)
- Right Y-axis: ROI% (-5% to +10%)
- Two `Line` components: one for win_rate, one for roi_pct
- Highlight the Pareto frontier points

### 12.3 GREEN — Add interactive threshold slider

Below the chart, add a range slider for the confidence threshold. When adjusted:
1. Show the expected win_rate and ROI% at that threshold
2. Show the number of trades that would pass
3. Show the Kelly-optimized stake at that threshold

This is a **display-only** control — it doesn't change the actual filter config. A "Apply threshold" button sends a `PUT /api/models/{name}/filter-config` request (added by Plan A).

### 12.4 GATE — `cd dashboard && npm test`

---

## Step 13: Add Models Tab to TabBar

### 13.1 Current State

TabBar has 4 tabs: home, trades, predictions, live. Models, Settings, FleetTraining are hidden.

### 13.2 GREEN — Add models tab

In `dashboard/src/components/TabBar.tsx`, add a 5th tab:

```tsx
<NavLink to="/models" className={/* ... */}>
  models
</NavLink>
```

### 13.3 GATE — `cd dashboard && npm test`

---

## Step 14: Prepare Platform Toggle UI (Read-Only for Now)

### 14.1 GREEN — Add platform status display to ModelDetail page

In the ModelDetail page, add a "Platform Status" section:
- Paper: Active / Inactive (green/red badge)
- Kalshi: Prepared / Not Ready (gray badge — not live yet)
- Polymarket: Prepared / Not Ready (gray badge)

These are read-only displays. The toggle functionality will be wired when Plan A's `platform_active_json` endpoints are available.

### 14.2 GATE — `cd dashboard && npm test && npm run build`

---

## Files Changed Summary

| File | Change | Step |
|------|--------|------|
| `ofi-lab-v3/dashboard_api/routers/models_admin.py` | Fix overlap WHERE, add decay_metrics JOIN, wire calibration_summary | 7 |
| `ofi-lab-v3/dashboard_api/routers/performance.py` | Add portfolio, threshold-sweep, pareto endpoints | 11 |
| `ofi-lab-v3/dashboard_api/services/metrics.py` | Add portfolio_metrics, threshold_sweep, pareto_frontier | 11 |
| `ofi-lab-v3/dashboard_api/services/sqlite_store.py` | Add get_resolved_predictions() method | 11 |
| `dashboard/src/pages/Models.tsx` | Refactor to inline detail card + full-page link | 8 |
| `dashboard/src/pages/ModelDetail.tsx` | **NEW** — per-model detail page | 9 |
| `dashboard/src/pages/SymbolDetail.tsx` | **NEW** — per-symbol comparison page | 10 |
| `dashboard/src/App.tsx` | Add `/models/:name` and `/symbols/:symbol` routes | 9-10 |
| `dashboard/src/components/TabBar.tsx` | Add models tab | 13 |
| `dashboard/src/lib/api.ts` | Add performance API methods | 9 |
| `dashboard/package.json` | Add recharts, vitest, @testing-library/react | 8-9 |
| `dashboard/vitest.config.ts` | **NEW** — vitest configuration | 8 |
| `dashboard/src/test-setup.ts` | **NEW** — test setup | 8 |
| `tests/test_dashboard_models_admin.py` | Add detail endpoint data tests | 7 |
| `tests/test_dashboard_performance.py` | **NEW** — performance endpoint tests | 11 |
| `dashboard/src/pages/__tests__/Models.test.tsx` | **NEW** — Models page tests | 8 |
| `dashboard/src/pages/__tests__/ModelDetail.test.tsx` | **NEW** — ModelDetail page tests | 9 |
| `dashboard/src/pages/__tests__/SymbolDetail.test.tsx` | **NEW** — SymbolDetail page tests | 10 |

## Cross-Plan Dependencies

Plan B should **NOT** depend on Plan A's code changes being complete. Specifically:
- The `filter_config_json` and `platform_active_json` columns (Plan A Step 4) won't exist yet when Plan B runs
- The `model_selection` table (Plan A Step 4) won't exist yet
- Plan B's frontend should **gracefully handle missing keys** — use `?.` chaining and default values
- Plan B's API endpoints should **not assume** new columns exist — use `try/except` or check for column presence

When Plan A and Plan B are both complete, Plan C will reconcile any integration issues.
