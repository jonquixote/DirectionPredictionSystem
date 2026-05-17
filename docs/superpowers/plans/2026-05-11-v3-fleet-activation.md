# v3 Fleet Activation Plan

> **Goal:** Get 84 trained fleet models making predictions and paper trades at 5/15/30 minute intervals on the VPS.

**Date:** 2026-05-11
**Pre-deploy state:** Fleet Training Plan complete — 84 models trained and registered in `model_registry` SQLite table. Paper trader cannot use them due to 7 blocking issues below.

---

## Blocking Issues (must fix before fleet can trade)

### B1. CRASH: Fleet mode never activates

**File:** `ofi-lab-v3/trading/paper_trader.py:1763`
**Problem:** The fleet-mode gate is:
```python
if not args.h60_model and not args.h300_model:
```
But argparse defaults are non-empty paths:
```python
parser.add_argument("--h60-model", default="/data/models/latest_h60/model.lgb", ...)
parser.add_argument("--h300-model", default="/data/models/latest_h300/model.lgb", ...)
```
Systemd unit passes no CLI flags → defaults fill in → condition is always `False` → legacy mode always selected. Fleet mode is dead code.
**Fix:** Add `--fleet` flag. When set, skip legacy model path logic and enter fleet branch. Make argparse defaults `""` (empty string) so `--fleet` can also be auto-detected from the old condition. Update systemd ExecStart to include `--fleet`.

### B2. CRASH: model_metadata KeyError for fleet model names

**File:** `ofi-lab-v3/trading/paper_trader.py` — lines 322, 374, 1234
**Problem:** Three sites do `config.PAPER_TRADING["model_metadata"][model_name]`. The dict has 4 hardcoded entries: `h300`, `h60`, `900s_btc_v3_20260315`, `60s_btc_v3_20260315`. Fleet names like `h60_btc_v3_90d` will raise `KeyError`.
**Fix:** In fleet mode, build `model_metadata` dynamically from `fleet_loader` results. Each fleet dict already has `symbol`, `training_horizon_seconds`, `feature_version` (always `v3`), `train_window_start`, `train_window_end`. Synthesize the same shape as the hardcoded entries. Store as `self._model_metadata` (instance attribute) and replace all `config.PAPER_TRADING["model_metadata"][model_name]` with `self._model_metadata[model_name]`.

### B3. SILENT GARBAGE: Cross-symbol scoring

**File:** `ofi-lab-v3/trading/paper_trader.py:1205-1233`
**Problem:** The scoring loop is:
```python
for symbol in PREDICTION_SYMBOLS:        # BTC, ETH, SOL, (XRP soon)
    ...
    for model_name, model in self.models.items():  # ALL 84 models
        meta = config.PAPER_TRADING["model_metadata"][model_name]
        features = {col: bar.get(col, 0.0) for col in self.feature_names[model_name]}
```
Every model scores against every symbol. A BTC-only model scores XRP features → garbage prediction. With 4 symbols × 84 models = 336 scores per boundary, 252 of which are cross-symbol noise.
**Fix:** Restructure loop so each model only scores its own symbol. Use `self._model_metadata[model_name]["symbol"]` to filter. Inner loop becomes: iterate `self.models`, get its symbol from metadata, skip if that symbol isn't warmed up, get that symbol's bar.

### B4. SILENT SKIP: XRPUSDT not in PREDICTION_SYMBOLS or TRADE_SYMBOLS

**File:** `ofi-lab-v3/trading/paper_trader.py:77-78`
**Problem:**
```python
PREDICTION_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TRADE_SYMBOLS = ["BTCUSDT", "SOLUSDT"]
```
XRPUSDT is missing. 21 XRP models exist in registry but have no WebSocket subscription, no feature computation, no scoring loop entry.
**Fix:** Add `"XRPUSDT"` to both lists. Also add XRPUSDT to `config.PAPER_TRADING["prediction_symbols"]`, `config.PAPER_TRADING["trade_symbols"]`, and `MID_PRICE_TRAINING_RANGE` in both `paper_trader.py:96-100` and `config.py:103-107`.

### B5. MISSING: No 1800s (30-min) contract duration

**File:** `ofi-lab-v3/trading/paper_trader.py:81` and `config.py:99`
**Problem:** `CONTRACT_DURATIONS = [300, 900]` — no 1800s paper trade simulation.
**Fix:** Add `1800` to `CONTRACT_DURATIONS` in both `paper_trader.py:81` and `config.py:99`. Add 30-minute boundary alignment check (similar to the 15-minute check at line 1517): `if duration == 1800 and boundary_sec % 1800 != 0 and not suppress_reason: suppress_reason = "non_30m_boundary"`.

