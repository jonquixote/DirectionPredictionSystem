# Plan: Separate WS Feed Service

## Problem
`_run_predictions()` runs 84 models synchronously on the asyncio event loop, blocking it for 1-5+ seconds. The WebSocket can't respond to pings within `ping_timeout=60s`, so the connection drops. MAD warmup never completes, no predictions fire.

## Architecture
```
┌─────────────────────────┐  Unix Socket   ┌──────────────────────────────┐
│ v3-ws-feed.service      │ ───────────── │ v3-paper-trader.service      │
│                         │ /tmp/v3-feed  │                              │
│ BybitOrderBookManager   │  1s ticks     │ FeedClient                   │
│ (owns WS connection)    │ ──streamed──→ │  ├─ reads from data socket   │
│  ├─ connect_async()     │ JSON lines    │  ├─ calls feature_computer   │
│  ├─ process_message()   │               │  │   .on_book_update()       │
│  └─ applies deltas      │               │  └─ tracks _first_data_time  │
│                         │               │                              │
│  Downsample: 1/s/symbol │               │  Reconnect = snapshot+resume │
│                         │               │                              │
│ Control Socket          │ ◄────────────→│ Control requests:            │
│ /tmp/v3-feed-ctl        │ request/resp  │ {cmd:"health"}               │
│                         │               │ {cmd:"snapshot", symbol:…}   │
└─────────────────────────┘               └──────────────────────────────┘
```

## Invariants
1. **Data socket emits complete canonical 1s state** — not partial deltas
2. **Control-socket snapshot is full book for resync** — separate from 1s streamed updates

## New Files

### 1. `api/ws_feed_service.py` (~150 lines)
Standalone WS feed process:
- Owns `BybitOrderBookManager` (same delta protocol, gap detection, reconnect)
- On each WS tick, tracks per-symbol downsample (1s via `DOWNSAMPLE_MS=1000`)
- When 1s elapsed: serialize canonical tick and write to all connected data clients
- Control socket handler: `health` (connection status, uptime, last tick per symbol), `snapshot` (full book for a symbol)
- New data clients receive immediate snapshot of all current books on connect
- Entry point: `python -m api.ws_feed_service [--testnet] [--data-socket ...] [--control-socket ...]`

Protocol:
- Data socket (`/tmp/v3-feed.sock`): newline-delimited JSON
  - Tick: `{"type":"tick","symbol":"BTCUSDT","bids":[[104500.0,0.5],...],"asks":[[104501.0,0.3],...],"exchange_ts":1747123456000}`
  - Snapshot (on connect): `{"type":"snapshot","symbol":"BTCUSDT","bids":[...],"asks":[...],"exchange_ts":...}`
  - Heartbeat: `{"type":"heartbeat","ts":1747123456000}`
- Control socket (`/tmp/v3-feed-ctl.sock`): request-response JSON
  - `→ {"cmd":"health"}` / `← {"status":"connected","uptime_s":3600,"symbols":[...],"last_tick_ts":{...},"clients":1}`
  - `→ {"cmd":"snapshot","symbol":"BTCUSDT"}` / `← {"symbol":"BTCUSDT","bids":[...],"asks":[...],"exchange_ts":...,"synced":true}`

### 2. `api/feed_client.py` (~120 lines)
Async client for paper trader:
- `connect()`: opens data socket connection
- `listen()` (async): reads JSON lines, fires registered callbacks
- `on_update(callback)`: register callback `(symbol, bids, asks, exchange_ts)`
- `on_snapshot(callback)`: register callback for snapshot messages
- `health()`: sends request on control socket, returns status
- `request_snapshot(symbol)`: sends request on control socket, returns full book
- `close()`: cleanup sockets
- **Reconnect logic**: on data socket close/error, auto-reconnect with backoff (2s→4s→8s→max 30s). On reconnect: request snapshot via control socket for each symbol, fire snapshot callbacks so paper trader rebuilds state, then resume tick stream.
- Tracks `first_data_time_ms`

