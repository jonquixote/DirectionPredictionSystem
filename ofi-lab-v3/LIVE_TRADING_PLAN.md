# Live Trading Activation Plan — Project Golden Goose Final Leg

**Status:** Pre-implementation specification
**Target container:** `h300-retrain-shifted-v2-clone` (Server 2 VPS)
**Author:** Handoff session, 2026-05-01
**Mode:** Paper trading remains primary. Live execution is additive, gated, and default-OFF.

---

## 1. Objective

Enable live execution on Polymarket for the `h300` model targeting 15-minute (900s) BTC and SOL up/down contracts. All other symbol/horizon combinations remain paper-only. The system must deploy in a "loaded but safe" state — kill switch off, no orders sent until operator explicitly arms via API.

## 2. Hard Constraints

1. **No ML code modified.** Feature engineering, model training, and inference paths are frozen.
2. **Edits limited to:** API routing, configuration parsing, market discovery, trade execution dispatch.
3. **Local-first development.** Build and validate in `ofi-lab/` repository, then deploy to V2 Clone container.
4. **Default-deny posture.** Every new gate ships disabled except the existing 0.55 confidence gate.

## 3. Live Trading Universe

| Symbol | Horizon | Duration | Kelly Fraction | Status |
|---|---|---|---|---|
| BTCUSDT | h300 | 900s (15m) | 20% | LIVE-eligible |
| SOLUSDT | h300 | 900s (15m) | 5% | LIVE-eligible |
| All others | any | any | n/a | Paper-only |

Allow-list enforced at `LiveTrader.maybe_place_order()` boundary. Anything outside silently falls through to paper-only path.

## 4. Credential Schema (`.env`)

L1 EOA signer is also the funder address (single-wallet setup confirmed by operator).

```
POLYMARKET_PRIVATE_KEY=<EOA private key>
POLYMARKET_FUNDER_ADDRESS=<same address as signer>
POLYMARKET_API_KEY=<L2 API key>
POLYMARKET_API_SECRET=<derived at boot if blank>
POLYMARKET_API_PASSPHRASE=<derived at boot if blank>

LIVE_TRADING_ENABLED=false
LIVE_ALLOW_LIST=BTCUSDT:900,SOLUSDT:900
LIVE_BANKROLL_FRACTION=1.0
LIVE_ORDER_TYPE=market
```

`API_SECRET` and `API_PASSPHRASE` may be derived at boot via `py_clob_client_v2.ClobClient.derive_api_key()` when blank, using `PRIVATE_KEY` as L1 signer.

## 5. Filter Defaults — Default-OFF Posture

| Filter | Default state | Runtime override |
|---|---|---|
| Confidence gate (≥0.55) | **ON** (existing) | `POST /live/config` |
| Per-symbol Kelly map | empty → falls back to global | `POST /live/config` |
| Per-trade USD ceiling | unset → no cap | `POST /live/config` |
| Slippage cap (limit orders) | unset | `POST /live/config` |
| Order type | `market` | `POST /live/config` |
| Bankroll fraction | 1.0 (full chain USDC) | `POST /live/config` |
| Kill switch | OFF | `POST /live/enable` (mem-only) |
| Allow-list | env-only | requires container restart |

## 6. File-Level Changes

### 6.1 `trading/polymarket_discovery.py`
- Add `duration_min: int = 5` parameter to `_make_slug`, `discover_contract`, `get_p_market`.
- Update slug template to `{prefix}-updown-{duration_min}m-{boundary_ts}`.
- Cache key changes to `(symbol, boundary_ts, duration_min)`.

### 6.2 `trading/paper_trader.py`
- Config additions:
  - `per_symbol_kelly: dict[str, float]` mirroring existing `per_symbol_confidence` pattern.
- `_compute_stake`: lookup `f["per_symbol_kelly"].get(symbol, f["kelly_fraction"])`.
- Horizon → duration map: `h60` → 5min, `h300` → 15min. Pass `duration_min` into `get_p_market()`.
- New CLI flag: `--per-symbol-kelly '{"BTCUSDT":0.20,"SOLUSDT":0.05}'`.
- After paper ledger write, call `live_trader.maybe_place_order(...)`. No-op when disabled or off allow-list.
- **No changes to model loading, feature pipeline, prediction code, or stake math itself** beyond the per-symbol Kelly lookup.