### B6. H60_BLACKOUT_MODELS and H300_SUPPRESS_DURATIONS don't match fleet names

**File:** `ofi-lab-v3/trading/paper_trader.py:90, 93`
**Problem:**
```python
H60_BLACKOUT_MODELS = {"h60", "h60_v2", "h60_v3"}
H300_SUPPRESS_DURATIONS = {"h300": {300}}
```
Fleet names are `h60_btc_v3_90d`, `h300_eth_v3_180d`, etc. — won't match. H60 fleet models trade during blackout hours. H300-equivalent fleet models won't suppress 300s contracts.
**Fix:** Change both to predicate-based matching. `H60_BLACKOUT_MODELS` → check `training_horizon_seconds == 60` from metadata. `H300_SUPPRESS_DURATIONS` → check `training_horizon_seconds == 300` (or `== 900` for the H300-equivalent). Alternatively: populate sets at boot from fleet metadata.

### B7. _emit_prediction_rows uses hardcoded EVALUATION_WINDOWS

**File:** `ofi-lab-v3/trading/paper_trader.py:379`
**Problem:**
```python
evaluation_windows=config.EVALUATION_WINDOWS,
```
This uses the global config evaluation windows (which include 3600s) for every model. But fleet models have per-model `evaluation_windows` in the registry (default `[300, 900, 1800]`). Using `config.EVALUATION_WINDOWS` means models get evaluation rows at windows they weren't intended for.
**Fix:** Pass per-model evaluation windows from `self._model_metadata[model_name]["evaluation_windows"]` instead of the global constant. Fleet loader already returns this field.

---

## Implementation Tasks

### Task 1: Add `--fleet` CLI flag and fix fleet activation gate
- **File:** `ofi-lab-v3/trading/paper_trader.py`
- Add `parser.add_argument("--fleet", action="store_true", default=False, help="Use fleet mode (load models from registry)")`
- Change argparse defaults for `--h60-model` and `--h300-model` to `""` (empty string)
- Change fleet gate at line 1763 to: `if args.fleet or (not args.h60_model and not args.h300_model):`
- In fleet branch, load fleet metadata from `load_active_fleet(conn)` and store as `fleet_meta`
- Build `model_paths` from fleet dicts: `{c["name"]: c["artifact_path"] for c in fleet}`
- Build `model_metadata` dict from fleet dicts (see Task 2)
- Pass `model_metadata` to `PaperTrader.__init__`
- **Verify:** Run with `--fleet` and no legacy flags → fleet branch taken

### Task 2: Dynamic model_metadata from fleet registry
- **Files:** `ofi-lab-v3/trading/paper_trader.py`, `ofi-lab-v3/trading/fleet_loader.py`
- `fleet_loader.py` already returns `symbol`, `training_horizon_seconds`, `evaluation_windows`, `train_window_start`, `train_window_end`, `feature_version`
- In `PaperTrader.__init__`, accept `fleet_metadata` parameter (optional, default `None`)
- If `fleet_metadata` provided, build `self._model_metadata` from it:
  ```python
  self._model_metadata = {}
  for m in fleet_metadata:
      self._model_metadata[m["name"]] = {
          "symbol": m["symbol"],
          "training_horizon_seconds": m["training_horizon_seconds"],
          "feature_version": m.get("feature_version", "v3"),
          "train_window_start": m.get("train_window_start"),
          "train_window_end": m.get("train_window_end"),
          "train_cutoff": m.get("train_window_end"),
          "evaluation_windows": m.get("evaluation_windows", [300, 900, 1800]),
          "kalshi_dispatch_enabled": False,  # safe default for new fleet models
      }
  ```
- If `fleet_metadata` is `None` (legacy mode), `self._model_metadata = config.PAPER_TRADING["model_metadata"]`
- Replace all 3 occurrences of `config.PAPER_TRADING["model_metadata"][model_name]` with `self._model_metadata[model_name]`
- Add a `get_model_meta(self, model_name)` helper that returns `self._model_metadata.get(model_name, {})` with a logged warning if missing
- **Verify:** Fleet model name lookup doesn't KeyError

