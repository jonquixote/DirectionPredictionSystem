# polymarket-ofi Dashboard — Frontend Specification

**Project:** `polymarket-ofi` ·
[github.com/jonquixote/DirectionPredictionSystem](https://github.com/jonquixote/DirectionPredictionSystem/tree/main/polymarket-ofi)
**Version:** 1.1
**Date:** April 2026
**API reference:** `API_CONTRACT.md`
**Backend tasks:** `BACKEND_TASKS.md`

---

## Contents

1. [Overview](#1-overview)
2. [Tech Stack](#2-tech-stack)
3. [Design System](#3-design-system)
4. [Layout Shell](#4-layout-shell)
5. [State Architecture](#5-state-architecture)
6. [Section 1 — Live System Status](#6-section-1--live-system-status)
7. [Section 2 — Prediction Explorer](#7-section-2--prediction-explorer)
8. [Section 3 — Trade Explorer](#8-section-3--trade-explorer)
9. [Section 4 — Performance Dashboard](#9-section-4--performance-dashboard)
10. [Section 5 — Model Comparison](#10-section-5--model-comparison)
11. [Section 6 — p_market / EV Analysis](#11-section-6--pmarket--ev-analysis)
12. [Section 7 — Price & Market Overlay](#12-section-7--price--market-overlay)
13. [Section 8 — Feature Analysis](#13-section-8--feature-analysis)
14. [Section 9 — Training & Architecture History](#14-section-9--training--architecture-history)
15. [Section 10 — Statistical Tools](#15-section-10--statistical-tools)
16. [Section 11 — Alerts & Anomaly Panel](#16-section-11--alerts--anomaly-panel)
17. [Section 12 — Raw Log Explorer](#17-section-12--raw-log-explorer)
18. [Crossfilter Design](#18-crossfilter-design)
19. [URL State Persistence](#19-url-state-persistence)
20. [WebSocket Integration](#20-websocket-integration)
21. [File & Directory Structure](#21-file--directory-structure)
22. [Performance Requirements](#22-performance-requirements)
23. [Accessibility & Quality](#23-accessibility--quality)
24. [Development Phases](#24-development-phases)
25. [Glossary](#25-glossary)

---

## 1. Overview

This document specifies the complete frontend for the Polymarket OFI Direction Prediction
monitoring dashboard. The system uses LightGBM models (H60 and H300 horizons) trained on
Bybit L2 orderbook features to predict direction on Polymarket binary crypto contracts.

The frontend is a **single-page application (SPA)** that connects exclusively to the
`dashboard_api` FastAPI backend via REST and WebSocket. It never reads parquet files or
SQLite directly — all data comes through the API layer.

**Primary goals:**
- Expose every field of every prediction, trade, and resolution record
- Track all metrics we have been computing manually so they update automatically
- Provide the statistical context needed to interpret what the numbers mean
- Give a live, persistent view of system health 24/7

---

## 2. Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| UI Framework | React 18 + Vite | SPA routing, reactive state, rich ecosystem |
| State Management | Zustand | Lightweight, WebSocket-friendly, no boilerplate |
| Charts | Recharts + Lightweight-Charts | Recharts for analytics; Lightweight-Charts for OHLCV |
| Feature Viz | D3.js | Calibration curves, heatmap, correlation matrix, lineage |
| Tables | TanStack Table v8 | Virtualized, sortable, filterable, server-side pagination |
| Styling | Tailwind CSS v4 | Token-aligned, dark/light via `data-theme` |
| Statistical Tools | `jstat` | Binomial, z-test, Wilson CI, beta distribution in-browser |
| Date/Time | `date-fns` | UTC-correct formatting and range arithmetic |
| JSON Viewer | `react-json-view-lite` | Nested JSON rendering in Raw Log Explorer |
| Diff Viewer | `react-diff-viewer` | Side-by-side record comparison |
| Icons | Lucide React | Consistent, tree-shakeable icon set |
| API Transport | REST + WebSocket | REST for historical; WS for live push |
| Routing | React Router v6 | Hash-based routing with URL filter state serialization |

**Removed from earlier draft:** `hyparquet` (WASM parquet reader). Parquet data is
served through the backend API (`/api/parquet/*`). Do not load parquet files directly
in the browser.

---

## 3. Design System

### 3.1 Color Palette

The dashboard runs 24/7. Dark-mode-first reduces eye strain. The palette below is a
**custom override** of the Nexus design system token names — variable names are kept
identical so light mode falls back to Nexus beige surfaces correctly.

```css
/* Dark mode (default for this app) */
[data-theme="dark"], :root {
  --color-bg:               #0f1117;
  --color-surface:          #161b27;
  --color-surface-2:        #1c2236;
  --color-surface-offset:   #212840;
  --color-surface-dynamic:  #272e48;
  --color-divider:          rgba(255,255,255,0.06);
  --color-border:           rgba(255,255,255,0.08);

  --color-text:             #e2e8f0;
  --color-text-muted:       #94a3b8;
  --color-text-faint:       #475569;
  --color-text-inverse:     #0f1117;

  --color-primary:          #2dd4bf;   /* teal — CTAs, active nav, live dots */
  --color-primary-hover:    #14b8a6;
  --color-primary-active:   #0d9488;
  --color-primary-highlight: rgba(45,212,191,0.10);

  --color-success:          #4ade80;   /* correct prediction, gate pass */
  --color-error:            #f87171;   /* incorrect prediction, gate fail */
  --color-warning:          #fbbf24;   /* suppressed, warmup, unresolved */
  --color-purple:           #a78bfa;   /* H300 model series */
  --color-orange:           #fb923c;   /* H60 model series */
  --color-blue:             #60a5fa;   /* H60 V3 model series / secondary */

  --shadow-sm:  0 1px 2px rgba(0,0,0,0.3);
  --shadow-md:  0 4px 12px rgba(0,0,0,0.4);
  --shadow-lg:  0 12px 32px rgba(0,0,0,0.5);

  --radius-sm:   0.375rem;
  --radius-md:   0.5rem;
  --radius-lg:   0.75rem;
  --radius-xl:   1rem;
  --radius-full: 9999px;

  --transition-interactive: 180ms cubic-bezier(0.16, 1, 0.3, 1);
}

/* Light mode — Nexus beige surfaces */
[data-theme="light"] {
  --color-bg:               #f7f6f2;
  --color-surface:          #f9f8f5;
  --color-surface-2:        #fbfbf9;
  --color-surface-offset:   #f3f0ec;
  --color-surface-dynamic:  #e6e4df;
  --color-divider:          #dcd9d5;
  --color-border:           #d4d1ca;
  --color-text:             #28251d;
  --color-text-muted:       #7a7974;
  --color-text-faint:       #bab9b4;
  --color-text-inverse:     #f9f8f4;
  --color-primary:          #01696f;
  --color-primary-hover:    #0c4e54;
  --color-primary-highlight: #cedcd8;
  --color-success:          #437a22;
  --color-error:            #a12c7b;
  --color-warning:          #964219;
  --color-purple:           #7a39bb;
  --color-orange:           #da7101;
  --color-blue:             #006494;
  --shadow-sm:  0 1px 2px rgba(0,0,0,0.06);
  --shadow-md:  0 4px 12px rgba(0,0,0,0.08);
  --shadow-lg:  0 12px 32px rgba(0,0,0,0.12);
}
```

### 3.2 Model Color Assignment

Each model gets a consistent color used across all charts, badges, and table rows:

| Model | Color Token | Hex (dark) |
|-------|-------------|------------|
| H60 V1 | `--color-orange` | `#fb923c` |
| H60 V3 | `--color-blue` | `#60a5fa` |
| H300 | `--color-purple` | `#a78bfa` |

### 3.3 Typography

```css
--font-display: 'Geist', 'Inter', sans-serif;   /* headings, section titles */
--font-body:    'Inter', sans-serif;             /* all UI text, tables */
--font-mono:    'JetBrains Mono', 'Fira Code', monospace;  /* IDs, timestamps, JSON */
```

Load via CDN:
```html
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300..700
            &family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

Geist via `@vercel/font` or CDN equivalent.

**Type scale:**
```css
--text-xs:   0.75rem;    /* 12px — tiny badges, metadata */
--text-sm:   0.8125rem;  /* 13px — table cells, secondary labels */
--text-base: 0.875rem;   /* 14px — standard dashboard body (dense UI) */
--text-lg:   1rem;       /* 16px — section headings */
--text-xl:   1.125rem;   /* 18px — panel titles */
--text-2xl:  1.375rem;   /* 22px — page title in topbar */
```

All table numerics: `font-variant-numeric: tabular-nums; font-family: var(--font-mono);`

### 3.4 Spacing

4px base unit. Use Tailwind spacing scale directly (`gap-2` = 8px, `p-4` = 16px, etc.).
Section padding: `px-6 py-5`. Card padding: `p-4`. Table cell padding: `px-3 py-2`.

### 3.5 NE_t Display Convention

**Wherever NE_t is displayed, it must be labeled "Realized NE_t" and shown in dollars.**
Never show the theoretical formula `p_model − fee − p_market` as a primary metric.
The theoretical figure may appear in a secondary comparison column, always labeled
"Theoretical (comparison only)".

---

## 4. Layout Shell

```
┌─ 60px topbar ───────────────────────────────────────────────────────────────┐
│  [Logo]  polymarket-ofi     ● LIVE    🔔 3    [UTC/Local toggle]  [☀/🌙]   │
└─────────────────────────────────────────────────────────────────────────────┘
│                                                                              │
│  ┌── 240px sidebar ──┐  ┌── main content (flex-1, single scroll region) ──┐ │
│  │                   │  │                                                  │ │
│  │  ① Live Status    │  │  <ActiveFiltersBar />  (sticky, dismissible)    │ │
│  │  ② Predictions    │  │                                                  │ │
│  │  ③ Trades         │  │  <SectionContent />                              │ │
│  │  ④ Performance    │  │                                                  │ │
│  │  ⑤ Model Comp     │  │                                                  │ │
│  │  ⑥ p_market EV    │  │                                                  │ │
│  │  ⑦ Price Chart    │  │                                                  │ │
│  │  ⑧ Features       │  │                                                  │ │
│  │  ⑨ Architecture   │  │                                                  │ │
│  │  ⑩ Stat Tools     │  │                                                  │ │
│  │  ⑪ Alerts         │  │                                                  │ │
│  │  ⑫ Raw Logs       │  │                                                  │ │
│  │  ──────────────   │  │                                                  │ │
│  │  H300 ●  LIVE     │  │                                                  │ │
│  │  H60V3 ● WARMUP   │  │                                                  │ │
│  │  Gate: ✅ 53.2%   │  │                                                  │ │
│  └───────────────────┘  └──────────────────────────────────────────────────┘ │
```

**Rules:**
- ONE scroll region: the main content pane. No nested scrollers except virtualized
  table bodies.
- Sticky section sub-tabs where needed.
- Sidebar collapses to icon-only at viewport < 1024px. Bottom nav bar at < 768px.
- Topbar live dot: green = WebSocket connected, amber = reconnecting, red = disconnected.
- Topbar alert bell shows unread count. Clicking opens alerts drawer.
- All KPI numbers animate on value change (counter, 300ms ease-out). Respect
  `prefers-reduced-motion` — skip animation if set.

### 4.1 Topbar Components

**Timezone Toggle (required):**
All timestamps in the system are UTC. Many hours are meaningful in UTC context
(overnight suppression is UTC 21:00–04:00). The toggle switches display between
`UTC` and `Local (PDT)`. Default to UTC. Store preference in `settingsStore`.
Apply everywhere: table cells, chart axes, heatmap labels, tooltips.

```typescript
// settingsStore.ts
interface Settings {
  timezone: "UTC" | "local";
  theme: "dark" | "light";
  alert_thresholds: AlertThresholds;
  rolling_n: number;           // default 50, used across all rolling charts
  fee: number;                 // default 0.02, used in NE_t calculations
  annotation_layer: boolean;   // show deployment events on time series charts
}
```

---

## 5. State Architecture

### 5.1 Stores

**`liveStore.ts` — WebSocket state (real-time)**
```typescript
{
  connected: boolean;
  last_status: StatusResponse | null;
  recent_predictions: PredictionRecord[];   // last 100, newest first
  recent_trades: TradeRecord[];             // last 50 resolved
  recent_alerts: AlertRecord[];             // last 20
  last_update_ms: number | null;
}
```

**`filterStore.ts` — Shared crossfilter state**
```typescript
// Mirrors FilterState from API_CONTRACT.md §9
// All filter changes serialize to URL query string (see §19)
{
  model, symbol, from_ms, to_ms, direction,
  suppressed, warmup, outcome, contract_duration,
  divergence_min, divergence_max, pmodel_min, pmodel_max,
  net_sign, settled
}
```

**`settingsStore.ts` — User preferences (in-memory, no localStorage)**
```typescript
{
  timezone, theme, alert_thresholds,
  rolling_n, fee, annotation_layer
}
```

### 5.2 API Client

`api/client.ts` — Axios instance with:
- Base URL: `http://localhost:8765`
- Basic auth headers injected from env or runtime prompt
- Request deduplication for identical in-flight requests
- Automatic retry (×2) on 503 with 500ms backoff
- Toast on 401 (credentials prompt)

`api/websocket.ts` — WebSocket manager with:
- Auto-reconnect with exponential backoff (1s → 2s → 4s → ... max 30s)
- On reconnect: dispatch `status` snapshot immediately
- Exposes `subscribe(type, callback)` for per-message-type listeners

---

## 6. Section 1 — Live System Status

**Purpose:** Real-time operational health. The first thing you check when
something feels wrong.

### Layout

Full-width grid, auto-refreshing via WebSocket every 5 seconds.

---

**Row 1 — Container Health Cards (one per model)**

One card per model (`H60 V1`, `H60 V3`, `H300`). Each shows:

```
┌─────────────────────────────────────────┐
│  H300  ● LIVE                           │
│  CPU  ████░░░░ 42%    RAM  6.1 / 8 GB  │
│  Uptime: 2d 14h 33m                    │
│  Last prediction: 23s ago (SOL)        │
│  Predictions/hr: 18  │  Trades/hr: 12  │
└─────────────────────────────────────────┘
```

- Live status dot: green=healthy, amber=warmup, red=stopped/unhealthy
- CPU: ring gauge, color shifts red above 80%
- RAM: horizontal progress bar
- `WARMUP` badge (amber) replaces `LIVE` badge (teal) when `warmup: true`
- Last prediction timestamp formats as relative ("23s ago", "4m ago", "2h ago")
  in chosen timezone

---

**Row 2 — Data Pipeline Health**

Three panels side by side:

| Panel | Content |
|---|---|
| Feature Pipeline | Last parquet update, hours since ingest, file sizes, row counts. Cell glows amber if `stale: true` |
| p_market API | Last fetch time, latency ms, consecutive failure count. Red if `degraded: true` |
| EWM Preload | Mini-table: 3 rows (BTC/SOL/ETH) × 4 cols (rows loaded, mean, std, last updated) |

---

**Row 3 — Model Metadata & Gate Status**

Left: Model file cards (version, trained date, feature count, AUC).

Center: Active suppression rules as pills:
- `UTC_BLACKOUT active` → red pill with tooltip showing hours affected
- `CONTRACT_MISMATCH suppressing` → amber pill
- Inactive rules → faded pills

Right: Gate status mini-cards (one per model):
```
H300  Rolling-50: 53.2%  ↑  ✅ PASS (+1.7pp)
H60V3 Rolling-50: 48.0%  ↓  ❌ FAIL (−3.5pp)
```
Clicking any gate card navigates to Section 4 with that model pre-selected.

---

**Row 4 — Execution Funnel (NEW)**

Horizontal funnel chart showing drop-off at each gate stage across all models.
Data from `GET /api/performance/funnel`.

```
Predictions  →  Structural  →  Adverse  →  NE_t  →  Sanderink  →  Executed
   450             380           310         290        270           245
               −15.6%        −18.4%       −6.5%      −7.3%        (54.4% overall)
```

Color: teal for executed, progressively lighter for earlier stages.
Tooltip on each stage: count + drop rate from previous stage.

---

**Row 5 — Coverage Rate & p_market Status**

Left: Coverage rate donut (predictions fired / contracts available in last 1h).

Right: Per-symbol p_market live values with anomaly indicator.
Red border if `degraded: true` or p_market outside 0.40–0.60.

---

**Data:** All from `GET /api/status` + WebSocket `status` messages.

---

## 7. Section 2 — Prediction Explorer

**Purpose:** Fully filterable, sortable log of every prediction record.

### Filter Bar (sticky)

```
[Model ▾]  [Symbol ▾]  [Time range ▾]  [Direction ▾]
[Status ▾]  [Warmup ▾]  [Divergence ●───● ]  [p_model ●───● ]  [Search prediction_id]
```

- Multi-select checkboxes for Model and Symbol
- Time range presets: Last 1h / 6h / 24h / 7d / All + custom date-range picker
- Divergence and p_model: dual-handle range sliders
- Collapsible to "Filters (3 active)" button on narrow screens
- "Clear all" link when any non-default filter is active

### Table Columns

| Column | Source | Notes |
|--------|--------|-------|
| Timestamp | `ts_model_ran_ms` | Monospace, timezone from settings, sortable |
| ID | `prediction_id` | Truncated (first 8 chars) + copy button |
| Symbol | `symbol` | Colored chip (BTC=teal, SOL=purple, ETH=blue) |
| Model | `model` | Colored version badge |
| Direction | `pred_direction` | ▲ up (teal) / ▼ down (orange) |
| p_model | `pred_proba` | 4dp, monospace |
| p_market | `p_market` | 4dp, null shown as `—` |
| Divergence | `signed_divergence` | Signed; green if same dir as prediction |
| \|Div\| | `divergence` | Absolute, used for bucket coloring |
| Warmup | `warmup` | Badge if true |
| Suppressed | `suppressed_reason` | Amber badge, hover shows rule name |
| Outcome | resolved outcome | 🟢 correct / 🔴 incorrect / ⚫ unresolved |
| → Trade | `trade_id` | Button, disabled if no trade |

**Row color coding:**
- Correct + resolved → left border teal, subtle teal row tint
- Incorrect + resolved → left border red
- Suppressed → subtle amber row tint
- Unresolved → neutral

**Row click → side drawer:**
- Full JSON viewer (all fields including `features` dict)
- Features sorted by model importance rank if available
- Z-score shown next to each feature value (vs training distribution)
- `→ Trade` and `→ Resolution` buttons in drawer footer
- Drawer is 40% viewport width, slides in from right
- Closes on Escape or backdrop click

**Export:** "Export CSV" button — exports all fields, features dict as JSON string.
Respects active filters. Max 10,000 rows per export.

**Pagination:** Server-side, 50 per page. Virtual rows for current page.
Header shows: "Showing 1–50 of 4,382 predictions"

---

## 8. Section 3 — Trade Explorer

**Purpose:** Full log of paper trade records with outcome, NE_t breakdown, and gate attribution.

### Filter Bar

All Section 2 filters plus:
- Contract Duration (All / 300s / 900s)
- Outcome (All / Correct / Incorrect / Unresolved)
- NE_t sign (All / Positive / Negative)
- Settlement (All / Settled / Open)

### Open Trades Banner

Collapsible banner above the table:
```
⏳ 3 trades currently open
  BTC 900s UP  -   opened 4m ago  -   p_market 0.614
  SOL 900s DN  -   opened 2m ago  -   p_market 0.381
  BTC 900s UP  -   opened 7m ago  -   p_market 0.598
```
Updates live via WebSocket. Each row links to the trade in the table below.

### Table Columns

| Column | Source | Notes |
|--------|--------|-------|
| ID | `id` | Monospace int, copy button |
| Timestamp | `timestamp_ms` | Timezone from settings |
| Symbol | | Chip |
| Model | | Badge |
| Duration | `contract_duration` | `300s` / `900s` pill |
| Direction | | ▲/▼ |
| Stake | `simulated_stake_usdc` | `$10.00` |
| p_market | | 4dp |
| Price Open | `price_at_open` | Monospace |
| Price Close | `price_at_close` | Blank if open |
| Outcome | | ✓ correct / ✗ incorrect / — open |
| Realized NE_t | `realized_net` | Dollar, colored green/red. Null shown as `—` |
| Suppressed | `suppressed_reason` | Gate badge |
| Status | `resolved` | Open pill / Settled pill |
| Links | | → Prediction, → Resolution buttons |

**NE_t Tooltip (hover over Realized NE_t cell):**
```
Direction: UP  -   Outcome: WIN
p_market: 0.614  -   Fee: 0.02
Formula: +(1 − 0.614 − 0.02) = +$0.366
```
Shows the full `realized_net_breakdown` object from the API.

**Row click → trade detail drawer:**
Full JSON, linked prediction, linked resolution if available.
Same drawer pattern as Section 2.

---

## 9. Section 4 — Performance Dashboard

**Purpose:** Core accuracy, edge, and risk metrics.

### Sub-tabs

`Overview` · `By Symbol` · `By Contract` · `By Hour` · `By Direction`
· `By Divergence` · `By p_market Range` · `Streaks & Drawdown`

---

### Overview Tab

**KPI Row (6 animated cards):**

| Card | Primary | Secondary |
|------|---------|-----------|
| Accuracy | `54.2%` large | `[51.1% – 57.3%]` CI below |
| Total Trades | `245` | `34W / 28L / 12 open` |
| Realized NE_t | `+$66.70` total | `+$0.64/trade` below |
| Gate Pass Rate | `54.4%` | `245 / 450 candidates` |
| High-Div Accuracy | `61.3%` | top 25% divergence, amber label |
| Significance | `p = 0.004` | `z = 2.62  ✅ sig.` |

Clicking the Accuracy card or CI expands a small popover explaining Wilson CI.
Clicking Significance expands a popover with the full z-test result.

**Model selector tabs** (above KPI row): `All Models` · `H60 V1` · `H60 V3` · `H300`
Symbol selector: `All` · `BTC` · `SOL` · `ETH`

All charts and KPIs update when model or symbol selection changes.

---

**Cumulative P&L Curve:**
Recharts AreaChart. X = time. Y = cumulative realized NE_t (USDC).
One line per model (colors from §3.2). Legend with toggle.
Hover tooltip: date, per-model cumulative value at that point.

---

**Rolling-N Accuracy Chart:**

```
N:      [50★]       ← N selector, default 50[1]

   66% ┤ ╭─╮
   60% ┤─╯  ╰─╮         ← gate threshold dashed line at 51.5%
   54% ┤       ╰─╮──╮
   50% ┤ ░░░░░░░░░╰──╯  ← red shading below threshold
   44% ┤
        Jan 28  Feb 4  Feb 11 ...

   Gate Status:  H300  50.0%  ↓  ❌ FAIL (−1.5pp)
```

**Variance Context Card (shown only when gate fails):**
```
┌──────────────────────────────────────────────────────────────┐
│  ⚠️  Gate failed — but is this signal or variance?           │
│                                                              │
│  Rolling-50 at 50.0% is within expected sampling variance   │
│  for a 58.4% true accuracy model.                           │
│                                                              │
│  z = −1.13  -   p = 0.258  (two-tailed)                      │
│  "1 in 3.9 chance of seeing this or worse by random alone"  │
│                                                              │
│  Not yet evidence of real edge erosion. Watch next 2 batches.│
└──────────────────────────────────────────────────────────────┘
```
Data from `variance_context` in `GET /api/performance/rolling` response.

---

**Gate Status Cards (one per model, below rolling chart):**
- Rolling-50 value, distance to 51.5% threshold
- PASS (teal) / FAIL (red)
- 7-day sparkline of rolling-50
- Trend arrow ↑ / ↓ / →

---

**Derived Metrics Panel (bottom of Overview):**

| Metric | Value | Notes |
|--------|-------|-------|
| Realized NE_t/trade | `+$0.64` | Primary — from actual outcomes |
| Theoretical NE_t/trade | `+$0.31` | Comparison only — p_model − fee − p_market |
| Theory vs Realized Delta | `+$0.33` | Model under-estimated edge |
| Break-even accuracy | `62.6%` | At this p_market + fee level |

---

### "What If" Deployment Simulator

Given a real stake size (amount per trade), simulates historical deployment.
*   **Input:** Stake size slider (USDC)
*   **Outputs:** Cumulative dollar P&L, max dollar drawdown, probability-of-ruin curves.
*   **Purpose:** Connects paper trading data directly to real-world deployment decisions.

### By Symbol Tab

Three-column layout (BTC / SOL / ETH). Each column:
- Accuracy with CI badge
- W/L count and ratio bar
- Realized NE_t total and per-trade
- Mini rolling-50 sparkline
- z-score and p-value
- BTC column highlighted in amber if carrying disproportionate share of NE_t

---

### By Contract Tab

Side-by-side: 300s vs 900s for each model.
Grouped bar chart comparison (accuracy, NE_t/trade, trade count).

**Below the chart — 300s vs 900s Agreement Analysis panel:**
```
Matched prediction events (both 300s + 900s fired): 243

 Same outcome:     159/243  (65.4%)
   Both correct:  89
   Both wrong:    70

 Different outcome: 84/243  (34.6%)
   300s wrong, 900s right: 53
   300s right, 900s wrong: 31

 When disagreeing: 900s correct 63.1% of the time (53/84)

 ✅ Suppression verdict: 300s suppression is CORRECT
    Suppression has saved X NE_t since activation.
    [View suppression effectiveness →]
```
Data from `GET /api/performance/by-contract` → `agreement_analysis`.
Link to `GET /api/performance/suppression-effectiveness` panel.

---

### By Hour Heatmap Tab

24×7 grid. Rows = UTC hours (0–23). Columns = days of week (Sun–Sat).
Cell color: green (high accuracy) → neutral (50%) → red (low accuracy).
Cells with n < 5 shown gray with `*` marker (insufficient data).

Click a cell → applies UTC hour + day-of-week filter to Prediction Explorer.
Timezone toggle (§4.1) converts display labels but filter is always stored in UTC.

Below grid: daytime (04:00–21:00 UTC) vs overnight (21:00–04:00 UTC) summary.

---

### By Divergence Tab

Divergence buckets: `0.00–0.02` / `0.02–0.05` / `0.05–0.10` / `0.10+`

Grouped bar chart (Recharts): per bucket × (accuracy, NE_t/trade, n).
`0.10+` bucket highlighted in amber — the key watch metric.

Table below chart:

| Bucket | N | Accuracy | CI | Realized NE_t/trade | Theoretical (ref) |
|--------|---|----------|----|---------------------|-------------------|
| 0.10+ | 23 | 65.2% | [44%, 82%] | +$1.84 | +$0.91 |

---

### By p_market Range Tab

Buckets: `0.35–0.45` / `0.45–0.50` / `0.50–0.55` / `0.55–0.65`
Same grouped bar + table structure as By Divergence tab.

---

### Streaks & Drawdown Tab

- Current streak counter: `🔴 L3` or `🟢 W7`, animated
- Historical max win / max loss streak
- Max drawdown: consecutive-loss timeline chart
- Batch accuracy variance chart: 50-trade batches chronologically,
  with 5th / 95th percentile band showing expected variance range

---

## 10. Section 5 — Model Comparison

**Purpose:** Multi-model side-by-side analysis with agreement and ensemble simulation.

### Accuracy Timeline (All Models Overlaid)

Line chart. X = time (weekly buckets). Y = rolling accuracy.
One line per model version (colors from §3.2). Legend with toggle.
Gate threshold dashed line at 51.5%.

**Annotation layer** (if `settings.annotation_layer = true`):
Vertical dashed lines at deployment events from Section 9 timeline.
Each line has a small label: "300s suppressed", "UTC blackout added", etc.

---

### NE_t/Trade Over Time

Line chart. X = week. Y = realized NE_t/trade.
Shows whether edge is improving or decaying across model generations.
Zero line highlighted — crossing below zero is a critical event.

---

### Direction Bias Panel

Grouped stacked bar per symbol per model.
Shows `up% vs down%` of all predictions.
Highlights significant asymmetry (> 60% in one direction).

---

### p_model Distribution

Histogram per model (overlaid, 30% opacity each).
X = p_model (0–1), bins of 0.025. Y = count.
Shows whether predictions cluster near 0.5 (weak signal)
or spread toward extremes (stronger signal).

---

### Divergence Distribution

`|p_model − p_market|` histogram per model (overlaid).
Shows which model generates more extreme divergences.

---

### Agreement Analysis

When do H60 V3 and H300 agree on direction?

```
Agreement rate: 62.0% of matched decisions

  Agree + both correct:   38.1%
  Agree + both wrong:     23.9%
  Disagree:               38.0%

Accuracy on agreement:    61.4%
Accuracy on disagreement: 49.2%
```

Stacked bar: agree-correct / agree-wrong / disagree-900s-right / disagree-300s-right.

---

### Cross-Symbol Prediction Correlation

When a model fires on BTC, SOL, and ETH simultaneously, are the outcomes correlated?
Displays a pairwise correlation matrix of prediction outcomes (win/loss across symbols).
*   **Purpose:** Helps answer: "If BTC wins, does SOL win too, or are they independent?"
*   **Display:** Matrix with correlation coefficients (Pearson r) and interpretation notes.

### Ensemble Simulation Panel

"Only bet when X of N models agree"

Input: slider 1–3 (agreement threshold), default 2 of 2 (H60 V3 + H300).
Shows live simulation from local prediction log (computed client-side):
- How many trades would have been taken
- Simulated accuracy + CI
- Simulated realized NE_t

Useful for evaluating whether multi-model consensus improves edge.

---

### Model Lineage View

Horizontal timeline (D3):

```
H60 V1           H60 V2           H60 V3           H300
Jan 2026         Feb 2026         Mar 2026         Jan 2026

feat: 12         feat: 18         feat: 22         feat: 18
AUC:  0.541      AUC:  0.558      AUC:  0.571      AUC:  0.587
                 + spread_t       + vpin_50
                 + vpin_15        + mid_price_dev   Separate model
                 UTC suppress.    Adverse gate
```

Click any version node → expands to full parameter diff card.
Two-column diff table: added rows (green), removed rows (red), changed rows (amber).

---

## 11. Section 6 — p_market / EV Analysis

**Purpose:** Deep analysis of Polymarket market probabilities and edge structure.

### p_market Time Series

Line chart per symbol (toggle). X = time. Y = p_market (0–1).
Secondary overlay: 60-period rolling mean.
Anomaly shading: orange background when outside 0.40–0.60.

---

### p_model vs p_market Scatter

Scatter plot. X = p_market. Y = p_model.
Each dot = one resolved prediction.
Color: teal = correct, red = incorrect.
Size = stake (all $10 standard).
Diagonal reference line (no divergence).
Regression line overlay with R² shown.

Quadrant labels:
- Top-left: model high / market low → up bets taken
- Bottom-right: model low / market high → down bets taken
- Other quadrants: inversion zone (model and market agree)

---

### Divergence Histogram

`p_model − p_market` signed distribution (one histogram per model, overlaid).
Positive = model more bullish than market.
Shows asymmetry in how each model diverges.

---

### Accuracy vs Threshold Comparison

Two panels side by side:
1. Accuracy for trades where `p_model > 0.50` vs `p_market > 0.50`
2. The core NE_t finding: which threshold tracks actual accuracy better?

This is the visualization of the key discovery: the model predicts direction
better when evaluated against p_market divergence, not raw p_model.

---

### EV by Divergence Bucket

Table + grouped bar: same as Section 4 By Divergence tab,
but with explicit comparison between realized and theoretical NE_t per bucket.
Both columns always shown. Theoretical clearly labeled.

---

### Calibration Curves (D3)

Two reliability diagrams rendered side by side:

**Model Calibration:** X = p_model bucket, Y = actual win rate.
**Market Calibration:** X = p_market bucket, Y = actual win rate.

Each point sized by count. Perfect calibration = diagonal.
Deviation from diagonal shown as colored arrows per point.

Reveals whether the model is overconfident, underconfident, or well-calibrated —
and whether the market price itself is exploitable.

---

### Threshold Inversion Detector

Table: anomalous events where `p_model > p_market` but model bet down (or vice versa).
Columns: timestamp, symbol, model, p_model, p_market, direction_bet, outcome.
Signals bugs in the prediction → trade pipeline logic.

---

## 12. Section 7 — Price & Market Overlay

**Purpose:** Interactive charting of price, OFI signals, predictions, and trade windows.

### Controls

```
[BTC ●]  [SOL]  [ETH]          ← symbol tabs
[1m]  [5m ●]  [15m]  [1h]      ← interval selector
[Last 1h]  [6h ●]  [24h]  [7d]  [Custom]  ← time range

Overlays: [✓ p_market]  [✓ Predictions]  [✓ Trades]
          [✓ Contract Windows]  [Volume]  [Spread]
          [OFI]  [MLOFI]  [mid_price_dev_30d]
          [✓ System Uptime]  [✓ Deployments]
```

### Panes (lightweight-charts multi-pane)

**Pane 1 — Price (primary)**
- Candlestick chart from `/api/parquet/price`
- p_market as right-axis line (0–1, dashed, teal)
- Prediction markers:
  - ▲ teal = up correct
  - ▲ red = up incorrect
  - ▲ gray = up unresolved
  - ▲ amber = up suppressed
  - ▼ same color coding for down bets
- Trade open: circle. Trade close: square. Connected by thin line.
- Contract windows: shaded spans (open → close timestamp), color by outcome
- Bid-ask spread: translucent band around mid price
- **System uptime overlay:** thin colored band along top edge of chart.
  Green = system running. Red = system down/offline. Gray = unknown.
  Helps distinguish prediction gaps from quiet market periods.
- **Deployment event lines:** vertical dashed lines from Section 9 timeline
  (only when `annotation_layer` setting is ON)

**Pane 2 — Volume** (collapsible)
Standard volume bars, colored by candle direction.

**Pane 3 — OFI / MLOFI** (collapsible)
Dual-line: `ofi` (raw) and `mlofi` (MAD-normalized).
Zero reference line.

**Pane 4 — mid_price_dev_30d** (collapsible)
Value over time. EWM mean line. ±1 std band shading.
±2 std horizontal lines ("extreme deviation").

### Interactions

- Crosshair synchronized across all panes
- Click prediction marker → opens prediction detail drawer (same as Section 2)
- Hover contract window → tooltip: symbol, duration, direction, outcome, realized NE_t
- Data gap indicator: if parquet data is missing for a range, show a hatched
  gray band with label "No data"

### Data Sources

- OHLCV: `GET /api/parquet/price?symbol&interval&from_ms&to_ms`
- Predictions overlay: `GET /api/predictions?symbol&from_ms&to_ms&page_size=500`
- Feature overlays: `GET /api/parquet/features?symbol&features=ofi,mlofi,mid_price_dev_30d&from_ms&to_ms`

---

## 13. Section 8 — Feature Analysis

**Purpose:** Feature importance, live values, distribution shift, and staleness monitoring.

### Sub-tabs

`Importance` · `Live Values` · `Time Series` · `Distribution Shift`
· `Correlation Matrix` · `mid_price_dev_30d` · `Missingness`

---

### Importance Tab

Horizontal bar chart (Recharts). Sorted descending by gain importance.
Model version selector (V1 / V3 / H300) — each has its own chart.
Toggle: Gain / Split / SHAP (SHAP tab shows "Not available" if `shap_available: false`).

---

### Live Values Tab

Most recent feature values for selected model + symbol.
Table: feature name | current value | training mean | training std | z-score | staleness.

Staleness indicator:
- 🟢 < 30s — "Fresh"
- 🟡 30–120s — "Stale"
- 🔴 > 120s — "Missing"

Rows sorted by importance rank (most important features at top).

---

### Time Series Tab

Feature selector dropdown (all feature names from model's feature list).
Date range picker. Symbol selector.
Recharts line chart: feature value over time.
Overlay: EWM mean (dashed), ±1 std band (shaded).

---

### Distribution Shift Tab

For each feature: two histograms side by side.
Left: training distribution (gray). Right: live distribution (teal, last 7 days).

PSI badge above each pair:
- `PSI < 0.10` → green "Stable"
- `PSI 0.10–0.25` → amber "Monitor"
- `PSI > 0.25` → red "Shift Detected"

Sorted by PSI descending (most shifted first).

---

### Correlation Matrix Tab

D3 heatmap. Color scale: −1 (red) → 0 (neutral) → +1 (teal).
Click a cell → side panel shows scatter plot of that feature pair.

---

### mid_price_dev_30d Panel Tab

Dedicated panel for the EWM normalization feature:
- Current value (large display number)
- EWM mean and std
- Last updated timestamp + staleness status
- Delta: current − expected
- 7-day time series chart
- Historical distribution histogram

---

### Missingness Tab

Table: feature | total | non-null | null count | null% | last seen non-null.
Sorted by null% descending.
`null% > 5%` → amber flag. `null% > 20%` → red flag.

---

## 14. Section 9 — Training & Architecture History

**Purpose:** Model registry, parameter diffs, training data provenance,
and annotated deployment timeline.

### Model Version Registry

Card grid, one card per version (H60 V1, H60 V2, H60 V3, H300).

Each card expandable — shows:
- Version label + deployment date
- Feature list (pill list, scrollable if long)
- LightGBM hyperparams table
- Training window dates + row counts per symbol
- EWM params
- AUC + accuracy on train/val/test
- `⚠️ LEAKAGE FLAG` if test accuracy > 62%
- Gate config at time of training

**Version Diff View:**
"Compare" button above grid — select any two versions.
Side-by-side diff table rendered with `react-diff-viewer`.
Added rows green, removed red, changed amber.

---

### Training Data Panel

Per version, table:
- Symbol | Start date | End date | Row count | Temporal gap | Leakage flag

---

### Suppression & Gate History

Timeline table:
- Date | Change type | Description | Models affected

This is the canonical record of all interventions:
- UTC blackout rule added
- 300s contract suppression activated
- Sanderink threshold changes
- EWM bug fix

---

### Annotated Deployment Timeline

Horizontal D3 timeline. X = date.
Events as labeled dots, color by type:
- Deploy (teal)
- Model change (purple)
- Suppression added (amber)
- Bug fix (blue)
- Gate failure milestone (red)
- Gate pass milestone (green)

Click any event → detail drawer with full change notes.

**The events from this timeline feed the annotation layer in Section 7
(price chart) and Section 5 (model comparison) when the annotation
layer setting is ON.**

---

## 15. Section 10 — Statistical Tools Panel

**Purpose:** In-browser calculators. No server calls. All computed via `jstat`.

All tools are in the `utils/stats.ts` module. Section 10 is just a UI wrapper.

---

### Tool 1 — Confidence Interval Calculator

Inputs: N (trades), accuracy (%).
Outputs: Wilson score 95% CI, exact binomial 95% CI, normal approximation.
Visual: number line showing CI range with 50% and break-even marked.

---

### Tool 2 — Significance Test (z-test)

Inputs: N, observed accuracy (%), null hypothesis (default 50%).
Outputs: z-score, p-value (one-tailed), "significant at α=0.05?" badge.
Visual: standard normal curve with rejection region shaded, z-score marked.

---

### Tool 3 — Sample Size Calculator

Inputs: true accuracy to detect (%), desired confidence (90/95/99%), power (80/90%).
Output: required N.
Visual: slider — drag accuracy to see how N changes in real time.

---

### Tool 4 — Binomial Streak Probability

Inputs: streak type (W/L), streak length, assumed win rate.
Output: probability of seeing this streak or longer by chance.
Visual: "1 in X" framing + probability bar.

---

### Tool 5 — Batch Variance Simulator

Inputs: true accuracy (%), batch size (default 50), simulated batches (default 1000).
Output: histogram of simulated batch results with 5th/95th percentile lines.
Calibration tool: shows how much variance is expected in a 50-trade window.
**This is the context behind every gate failure card — demonstrates that
50.0% in a 50-trade batch is not surprising for a 58% true accuracy model.**

---

### Tool 6 — Break-Even Calculator

Inputs: p_market, fee, spread (optional).
Formula: `p_break_even = p_market + fee + spread`
Output: minimum model accuracy to be NE_t-positive.
Visual: gauge showing current model accuracy vs break-even line.

---

### Tool 7 — Kelly Stake Calculator

Inputs: accuracy (win rate), p_market.
```
b = (1 − p_market) / p_market
f_full_kelly  = (b × p − q) / b
f_half_kelly  = f_full / 2
f_quarter_kelly = f_full / 4  (system default)
```
Visual: stake fraction dial with warning if full Kelly > 0.50.
Prominent warning if accuracy input < break-even.

---

## 16. Section 11 — Alerts & Anomaly Panel

**Purpose:** Centralized alert log with configurable thresholds and anomaly history.

### Alert Log Table

Columns: Timestamp | Type | Severity | Model | Symbol | Message | Resolved?

Severity icons:
- 🔵 INFO
- 🟡 WARN
- 🔴 CRITICAL

Filter bar: severity, type, model, symbol, time range, resolved/unresolved.

---

### Threshold Configuration Drawer

Gear icon in panel header opens settings drawer:
- Accuracy alert floor (default 50%)
- Coverage drop floor (predictions/hour, default 2)
- PSI monitor threshold (default 0.10)
- PSI shift threshold (default 0.25)
- p_market anomaly range (default 0.40–0.60)
- Agreement flip minimum period (default 24h)

Changes update `settingsStore` immediately. Alerts engine reads from settings.

---

### Watchlist & Active Subscriptions

UI for configuring custom, hyper-specific alert subscriptions beyond the generic global thresholds.
*   **Example:** "Alert me when H300 BTC rolling-50 drops below 55%".
*   **Storage:** Stores user-configured subscriptions in `settingsStore` under `active_subscriptions`.
*   **Purpose:** Allows per-model-per-symbol custom thresholds.

### Suppression Effectiveness Panel

Dedicated sub-section. Shows live `GET /api/performance/suppression-effectiveness`
data for each active rule:

```
┌─ CONTRACT_MISMATCH (300s trades) ─────────────────────────────┐
│  Suppressed: 89 trades  -   Resolved: 72                       │
│  Hypothetical accuracy: 34.7%  (-$0.83/trade)                 │
│  Hypothetical NE_t if executed: -$59.76                       │
│                                                               │
│  ✅ CORRECT — Suppression saved +$59.76 NE_t                  │
│  (72 resolved trades since rule activation)                   │
└───────────────────────────────────────────────────────────────┘

┌─ UTC_BLACKOUT (H60, 21:00–04:00 UTC) ─────────────────────────┐
│  Suppressed: 134 trades  -   Resolved: 118                     │
│  Hypothetical accuracy: 28.0%  (-$1.41/trade)                 │
│  Hypothetical NE_t if executed: -$166.38                      │
│                                                               │
│  ✅ CORRECT — Suppression saved +$166.38 NE_t                 │
└───────────────────────────────────────────────────────────────┘
```

Updated every time the `/api/performance/suppression-effectiveness` endpoint is polled
(every 60 seconds). The `decision` field drives the ✅/❌/⚠️ badge.

---

### Gate Status Change History

Sub-table: all historical events where rolling-50 crossed 51.5%.
Columns: Timestamp | Direction (↑ above / ↓ below) | Value at crossing | Current value.

---

### Alert Timeline Chart

Recharts stacked bar: alert count per day, stacked by severity.
Shows whether alert frequency is increasing over time.

---

## 17. Section 12 — Raw Log Explorer

**Purpose:** Low-level record browser for debugging, audit, and data extraction.

### Search Bar

```
[Type: All ▾]  [Symbol ▾]  [Time range ▾]  [Search by ID or prediction_id...]
```

### Result List (virtualized, TanStack Virtual)

Each result:
- Record type badge (Prediction / Trade / Resolution)
- ID
- Timestamp
- Symbol + model
- One-line summary

### Full JSON Viewer (right panel, 60% width)

`react-json-view-lite`:
- Collapsible nested keys
- Syntax highlighted
- Copy (full record or selected path)
- Download as `.json`

### Event Chain View

When a prediction record is selected, chain view appears below JSON viewer:

```
[Prediction abc123]  →  [Trade 45]  →  [Resolution 45]
ts: 12:03:41            ts: 12:04:12   ts: 12:08:58
p_model: 0.578          stake: $10     correct: true
direction: UP           direction: UP  realized_net: +$0.357
```

Click any chain node → switches JSON viewer to that record.

### Diff Viewer

"Compare" mode: select two records of the same type.
Side-by-side diff with `react-diff-viewer`.

### Download Controls

- "Download filtered set" → CSV (max 10,000 rows, respects filters)
- "Download as JSON" → JSON array

### Record Count Footer

"Showing X of Y predictions | Z trades | W resolutions in database"
Each count is a clickable filter shortcut.

---

## 18. Crossfilter Design

Sections 2 (Prediction Explorer), 3 (Trade Explorer), and 4 (Performance Dashboard)
share `filterStore`. Changing any filter in any panel updates all three.

**Clickable chart elements that apply filters:**

| Element | Filter Applied |
|---------|---------------|
| Heatmap cell (Section 4) | UTC hour + day of week |
| By-Symbol card (Section 4) | symbol |
| By-Contract tab bar (Section 4) | contract_duration |
| Divergence bucket bar (Section 4) | divergence_min + divergence_max |
| Model badge in any table | model |
| Prediction marker in chart (Section 7) | prediction_id (shows that prediction) |

**Active Filters Bar** (sticky, below topbar in affected sections):
When any non-default filter is active, a bar appears showing dismissible tags:
```
Filters active:  [Model: H300 ×]  [Symbol: BTC ×]  [Last 24h ×]  [Clear all]
```

---

## 19. URL State Persistence

Filters from `filterStore` serialize to the URL query string so views can be
bookmarked and shared.

```
/dashboard?model=h300&symbol=BTCUSDT&from_ms=1743462000000&contract_duration=900
```

**Rules:**
- Only non-default filter values are included in the URL
- On page load, initialize `filterStore` from URL params (fallback to defaults)
- URL updates on every filter change (use `replaceState`, not `pushState`,
  to avoid polluting browser history)
- Section selection is also in the URL: `/dashboard?section=4&...`
- React Router v6 `useSearchParams` for reading and writing

---

## 20. WebSocket Integration

**Connection:** `ws://localhost:8765/ws/live?token=<credentials>`

**`websocket.ts` responsibilities:**
- Maintain connection, reconnect with exponential backoff
- Parse incoming messages and dispatch to `liveStore`
- Expose `onStatus`, `onPredictions`, `onTrades`, `onAlerts` subscription hooks
- Send `{type: "ping"}` every 30s; expect `{type: "pong"}` within 5s
- If no pong: close and reconnect

**Components that subscribe to live updates:**
- `Topbar` — live dot, alert count
- `Section1/ContainerCard` — last prediction timestamp, predictions/hour
- `Section1/OpenTradesBanner` — open trade count
- `Section4/KPICards` — trade count, NE_t (append new resolved trades)
- `Section4/RollingChart` — append new trades to rolling series
- `Section11/AlertLog` — prepend new alerts

**All live updates are additive (append-only) — never replace the full dataset.**
Historical data loads via REST on mount. WebSocket only delivers deltas.

---

Continuing directly from Section 21:

***

## 21. File & Directory Structure

```
polymarket-ofi-dashboard/
├── package.json
├── vite.config.ts
├── tailwind.config.ts
├── tsconfig.json
├── index.html
└── src/
    ├── main.tsx                        # React root, router, WebSocket init
    ├── App.tsx                         # SPA shell: topbar + sidebar + main pane
    │
    ├── store/
    │   ├── liveStore.ts                # Zustand: WebSocket state
    │   ├── filterStore.ts              # Zustand: shared crossfilter state
    │   └── settingsStore.ts            # Zustand: timezone, theme, thresholds
    │
    ├── api/
    │   ├── client.ts                   # Axios base client, auth injection, retry
    │   ├── websocket.ts                # WS manager, reconnect, subscriptions
    │   └── endpoints.ts                # All endpoint call functions (typed)
    │
    ├── sections/
    │   ├── 01-LiveStatus/
    │   │   ├── index.tsx
    │   │   ├── ContainerCard.tsx
    │   │   ├── DataPipelinePanel.tsx
    │   │   ├── EWMTable.tsx
    │   │   ├── ExecutionFunnel.tsx     # NEW — gate drop-off funnel chart
    │   │   ├── GateStatusMini.tsx
    │   │   └── SuppressionPills.tsx
    │   │
    │   ├── 02-PredictionExplorer/
    │   │   ├── index.tsx
    │   │   ├── PredictionTable.tsx
    │   │   ├── PredictionDrawer.tsx
    │   │   └── FilterBar.tsx
    │   │
    │   ├── 03-TradeExplorer/
    │   │   ├── index.tsx
    │   │   ├── TradeTable.tsx
    │   │   ├── TradeDrawer.tsx
    │   │   ├── OpenTradesBanner.tsx
    │   │   └── NetTooltip.tsx          # NE_t breakdown tooltip
    │   │
    │   ├── 04-PerformanceDashboard/
    │   │   ├── index.tsx               # Sub-tab router
    │   │   ├── Overview.tsx
    │   │   ├── BySymbol.tsx
    │   │   ├── ByContract.tsx          # Includes agreement analysis panel
    │   │   ├── ByHourHeatmap.tsx
    │   │   ├── ByDirection.tsx
    │   │   ├── ByDivergence.tsx
    │   │   ├── ByPmarketRange.tsx
    │   │   ├── StreaksDrawdown.tsx
    │   │   ├── GateStatusCard.tsx
    │   │   ├── VarianceContextCard.tsx # Gate failure variance explanation
    │   │   ├── KPICard.tsx
    │   │   └── DerivedMetrics.tsx
    │   │
    │   ├── 05-ModelComparison/
    │   │   ├── index.tsx
    │   │   ├── AccuracyTimeline.tsx
    │   │   ├── NETOverTime.tsx
    │   │   ├── DirectionBias.tsx
    │   │   ├── PmodelDistribution.tsx
    │   │   ├── DivergenceDistribution.tsx
    │   │   ├── AgreementAnalysis.tsx
    │   │   ├── EnsembleSimulator.tsx
    │   │   └── ModelLineage.tsx        # D3 version timeline
    │   │
    │   ├── 06-PMarketEV/
    │   │   ├── index.tsx
    │   │   ├── PmarketTimeSeries.tsx
    │   │   ├── ScatterPlot.tsx
    │   │   ├── DivergenceHistogram.tsx
    │   │   ├── ThresholdComparison.tsx
    │   │   ├── EVByDivergence.tsx
    │   │   ├── CalibrationCurve.tsx    # D3 reliability diagram
    │   │   └── InversionDetector.tsx
    │   │
    │   ├── 07-PriceOverlay/
    │   │   ├── index.tsx
    │   │   ├── CandlestickChart.tsx    # lightweight-charts wrapper
    │   │   ├── PredictionMarkers.tsx
    │   │   ├── ContractWindowSpans.tsx
    │   │   ├── OFIPane.tsx
    │   │   ├── MidPriceDevPane.tsx
    │   │   └── UptimeOverlay.tsx       # System uptime band on chart
    │   │
    │   ├── 08-FeatureAnalysis/
    │   │   ├── index.tsx
    │   │   ├── ImportanceChart.tsx
    │   │   ├── LiveValuesTable.tsx
    │   │   ├── FeatureTimeSeries.tsx
    │   │   ├── DistributionShift.tsx
    │   │   ├── CorrelationMatrix.tsx   # D3 heatmap
    │   │   ├── MidPriceDevPanel.tsx
    │   │   └── MissingnessTable.tsx
    │   │
    │   ├── 09-TrainingHistory/
    │   │   ├── index.tsx
    │   │   ├── ModelCard.tsx
    │   │   ├── VersionDiff.tsx
    │   │   ├── TrainingDataTable.tsx
    │   │   └── SuppressionHistory.tsx
    │   │
    │   ├── 10-StatisticalTools/
    │   │   ├── index.tsx
    │   │   ├── CICalculator.tsx
    │   │   ├── ZTestTool.tsx
    │   │   ├── SampleSizeCalc.tsx
    │   │   ├── StreakProbability.tsx
    │   │   ├── BatchVarianceSimulator.tsx
    │   │   ├── BreakevenCalc.tsx
    │   │   └── KellyCalc.tsx
    │   │
    │   ├── 11-AlertsPanel/
    │   │   ├── index.tsx
    │   │   ├── AlertTable.tsx
    │   │   ├── AlertTimelineChart.tsx
    │   │   ├── SuppressionEffectiveness.tsx  # NEW — ongoing suppression validation
    │   │   ├── GateChangeHistory.tsx
    │   │   └── ThresholdSettings.tsx   # Settings drawer
    │   │
    │   └── 12-RawLogExplorer/
    │       ├── index.tsx
    │       ├── SearchBar.tsx
    │       ├── ResultList.tsx
    │       ├── JSONViewer.tsx
    │       ├── EventChainView.tsx
    │       └── DiffViewer.tsx
    │
    ├── components/
    │   ├── layout/
    │   │   ├── Sidebar.tsx
    │   │   ├── Topbar.tsx
    │   │   └── ActiveFiltersBar.tsx    # Dismissible filter tags strip
    │   ├── ui/
    │   │   ├── KPICard.tsx             # Animated counter card
    │   │   ├── StatusDot.tsx           # Live green/amber/red dot
    │   │   ├── DataTable.tsx           # TanStack Table wrapper
    │   │   ├── RecordDrawer.tsx        # Side drawer with JSON viewer
    │   │   ├── ModelBadge.tsx          # Colored version badge
    │   │   ├── SymbolChip.tsx          # BTC/SOL/ETH colored chip
    │   │   ├── OutcomePill.tsx         # correct/incorrect/unresolved
    │   │   ├── DurationPill.tsx        # 300s/900s pill
    │   │   ├── SuppressedBadge.tsx
    │   │   ├── EmptyState.tsx          # Animated icon + warm message
    │   │   ├── SkeletonLoader.tsx      # Shimmer loading state
    │   │   ├── ErrorState.tsx          # Retry button + message
    │   │   ├── Tooltip.tsx
    │   │   ├── Popover.tsx
    │   │   └── CopyButton.tsx
    │   └── charts/
    │       ├── RollingAccuracyChart.tsx
    │       ├── CumulativePnLChart.tsx
    │       ├── HourHeatmap.tsx         # D3 24×7 grid
    │       ├── CalibrationCurve.tsx    # D3 reliability diagram
    │       ├── CorrelationMatrix.tsx   # D3 heatmap
    │       ├── ModelLineage.tsx        # D3 version timeline
    │       ├── DeploymentTimeline.tsx  # D3 annotated timeline
    │       ├── FunnelChart.tsx         # Execution gate funnel
    │       └── Sparkline.tsx           # Mini inline sparkline
    │
    ├── utils/
    │   ├── stats.ts                    # Wilson CI, z-test, Kelly, PSI, batch sim
    │   ├── format.ts                   # Timestamps (UTC/local), dollar, percentage
    │   ├── net.ts                      # compute_realized_net() — mirrors backend
    │   ├── urlState.ts                 # filterStore ↔ URL query string sync
    │   └── colors.ts                   # Model color mapping, outcome colors
    │
    └── types/
        ├── prediction.ts               # PredictionRecord
        ├── trade.ts                    # TradeRecord, OpenTrade
        ├── resolution.ts               # ResolutionRecord
        ├── status.ts                   # StatusResponse and sub-types
        ├── performance.ts              # All /api/performance/* response types
        ├── features.ts                 # FeatureDistribution, LiveFeatureEntry, etc.
        ├── model.ts                    # ModelRegistryEntry, ModelDiff
        ├── alerts.ts                   # AlertRecord, AlertType, AlertSeverity
        └── filters.ts                  # FilterState, shared query param types
```

---

## 22. Performance Requirements

| Metric | Target |
|--------|--------|
| Initial cold load | < 3s |
| Section navigation (cached data) | < 100ms |
| Prediction table render (10k rows, virtual) | < 100ms |
| Chart update on filter change | < 200ms |
| WebSocket status refresh | 5s interval |
| Candlestick render (10k candles, lightweight-charts) | < 150ms |
| Calibration/heatmap D3 render | < 300ms |

**Implementation rules for hitting these targets:**

- Server-side pagination on all tables. Client holds current page +
  prefetched next page only. Never load all records into memory.
- Heavy computation (rolling accuracy, divergence bucketing, ensemble
  simulation, batch variance simulation) runs in **Web Workers** to prevent
  main-thread blocking.
- Chart data is computed server-side and returned as pre-aggregated arrays
  from `/api/performance/*` endpoints. The frontend does not re-aggregate
  raw trade arrays for charts — it renders what the API returns.
- Use `content-visibility: auto` on all off-screen table rows and chart panels.
- Recharts components wrapped in `React.memo` with stable prop references.
- D3 charts use `useRef` + imperative updates, never full re-renders on data change.
- lightweight-charts uses its native `update()` API for live candle appends —
  never replace the full series.
- All API calls use React Query (TanStack Query) for caching, deduplication,
  and background refresh. Default stale time: 30s for performance data, 5s
  for status data.

---

## 23. Accessibility & Quality

### Accessibility (WCAG AA)

- Semantic HTML throughout: `<header>`, `<nav>`, `<main>`, `<section>`,
  `<table>`, `<button>`. No `<div>` where a semantic element exists.
- One `<h1>` per page (section title). Heading hierarchy respected.
- WCAG AA contrast on all text/surface combinations in both light and dark mode.
  Verify the teal primary (`#2dd4bf`) specifically — it can fail contrast
  at small sizes on dark surfaces. Use at 14px+ only.
- Keyboard-navigable tables: arrow keys for row navigation, Enter to open
  drawer, Escape to close.
- All icon-only buttons have `aria-label`. No tooltips that only appear on hover
  for critical information.
- Focus indicators visible on all interactive elements (base CSS `:focus-visible`).
- All charts include `<title>` and `<desc>` SVG elements for screen readers.
- `prefers-reduced-motion`: disable shimmer animations and KPI counter
  animations if set.
- Touch targets: 44×44px minimum for all interactive elements.

### Loading States

Every data-dependent panel shows a skeleton loader (shimmer) while loading.
Skeleton mirrors the real layout shape (table rows, chart area, KPI cards).

```css
@keyframes shimmer {
  0%   { background-position: -200% 0; }
  100% { background-position:  200% 0; }
}
.skeleton {
  background: linear-gradient(
    90deg,
    var(--color-surface-offset) 25%,
    var(--color-surface-dynamic) 50%,
    var(--color-surface-offset) 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
  border-radius: var(--radius-sm);
}
```

### Error States

Every API-dependent panel has an error state with:
- Clear message ("Could not load performance data")
- Retry button
- Timestamp of last successful load

Never show raw error codes or stack traces to the user.

### Empty States

Never just "No data." Every empty state has:
- A warm, specific message ("No trades matching these filters")
- A relevant action ("Clear filters" or "Adjust time range")
- An icon

### NE_t Display Rules (enforced across all components)

These rules must be followed in every component that renders NE_t:

1. Always labeled "Realized NE_t" — never just "NE_t" or "Net" alone
2. Always shown in dollars with sign: `+$0.64` / `−$1.48`
3. Green for positive, red for negative, muted for null/zero
4. Null (unresolved) displayed as `—` never `$0.00`
5. Tooltip on hover shows the full `realized_net_breakdown` decomposition
6. Theoretical NE_t may only appear in comparison columns, always labeled
   "Theoretical (comparison only)" in muted text

---

## 24. Development Phases

### Phase 1 — Foundation (Week 1–2)

Goal: Working shell with the three most-used sections fully functional.

- [ ] Vite + React + TypeScript + Tailwind setup
- [ ] Design system tokens and base CSS in `style.css`
- [ ] App shell: topbar, sidebar, routing skeleton
- [ ] `filterStore`, `liveStore`, `settingsStore` wired
- [ ] `api/client.ts` with auth, retry, error handling
- [ ] `api/websocket.ts` with reconnect logic
- [ ] Timezone toggle (UTC/Local) in topbar, applied globally via `format.ts`
- [ ] URL filter state persistence (`urlState.ts`)
- [ ] `utils/net.ts` — `compute_realized_net()` implemented and tested
- [ ] **Section 1 (Live Status)** — fully functional, WebSocket-driven
- [ ] **Section 2 (Prediction Explorer)** — table, all filters, drawer, CSV export
- [ ] **Section 3 (Trade Explorer)** — table, filters, drawer, NE_t tooltip,
      open trades banner
- [ ] Shared components: `DataTable`, `RecordDrawer`, `ModelBadge`, `SymbolChip`,
      `OutcomePill`, `EmptyState`, `SkeletonLoader`, `ErrorState`

**Phase 1 done when:** Section 2 shows every prediction field, Section 3 shows
every trade with correct realized NE_t, and the Live Status page auto-refreshes
via WebSocket.

---

### Phase 2 — Analytics Core (Week 3–4)

Goal: Performance Dashboard fully operational.

- [ ] **Section 4 Overview tab** — KPI cards, cumulative P&L curve, rolling-N chart
- [ ] `VarianceContextCard` — shown on gate failure with p-value interpretation
- [ ] **Section 4 By Symbol tab**
- [ ] **Section 4 By Contract tab** — includes agreement analysis panel
- [ ] **Section 4 By Hour Heatmap tab** — D3 24×7 grid, clickable cells
- [ ] **Section 4 By Divergence tab**
- [ ] **Section 4 By p_market Range tab**
- [ ] **Section 4 Streaks & Drawdown tab**
- [ ] **Section 6 (p_market / EV Analysis)** — scatter, calibration curves,
      divergence histogram, threshold comparison
- [ ] **Section 10 (Statistical Tools)** — all 7 calculators, fully client-side
- [ ] Crossfilter wiring: heatmap cells, symbol cards, divergence bars all
      apply filters to Sections 2 and 3

**Phase 2 done when:** Every metric that has previously been requested manually
("run a rolling-50 analysis", "check high-divergence accuracy") is available
as a live, auto-updating panel.

---

### Phase 3 — Charts & Comparison (Week 5–6)

Goal: Price overlay and model comparison operational.

- [ ] **Section 7 (Price & Market Overlay)** — candlesticks, all panes, prediction
      markers, contract windows, uptime overlay, OFI/MLOFI panes
- [ ] **Section 5 (Model Comparison)** — all charts, ensemble simulator,
      lineage view
- [ ] Annotation layer: deployment events from Section 9 overlaid on
      Sections 5 and 7 charts (behind settings toggle)
- [ ] `ActiveFiltersBar` wired to all affected sections

**Phase 3 done when:** You can pull up the price chart for BTC 900s trades,
see every prediction marker color-coded by outcome, and compare H300 vs H60
rolling accuracy on the same axis.

---

### Phase 4 — Features & History (Week 7)

Goal: Model introspection and training history fully navigable.

- [ ] **Section 8 (Feature Analysis)** — importance, distribution shift (PSI),
      correlation matrix, live values, missingness
- [ ] **Section 9 (Training & Architecture History)** — model registry, version
      diff, deployment timeline
- [ ] Deployment timeline events wired to annotation layer in Sections 5 and 7

**Phase 4 done when:** You can compare H60 V1 vs H60 V3 feature lists side-by-side,
see which features have PSI drift, and click any deployment event to understand
what changed.

---

### Phase 5 — Ops & Polish (Week 8)

Goal: Alerts, raw logs, and final hardening.

- [ ] **Section 11 (Alerts Panel)** — alert log, threshold config drawer,
      suppression effectiveness panel, gate change history
- [ ] **Section 12 (Raw Log Explorer)** — JSON viewer, event chain, diff viewer,
      download controls
- [ ] Full WebSocket live updates across all sections
- [ ] Web Workers for heavy client-side computations (ensemble simulator,
      batch variance simulator, rolling accuracy for large datasets)
- [ ] Performance audit: verify all targets in §22
- [ ] Accessibility audit: keyboard nav, contrast check, screen reader test
- [ ] Light mode verification: all 12 sections checked in Nexus beige light theme
- [ ] Mobile (768px): sidebar collapses to bottom nav, tables become card lists
      or horizontal scroll, charts full-width
- [ ] Final QA: all 12 sections cross-checked against `API_CONTRACT.md` for
      field names, null handling, and NE_t formula correctness

**Phase 5 done when:** Every section in this spec is implemented, the dashboard
runs stably overnight without memory leaks, and all NE_t values have been
spot-checked against manual calculations for 10 known trades.

---

## 25. Glossary

| Term | Definition |
|------|------------|
| H60 | 60-second prediction horizon model |
| H300 | 300-second (5-minute) prediction horizon model |
| MLOFI | Multi-Level Order Flow Imbalance — normalized, MAD-scaled |
| Realized NE_t | Net Edge at time t computed from actual trade outcome. For UP bets: win = `+(1 − p_market − fee)`, loss = `−(p_market + fee)`. For DOWN bets: win = `+(p_market − fee)`, loss = `−((1 − p_market) + fee)`. Never use theoretical formula as primary metric. |
| Theoretical NE_t | `p_model − fee − p_market`. Comparison/reference only. Known to be inaccurate — was masking H60 losses before correction. |
| p_market | Polymarket implied probability of the YES (UP) outcome at trade time |
| p_model | Model's predicted probability that direction = UP |
| Divergence | `\|p_model − p_market\|` absolute. Signed divergence = `p_model − p_market` |
| PSI | Population Stability Index. Measures feature distribution shift between training and live. < 0.10 stable, 0.10–0.25 monitor, > 0.25 shift detected. |
| Gate | One of four sequential execution filters applied before a trade is placed: structural → adverse selection → NE_t → Sanderink |
| Sanderink Gate | Bayesian posterior gate that suspends trading if cumulative win rate falls below threshold |
| EWM | Exponentially Weighted Mean — used for `mid_price_dev_30d` normalization |
| Warmup | Startup period during which predictions are logged but not traded |
| Rolling-50 | Accuracy over the most recent 50 resolved trades. Gate threshold: 51.5%. |
| Contract Window | The period between a trade's open and close timestamps (300s or 900s) |
| Suppressed | A trade that was generated but not executed due to an active suppression rule (contract_mismatch or utc_blackout) |
| contract_mismatch | Suppression rule blocking 300s trades for H300 (whose signal resolves at 900s) |
| utc_blackout | Suppression rule blocking H60 trades during UTC 21:00–04:00 (overnight low-accuracy window) |
| Variance Context | Statistical explanation shown when rolling-50 fails gate: z-score and p-value comparing current rolling value against overall model accuracy, answering "is this real edge erosion or expected sampling variance?" |
| Annotation Layer | Optional overlay of deployment events (suppression additions, model changes, bug fixes) on time series charts. Toggled in settings. |
| Suppression Effectiveness | Ongoing validation that active suppression rules are saving NE_t, computed by back-testing what would have happened if suppressed trades had been executed |
| VPIN | Volume-synchronized Probability of Informed Trading |
| Wilson CI | Wilson score confidence interval for proportions — used for all accuracy confidence intervals. More accurate than normal approximation at small N and extreme p values. |
```