### 6.3 NEW `execution/live_trader.py`
```python
class LiveTrader:
    def __init__(self, env: dict): ...
    def is_enabled(self) -> bool
    def in_allow_list(self, symbol: str, duration_sec: int) -> bool
    def get_usdc_balance(self) -> float
    def effective_bankroll(self) -> float
    def enforce_tick(self, price: float, tick: float = 0.001) -> float
    def enforce_min_size(self, size_usd: float, floor: float = 5.0) -> float
    def maybe_place_order(
        self,
        symbol: str,
        duration_sec: int,
        token_id: str,
        side: str,                 # "BUY" | "SELL"
        price: float,
        size_usd: float,
        order_type: str = None,    # falls back to LIVE_ORDER_TYPE
    ) -> Optional[OrderResult]
```
- Import-guarded: `try: from py_clob_client_v2 import ClobClient` — when missing, class becomes pure stub (no live orders ever sent). Local development never breaks.
- Per-trade ceiling and slippage cap wired but unset by default.
- Tick size and min order size: hardcoded defaults (0.001, $5.00). Polymarket per-market API consulted opportunistically for monitoring — failures fall back to hardcoded values without blocking execution.

### 6.4 `trading/api_server.py`
New endpoints under `/live/*` namespace:

| Method | Path | Purpose |
|---|---|---|
| GET | `/live/status` | `{enabled, allow_list, bankroll_fraction, order_type, last_order_at, last_error}` |
| GET | `/live/balance` | `{usdc_chain_balance, effective_bankroll}` |
| POST | `/live/enable` | Flip kill switch ON (memory only) |
| POST | `/live/disable` | Flip kill switch OFF |
| POST | `/live/config` | Body: any of `{per_symbol_kelly, per_trade_usd_cap, slippage_cap, order_type, bankroll_fraction}` |
| GET | `/live/orders/recent?limit=N` | Tail of `live_orders.jsonl` |

## 7. Logging & Audit

- New ledger: `/data/live_orders.jsonl`, separate from existing paper ledger.
- One JSON line per live-eligible signal, regardless of whether order was placed.
- Schema:
  ```
  {
    "ts": "2026-05-01T15:00:00Z",
    "symbol": "BTCUSDT",
    "duration_sec": 900,
    "boundary_ts": 1774866600,
    "token_id": "0x...",
    "side": "BUY",
    "requested_price": 0.523,
    "requested_size_usd": 12.50,
    "gate_result": "PLACED" | "GATED_KILL_SWITCH" | "GATED_ALLOW_LIST" | "GATED_MIN_SIZE" | "ERROR",
    "order_id": "0x..." | null,
    "fill_status": "FILLED" | "PARTIAL" | "OPEN" | "REJECTED" | null,
    "error": null | "..."
  }
  ```
- Paper ledger writes always happen first, regardless of live path outcome.

## 8. Build Order (5 commits)

1. `.env.example` updated with new schema.
2. `trading/polymarket_discovery.py` — duration parameterization.
3. `execution/live_trader.py` — new module, import-guarded ClobClient, all gates wired but neutral.
4. `trading/paper_trader.py` — per-symbol Kelly, horizon→duration map, dispatch hook.
5. `trading/api_server.py` — `/live/*` endpoints.

Each commit independently verifiable. Live orders impossible until commit 5 plus operator-issued `POST /live/enable`.

## 9. Deployment to V2 Clone

After local verification:
1. Push commits to repository branch.
2. Pull into `h300-retrain-shifted-v2-clone` container on Server 2.
3. Mount real `.env` into container (do not bake into image).
4. Restart container, verify `/live/status` returns `{enabled: false, ...}`.
5. Verify `/live/balance` returns non-zero USDC.
6. **Operator-initiated** `POST /live/enable` — system goes live.

## 10. Post-Deployment Security

The private key was transmitted in plain text during this handoff session. After live trading is verified working:
1. Rotate the L2 API key via Polymarket dashboard.
2. Generate a fresh EOA wallet offline. Transfer USDC balance to it. Replace `PRIVATE_KEY` and `FUNDER_ADDRESS` in the V2 Clone `.env`.
3. Document the rotation in the operator runbook.

## 11. Open Considerations

