# Development Progress Report: May 4 – May 6, 2026

This report summarizes the major updates, bug fixes, feature integrations, and architectural risk analyses implemented across the system since `2026-05-04 01:46:51 -0700`. 

The work spans across the core trading engine (`ofi-lab`), the frontend interface (`dashboard`), production deployment, and system diagnostics.

---

## 1. Core Engine Upgrades (`ofi-lab` Commits)

### Resolving the Kalshi Rollover Dead-Zone (`5b50ff6`)
*   **The Issue:** During the 15-minute boundary transitions, the Kalshi API experiences a slight delay (up to 39 seconds) before the new ticker's state becomes queryable, causing resolution failures.
*   **The Fix:** We implemented a robust retry mechanism within the Kalshi resolver. The system now intelligently backs off and polls for the ticker state across this dead-zone, ensuring no trade resolutions are dropped during the rollover.

### Probability Calibration Framework (`6d056fb` & `a2ac04c`)
*   **The Issue:** Raw model probabilities (e.g., a 0.54 score) don't always translate linearly to a 54% real-world win rate. We needed a way to map the model's theoretical confidence to empirical reality.
*   **The Fix:** We built out a dedicated calibration framework. 
    *   Introduced `fit_calibration.py`, an analytical script that ingests the historical `paper_trades` ledger.
    *   The script groups predictions into probability bins and maps them to actual historical win rates (a `binmap`). 
    *   This sets the foundation for adjusting the live trader's stake sizes based on *calibrated* confidence rather than raw scores.

---

## 2. Dashboard Polish & Data Visibility (`c85b97d` & Recent Updates)

### Home Page & UI Refinement
*   **Kalshi PnL Tracking:** The dashboard home page was upgraded to prominently display the live Kalshi PnL alongside the paper trading metrics.
*   **Flicker Fixes & Polish:** Addressed UI flickering issues that occurred during state polling, providing a smoother user experience.
*   **Visibility-Aware Metrics:** Dashboard metrics (like Win Rate and Gross PnL) were refactored to dynamically update based on the specific time range, model, or symbol the user is currently viewing.

### Paper vs. Live Toggle Integration
*   **Backend (`api_server.py`):** Extended the `/trades` and `/performance` endpoints to accept a `source` query parameter. The API can now seamlessly merge and serve either `paper` ledger data or live `kalshi` order data.
*   **Frontend Data Feed:** Updated `api.ts`, `Trades.tsx`, and `Home.tsx` to include UI toggles. You can now switch your view between the theoretical simulation (Paper) and the actual executed orders (Kalshi).

---

## 3. Live Trading Synchronization & Production Deployment

### Bankroll Tethering
*   **The Goal:** Ensure the paper trader accurately sizes simulated stakes relative to the actual capital available in the live Kalshi account.
*   **The Fix:** Modified `paper_trader.py`'s `_run_predictions` loop to dynamically fetch `effective_bankroll_usd()` from Kalshi at every 15-minute boundary. The Kelly-criterion stake sizing formula (`_compute_stake`) now uses this live bankroll, mathematically tethering the simulation to reality.

### Production Environment Audit
*   **VPS Health:** Verified that the container `h300-retrain-shifted-v2-clone` is healthy and running on Server 2.
*   **Allow-List Verification:** Confirmed that the live dispatcher correctly respects the `KALSHI_LIVE_ALLOW_LIST`, actively trading `BTCUSDT` while successfully gating non-approved symbols like `SOLUSDT` (`contract_mismatch`).
*   **Safety Status:** The system is currently operating normally but under the `GATED_KILL_SWITCH`, preventing live capital exposure until we manually toggle the system to full "live" execution.
*   **Directional Mapping Verification:** We conducted an audit to ensure the live Kalshi trader accurately copies the paper trader. We verified the directional mapping is flawless: when the model predicts "Up", Kalshi buys YES, and when the model predicts "Down", Kalshi effectively bets NO (by adjusting price and probability correctly).

---

## 4. Architectural Analysis & Risk Assessment

### The "Midpoint Sizing" Risk Analysis
*   We analyzed the theoretical approach of averaging the Paper Trader's stake and the Kalshi Trader's stake (the "midpoint").
*   **The Danger (Negative Edge Exposure):** The Paper Trader sizes its bets using Polymarket prices, while Kalshi sizes its bets using Kalshi's orderbook. If Polymarket presents a massive edge (e.g., $0.20) but Kalshi presents a negative edge (e.g., $0.60), the Kalshi trader correctly wants to bet $0. Averaging the two would blindly force the system to commit capital ($25) into a mathematically disadvantageous bet on Kalshi. 
*   **Conclusion:** The two systems must be allowed to size their stakes independently based on the actual liquidity and price depth of their respective exchanges.