### 3. `deploy/systemd/v3-ws-feed.service`
```ini
[Unit]
Description=ofi-lab-v3 WebSocket feed
After=network.target
Before=v3-paper-trader.service

[Service]
Type=simple
User=johnny
WorkingDirectory=/home/johnny/ofi-lab-v3
EnvironmentFile=/etc/v3/env
Environment="PYTHONPATH=/home/johnny/ofi-lab-v3"
ExecStart=/home/johnny/ofi-lab-v3/.venv/bin/python -m api.ws_feed_service
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=v3-ws-feed

[Install]
WantedBy=multi-user.target
```

## Modified Files

### 4. `trading/paper_trader.py`
- **Line 234**: Replace `self.book_manager = BybitOrderBookManager(symbols=PREDICTION_SYMBOLS, levels=10, testnet=testnet)` with `self.feed_client = FeedClient(symbols=PREDICTION_SYMBOLS)`
- **Line 1140-1155**: `_on_book_update(self, symbol, bids, asks, exchange_ts)` — receive `exchange_ts` as parameter instead of reading from `book_manager.books`
  ```python
  def _on_book_update(self, symbol: str, bids: list, asks: list, exchange_ts: int) -> None:
      if not bids or not asks:
          return
      if self._first_data_time_ms is None:
          self._first_data_time_ms = int(time.time() * 1000)
          logger.info("First L2 data received — MAD warmup starts (30 min)")
      self.feature_computer.on_book_update(symbol, bids, asks, exchange_ts)
  ```
- **Line 1673**: `self.feed_client.on_update(self._on_book_update)`
- **Line 1693-1696**: `asyncio.gather(self.feed_client.listen(), self._contract_boundary_loop())`
- **Line 1707**: `self.feed_client.close()` instead of `self.book_manager.disconnect()`
- **Remove import**: `from api.bybit import BybitOrderBookManager` (no longer directly used)
- **Add import**: `from api.feed_client import FeedClient`

### 5. `deploy/systemd/v3-paper-trader.service`
Add `After=v3-ws-feed.service` to [Unit] section.

### 6. `vps_deployment_guide.md`
Add feed service deployment section.

## Feed Service Downsample Logic
```python
def _on_book_update(self, symbol, bids, asks):
    cts_ms = self.book_manager.books.get(symbol, {}).get("exchange_ts", 0)
    last = self._per_symbol_last_ts.get(symbol, 0)
    if cts_ms and (cts_ms - last) < DOWNSAMPLE_MS:
        return
    self._per_symbol_last_ts[symbol] = cts_ms
    # Broadcast canonical 1s tick to all data clients
    broadcast_tick(symbol, bids, asks, cts_ms)
```

## Reconnect Protocol
When the data socket disconnects (feed crash/restart):
1. `FeedClient._listen_loop()` catches the disconnect
2. Logs warning, pauses predictions (marks feed as disconnected)
3. Reconnects with exponential backoff (2s, 4s, 8s, max 30s)
4. On successful reconnect: request snapshot via control socket for each symbol
5. Fire `on_snapshot` callbacks — paper trader calls `feature_computer.on_book_update()` with snapshot data
6. Resume normal tick stream
7. Log recovery, unpause predictions

## Startup Ordering
1. `v3-ws-feed.service` starts first, connects to Bybit WS
2. `v3-paper-trader.service` starts after, connects to feed socket
3. If feed isn't ready yet, FeedClient retries with backoff
4. Paper trader's 30-min warmup starts from first tick received

## Deployment Steps
1. Create `api/ws_feed_service.py`
2. Create `api/feed_client.py`
3. Modify `trading/paper_trader.py`
4. Create `deploy/systemd/v3-ws-feed.service`
5. Update `deploy/systemd/v3-paper-trader.service`
6. Rsync all changes to VPS
7. Clear `__pycache__` on VPS
8. Copy service file, `systemctl daemon-reload`
9. Enable + start `v3-ws-feed.service`
10. Restart `v3-paper-trader.service`
11. Monitor: feed connects to Bybit, paper trader connects to feed, warmup progresses, predictions fire
12. Verify 1512 rows/hr

## Thread Safety (for the feed service itself)
- `BybitOrderBookManager._callbacks` is called from the event loop — safe
- `_data_clients` list is modified from the event loop — safe (all async on same loop)
- `_per_symbol_last_ts` dict is accessed from the callback which runs on the event loop — safe
- No threading needed in the feed service itself — it's pure asyncio