### Task 3: Fix cross-symbol scoring (restructure scoring loop)
- **File:** `ofi-lab-v3/trading/paper_trader.py:1205-1233`
- Current structure:
  ```
  for symbol in PREDICTION_SYMBOLS:
      bar = get_bar(symbol)
      for model_name in self.models:
          score(model, bar)  # WRONG: BTC model scores SOL bar
  ```
- New structure:
  ```
  for model_name, model in self.models.items():
      meta = self._model_metadata[model_name]
      symbol = meta["symbol"]
      if symbol not in PREDICTION_SYMBOLS:
          continue
      if not self.feature_computer.is_warmed_up(symbol):
          continue
      bar = self.feature_computer.get_1min_bar(symbol)
      if bar is None:
          continue
      mid_price = bar.get("mid_price", 0)
      self._check_mid_price_range(symbol, mid_price)
      # ... p_market query (per symbol, cache to avoid duplicate queries)
      # ... score model against its own symbol's bar
  ```
- Need to cache `p_market` per symbol per boundary so it's not queried 84 times
- Move `trade_eligible = symbol in TRADE_SYMBOLS` outside inner loop (compute once per symbol)
- **Verify:** Each model only scores its registered symbol's features

### Task 4: Add XRPUSDT to symbol lists and mid-price ranges
- **Files:** `ofi-lab-v3/trading/paper_trader.py`, `ofi-lab-v3/config.py`
- `paper_trader.py:77`: Add `"XRPUSDT"` to `PREDICTION_SYMBOLS`
- `paper_trader.py:78`: Add `"XRPUSDT"` to `TRADE_SYMBOLS`
- `paper_trader.py:96-100`: Add `"XRPUSDT": [1.5, 5.0]` to `MID_PRICE_TRAINING_RANGE` (approximate range — adjust based on recent XRP/USDT prices)
- `config.py:97`: Add `"XRPUSDT"` to `PAPER_TRADING["trade_symbols"]`
- `config.py:98`: Add `"XRPUSDT"` to `PAPER_TRADING["prediction_symbols"]`
- `config.py:103-107`: Add `"XRPUSDT": [1.5, 5.0]` to `PAPER_TRADING["mid_price_training_range"]`
- Note: `CONFIG["assets"]` in `config.py:19` already includes `"XRPUSDT"` — good.
- **Verify:** XRP models have warm features and score at boundaries

### Task 5: Add 1800s contract duration with 30-min boundary alignment
- **Files:** `ofi-lab-v3/trading/paper_trader.py`, `ofi-lab-v3/config.py`
- `paper_trader.py:81`: Change `CONTRACT_DURATIONS = [300, 900, 1800]`
- `config.py:99`: Change `"contract_durations": [300, 900, 1800]`
- Add 30-minute boundary alignment check near line 1517:
  ```python
  if duration == 1800 and boundary_sec % 1800 != 0 and not suppress_reason:
      suppress_reason = "non_30m_boundary"
  ```
- **Verify:** 1800s trades only fire at xx:00 and xx:30 boundaries

### Task 6: Fix H60_BLACKOUT_MODELS and H300_SUPPRESS_DURATIONS for fleet names
- **File:** `ofi-lab-v3/trading/paper_trader.py`
- Remove `H60_BLACKOUT_MODELS` set constant (line 90)
- Remove `H300_SUPPRESS_DURATIONS` dict constant (line 93)
- Replace line 1372 `if model_name in H60_BLACKOUT_MODELS and utc_hour in H60_BLACKOUT_HOURS:` with:
  ```python
  if meta["training_horizon_seconds"] == 60 and utc_hour in H60_BLACKOUT_HOURS:
  ```
  (where `meta = self._model_metadata[model_name]`)
- Replace line 1511 `suppressed_durs = H300_SUPPRESS_DURATIONS.get(model_name, set())` with:
  ```python
  suppressed_durs = {300} if meta["training_horizon_seconds"] == 900 else set()
  ```
  (H300 = 900s horizon models; they suppress 300s contracts because 5-min is too short for a 15-min signal)
- **Verify:** Fleet H60 models (horizon=60) get blacked out. Fleet H300 models (horizon=900) suppress 300s trades.

### Task 7: Use per-model evaluation_windows in _emit_prediction_rows
- **File:** `ofi-lab-v3/trading/paper_trader.py:379`
- Change:
  ```python
  evaluation_windows=config.EVALUATION_WINDOWS,
  ```
  to:
  ```python
  evaluation_windows=self._model_metadata.get(model_name, {}).get("evaluation_windows", config.EVALUATION_WINDOWS),
  ```
- **Verify:** Fleet models only emit evaluation rows at their configured windows