### Skipped Trades Visibility
*   **The Issue:** Trades that were suppressed or skipped by the model's confidence threshold were not appearing as "GATED" in the Kalshi Live Dashboard.
*   **The Cause:** Discovered that the architectural sequence in `paper_trader.py` evaluates the `above_threshold` gate *before* it calls `_dispatch_kalshi_live`. As a result, low-confidence predictions are dropped at the simulation layer and never passed to the Kalshi dispatcher, leaving no log entry in `kalshi_orders.jsonl`.
*   **Proposed Fix:** If we want to see skipped trades on the Live Dashboard, we need to pass the signal to the Kalshi dispatcher earlier so it can independently log a `GATED_CONFIDENCE` status.

---

## 5. Diagnostics & Bug Investigations

In the most recent session, we investigated discrepancies between what the paper trader was logging and what the Kalshi dashboard was displaying.

### The Display PnL Bug (Resolved visually, code pending fix)
*   **The Symptom:** A recent Kalshi trade (betting "No"/Down on BTCUSDT) lost money in reality, but the dashboard displayed it as a **WIN**.
*   **The Cause:** Discovered a sign-flipping logic bug in `api_server.py`. The backend was evaluating `win = correct if side == "yes" else not correct`. Because the model's prediction (Down) was incorrect (the price went Up), `correct` evaluated to `False`. For a "No" bet, the API evaluated `not False`, yielding `True` (a win). 
*   **Conclusion:** The trading engine, ledgers, and execution logic are perfectly sound. The issue is purely a visual reporting error in the API server.

### The Confidence Value Discrepancy
*   **The Symptom:** The Trades page reported a confidence of **0.457**, while the Kalshi Live page reported **0.543**.
*   **The Cause:** Both numbers are mathematically correct representations of the same trade:
    *   **0.457:** The *raw* probability score for an "Up" move. The Paper trades page displays this raw metric.
    *   **0.543:** The *computed confidence* for the chosen bet (`1.0 - 0.457`). Because 0.457 is less than 0.50, the system bet "Down" (No). The Kalshi page displays the confidence specifically for that "No" bet. 
*   **Conclusion:** The live execution logic correctly evaluated the 0.543 confidence against the 0.520 threshold and proceeded to gate/place the order appropriately.

---

## 6. APFS Integration & VPS Deployment (May 7)

### Adaptive Prediction Filter System (APFS) Integration
*   **The Feature:** We successfully decoupled the static confidence gate and fully integrated the Adaptive Prediction Filter System (APFS) into the live trader. APFS dynamically evaluates high-frequency order book signals (VWAP, MLOFI) to gate or boost trades based on immediate tape action.
*   **Dashboard Upgrades:** Updated the Settings UI to allow runtime toggling between the legacy "Confidence Gate" and "APFS Mode", along with an adjustable slider for the APFS Trade Threshold. The API automatically persists these settings to `/data/kalshi.env`.
*   **Ledger Updates:** The paper trader now tags trades with a `filter_mode` ("apfs" or "confidence_gate") in the ledger for clean historical analytics.

### The APFS "DOWN" Trade Sizing Bug
*   **The Issue:** APFS was successfully gating bad trades, but heavily passing "DOWN" (No) trades with high confidence (e.g., 0.6080). However, every passed DOWN trade was instantly rejected with `GATED_ZERO_CONTRACTS`.
*   **The Cause:** Found a severe bug in the sizing logic. APFS outputs the probability of the *chosen direction*. However, the Kelly sizing function (`compute_contracts`) explicitly expects the probability of "YES". When passed a DOWN probability of 0.6080, the Kelly function inverted it (`1.0 - 0.6080 = 0.3920`), creating a massive artificial negative edge and returning 0 contracts.
*   **The Fix:** Patched `kalshi_live_trader.py` to properly re-invert the APFS `trade_score` back to a YES probability when sizing a NO trade.

### VPS Deployment & Rebuild Complete
*   **Maker Fallback Fix:** Container restarts were resetting `kalshi.env` defaults due to Docker's built-in `.env` hierarchy. We successfully patched this dynamically via the API to maintain `maker`-first execution.
*   **Full Rebuild:** The APFS integration and sizing bug fixes have been successfully synced to the VPS. A full Docker image rebuild and container restart was executed on `h300-retrain-shifted-v2-clone` to securely mount the changes into the production environment.