- **Kill switch persistence:** Memory-only by design. Container restart resets to OFF. Forces explicit re-arm.
- **Allow-list mutation:** Env-only. Adding new live pairs requires container restart by intent.
- **Bankroll splits via API:** `bankroll_fraction` allows operator to test with a slice (e.g., 0.05 = 5% of chain balance) before going full size.
- **Order type evolution:** v1 ships market orders for simplicity. Limit and limit+slippage modes scaffolded but inactive — flip via `POST /live/config`.

---

# ADDENDUM A — Kalshi Migration (2026-05-02)

## A.1 Direction Change

Project pivoted from Polymarket to **Kalshi** as the live execution venue. Polymarket modules retained in repo for archival comparison; live-trading code path now routes through Kalshi by default (`EXCHANGE=kalshi`).

## A.2 Live Universe (v1, narrowed)

| Symbol | Horizon | Duration | Kelly | Status |
|---|---|---|---|---|
| BTCUSDT | h300 | 900s (15m) | **20%** | LIVE-eligible (Model A V2 Clone) |
| SOLUSDT | h300 | 900s | n/a | **PARKED** for v1 |
| All others | any | any | n/a | Paper-only |

Confidence gate for live Kalshi path: **0.56** (bumped from baseline 0.55). Paper trading retains 0.55.

## A.3 Files Added

| File | Purpose |
|---|---|
| `execution/kalshi_fees.py` | Pure-math taker/maker formulas (parabolic curve, ceil-up to $0.0001), break-even, EV, fee-aware Kelly |
| `api/kalshi.py` | RSA-PKCS1v15-SHA256 signer, REST client (markets, balance, positions, fills, orders, cancels), WebSocket streamer with exponential-backoff reconnect + 60s rolling re-subscribe, timezone diagnostic |
| `execution/kalshi_live_trader.py` | 7-gate stack (kill switch → allow-list → client-ready → confidence → bankroll → sizing → tick), ledger to `/data/kalshi_orders.jsonl`, memory-only kill switch, fee-aware contract sizing |
| `secrets/kalshi_private_key.pem` | RSA-2048 key (gitignored). Container deploy: mount at `/data/kalshi_private_key.pem` |

## A.4 Files Modified

- `config.py` — added `EXCHANGE = os.environ.get("EXCHANGE", "kalshi").lower()` flag
- `.env` / `.env.example` — added Kalshi credential + control block
- `requirements.txt` — added `cryptography>=42.0`
- `trading/paper_trader.py`:
  - Imports KalshiLiveTrader; reads `EXCHANGE` flag
  - `__init__`: declares `self._kalshi_trader: Optional[KalshiLiveTrader]` and ticker cache
  - `run()`: instantiates + connects KalshiLiveTrader after HTTP session creation, runs timezone diagnostic
  - `_run_predictions()`: after successful `log_trade()` for `(model="h300", symbol="BTCUSDT", duration=900)`, fires `asyncio.create_task(self._dispatch_kalshi_live(...))`
  - New methods: `_dispatch_kalshi_live`, `_resolve_kalshi_ticker`, `_midpoint_from_levels`

**Untouched:** all model loading, feature engineering, prediction inference, stake math internals, paper ledger schema.

## A.5 Empirical Findings (smoke test, 2026-05-02 demo env)

1. **Auth works.** `KalshiAuth.sign_headers` produces valid signatures; `/markets?series_ticker=KXBTC15M&status=open` returns HTTP 200 with 5 active 15m contracts.

2. **Series ticker confirmed:** `KXBTC15M` (not `GEMI-BTC15M` per original blueprint). Format observed: `KXBTC15M-{YY}{MMM}{DD}{HHmm}-{mm}` where embedded `HHmm` is **ET-encoded** (e.g., `0400` = 04:00 ET) but the **API `close_time` field is in UTC** (e.g., `2026-05-02T08:00:00Z`).

3. **Timezone risk RESOLVED — UTC alignment confirmed.** Live `close_time` values fall on `:00, :15, :30, :45 UTC`, matching the model's UTC prediction boundaries exactly. **No offset risk.** Always use API's `close_time` field, never parse from ticker name.
   ```
   KXBTC15M-26MAY020400-00 → close_time = 2026-05-02T08:00:00Z
   KXBTC15M-26MAY020415-15 → close_time = 2026-05-02T08:15:00Z
   KXBTC15M-26MAY020430-30 → close_time = 2026-05-02T08:30:00Z
   ```