### Task 8: Extend fleet_loader to return all needed fields
- **File:** `ofi-lab-v3/trading/fleet_loader.py`
- Current SQL:
  ```sql
  SELECT name, symbol, training_horizon_seconds, artifact_path,
         feature_names_path, evaluation_windows, generation, is_baseline,
         lifecycle_state
  ```
  Missing: `train_window_start`, `train_window_end`, `feature_version`, `train_days`
- Add these columns to the SELECT
- **Verify:** Fleet metadata dict has all fields needed by `model_metadata` synthesis

### Task 9: Update systemd unit for fleet mode
- **File:** `ofi-lab-v3/deploy/systemd/v3-paper-trader.service`
- Change ExecStart to include `--fleet`:
  ```ini
  ExecStart=/home/johnny/ofi-lab-v3/.venv/bin/python -m trading.paper_trader --fleet
  ```
- **Verify:** Service starts in fleet mode after deploy

### Task 10: VPS deploy and smoke test
- rsync ofi-lab-v3/ to VPS
- SSH in, restart service: `sudo systemctl restart v3-paper-trader`
- Wait 30 min for MAD warmup
- Check journalctl for: "Fleet mode: loading N models from registry"
- Verify first boundary scores appear in predictions table
- Verify no KeyError, no cross-symbol scoring logs
- Check that XRP predictions appear
- Check that 1800s paper trades fire at half-hour boundaries

---

## Execution Order

Tasks must be done in this order due to dependencies:

1. **Task 8** — Extend fleet_loader (no deps, other tasks depend on its output)
2. **Task 1** — Add `--fleet` flag (depends on fleet_loader output)
3. **Task 2** — Dynamic model_metadata (depends on Task 1 passing fleet data to PaperTrader)
4. **Tasks 4, 5, 6** — Add XRP, 1800s, fix blackout/suppress (all depend on Task 2 for `self._model_metadata`)
5. **Task 3** — Restructure scoring loop (depends on Tasks 2, 4 for metadata + XRP)
6. **Task 7** — Per-model evaluation_windows (depends on Task 2)
7. **Task 9** — Update systemd unit
8. **Task 10** — Deploy and smoke test

---

## Risk Matrix

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| XRP mid-price range wrong | Medium | Low | Range check logs warning, doesn't crash. Can adjust post-deploy. |
| CalibratorRegistry returns identity for new models | Certain | Low | By design — calibrated prob = raw prob until first refit cycle. |
| 84 models × boundary = slow scoring | Low | Medium | LightGBM predict is ~0.1ms. 84 models = ~8ms per boundary. Negligible. |
| 84 models × 3 durations = 252 paper trades per boundary | Certain | Low | Simulated stakes, no real money. Volume is expected and useful for comparison. |
| Fleet loader returns 0 models | Low | High | Guard already exists at line 1770-1772. Check VPS registry data. |
| Feature warmup delay for XRP | Certain (at restart) | Low | 30-min MAD warmup applies to all symbols uniformly. XRP predictions start after warmup. |

---

## Calibration & Kalshi Dispatch Notes

- **Calibration:** New fleet models have no calibration data. `CalibratorRegistry.get()` returns identity mapping (raw prob = calibrated prob). This is safe — no crash, just uncalibrated predictions. After ~100 native resolutions, `calibration_refit.py` can be run to build calibration maps.
- **Kalshi dispatch:** All fleet models default to `kalshi_dispatch_enabled = False`. Only manually promoted models (after sufficient paper trading track record) should be set to `True` in the registry.
- **H60 blackout:** All horizon=60 models (21 models across 4 symbols × 3 train-day windows) will respect the UTC 21:00-03:59 blackout. This is correct — short-horizon models have the same overnight liquidity issues regardless of symbol.

---

## File Change Summary

| File | Changes |
|------|---------|
| `ofi-lab-v3/trading/paper_trader.py` | Tasks 1-7: --fleet flag, dynamic metadata, scoring loop restructure, XRP, 1800s, blackout/suppress fix, per-model eval windows |
| `ofi-lab-v3/trading/fleet_loader.py` | Task 8: Add train_window_start, train_window_end, feature_version, train_days to SELECT |
| `ofi-lab-v3/config.py` | Tasks 4-5: Add XRPUSDT to symbols/ranges, add 1800s to contract_durations |
| `ofi-lab-v3/deploy/systemd/v3-paper-trader.service` | Task 9: Add --fleet to ExecStart |