4. **Auth padding correction (2026-05-02):** Initial blueprint specified `padding.PKCS1v15`. Empirically, this returns HTTP 401 `INCORRECT_API_KEY_SIGNATURE`. Kalshi v2 actually uses **RSA-PSS with MGF1-SHA256, salt_length = DIGEST_LENGTH (32 bytes)**. After switching `KalshiAuth.sign_headers` to PSS, all four private endpoints returned HTTP 200:
   ```
   [balance  ] HTTP 200  {"balance":10000,"portfolio_value":0,"updated_ts":...}
   [positions] HTTP 200  {"cursor":"","event_positions":[],"market_positions":[]}
   [fills    ] HTTP 200  {"cursor":"","fills":[]}
   [orders   ] HTTP 200  {"cursor":"","orders":[]}
   ```
   Demo portfolio confirmed seeded with $100.00 (10000 cents). Original `/markets` 200 was misleading — that endpoint is public and accepts unsigned requests.

5. **Fee model validated:**
   ```
   taker @ 0.50, 1c   = $0.0175  (matches docs: 1.75¢/contract → 3.5%)
   maker @ 0.50, 1c   = $0.0044  (matches docs: 0.44¢/contract → 0.875%)
   break_even @ 0.50  = $0.5175  (true_p must exceed market by ≥1.75pp)
   Kelly p=0.60 m=0.50 = 0.171   (~17% full-Kelly, scaled to 20% fraction = 3.4%)
   Kelly p=0.51 m=0.50 = 0.000   (correctly zeroed below break-even)
   ```

## A.6 Default-OFF Posture (Kalshi)

| Filter | Default | Override |
|---|---|---|
| `KALSHI_LIVE_ENABLED` (kill switch) | **OFF** | runtime: `kt.enable()` (memory-only) |
| Allow-list | `BTCUSDT:900` only | env: `KALSHI_LIVE_ALLOW_LIST` |
| Confidence gate | 0.56 | env: `KALSHI_CONFIDENCE_GATE` |
| Kelly fraction | 0.20 | env: `KALSHI_KELLY_FRACTION` |
| Bankroll fraction | 1.0 | env: `KALSHI_BANKROLL_FRACTION` |
| Order type | `taker` (Kalshi `market`) | env: `KALSHI_DEFAULT_ORDER_TYPE` |
| Per-trade USD cap | unset (no cap) | runtime: `kt.update_config(per_trade_usd_cap=...)` |
| Env | `demo` | env: `KALSHI_ENV=prod` |

## A.7 Pre-Deploy Checklist (Kalshi V2 Clone Container)

Operator-side:
- [ ] Verify Kalshi demo dashboard shows API key linked to funded demo portfolio
- [ ] Confirm `/portfolio/balance` returns HTTP 200 (currently 401 NOT_FOUND)
- [ ] Rotate API key + RSA key after this session (both transmitted in chat)

Container-side:
- [ ] Mount `/data/kalshi_private_key.pem` (chmod 600)
- [ ] Set env: `EXCHANGE=kalshi`, `KALSHI_ENV=demo`, key + key path
- [ ] `pip install cryptography` (already in requirements.txt)
- [ ] Restart container; verify `tz_diagnostic` log lines show `aligns_15m_utc=True`
- [ ] Verify `KalshiLiveTrader.status()` shows `client_ready=True`, `enabled=False`
- [ ] Watch `/data/kalshi_orders.jsonl` — should accumulate `GATED_KILL_SWITCH` rows for BTC 900s signals

Go-live:
- [ ] Operator-initiated: `KalshiLiveTrader.enable()` (or future `POST /live/enable` if API endpoints added)
- [ ] Watch first ledger row transition from `GATED_KILL_SWITCH` → `PLACED`
- [ ] Verify order_id appears in Kalshi dashboard under demo portfolio
- [ ] Verify fill (or rejection) reflected in `/portfolio/fills`

Prod cutover (after demo proves end-to-end):
- [ ] Generate fresh prod API key + RSA key in Kalshi dashboard
- [ ] Update `.env` with prod creds + `KALSHI_ENV=prod`
- [ ] Restart with kill switch OFF; repeat go-live sequence

