# The Complete Polymarket Bot Analysis Master Document
*Generated: April 27, 2026*

## 1. System Architecture & The "Three Containers" Discovery
After a complete audit of the `polymarket-server` logs located in `/data/logs/*.jsonl`, we uncovered a critical detail that explains all previously conflicting performance metrics. 

There are actually **three distinct Docker containers** orchestrating trades in parallel, each utilizing different models, bet sizing, and confidence thresholds. 

### A. The Standard Trader (`polymarket-ofi-paper-trader`)
*Created: April 1, 2026*
* **Models Running:** `h300 (v1)`, `h60 (v1)`, `h60 (v3)`
* **Assets:** BTCUSDT, SOLUSDT @ 300s & 900s horizons.
* **Strategy:** Conservative. Applies a strict **0.55 Confidence Threshold**. If the model is not 55% sure, it doesn't trade.
* **Bankroll:** **Flat $10.00 Stake**. It does not compound; it simulates a rigid $10 bet on every trade to measure unit accuracy.

### B. The Aggressive Compounder (`btc900s-trader`)
*Created: April 5, 2026*
* **Models Running:** `h300 (v1)` and `h60 (v1)`.
* **Assets:** BTCUSDT @ 900s ONLY.
* **Strategy:** Hyper-aggressive. **No Confidence Threshold.** It trades every single 15-minute prediction indiscriminately. Both models trade independently into the same pool.
* **Bankroll:** Compounding from an initial **$10.00**. Uses an aggressive Kelly fraction (12% of bankroll for `h300` predictions, 3% for `h60` predictions).

### C. The v2 Specialist (`h60v2-trader`)
*Created: April 5, 2026*
* **Models Running:** `h60 (v2)`
* **Assets:** BTCUSDT @ 900s ONLY.
* **Strategy:** Runs the v2 model in isolation.
* **Bankroll:** Compounding from an initial **$10.00**. Uses a conservative 5% Kelly fraction.

---

## 2. The Myth of the "h300 v3" Model
During the audit, we searched for the logs and files relating to the `h300` version 3 model. 
**Conclusion: It does not exist on the server.**
Checking the training logs (`/data/logs/training_v3_h300.log`), we found that the model failed its validation gate on March 26th (`Gate: FAILED ✗ (AUC_full=0.5159, AUC_contract=0.5174)`). Because it failed the gate, the script rightfully threw the model away, and it was never deployed to any container.

---

## 3. Equity Curve & Container Performance Post-Mortems

### The Standard Trader (The Golden Goose)
Because this container uses flat bet sizing and strict confidence thresholds (0.55), it provides the most accurate reflection of raw model capability. 
* **The Winner:** `h300 (v1)` on the 900s horizon dominated. By using the confidence threshold, it achieved an incredible **55.1% win rate** over 1,273 trades, generating **+$1,081.52** in pure profit.
* **The Losers:** `h60 (v3)` at 900s lost -$863.01. The 300s horizons across all models bled heavily.

### The Aggressive Compounder (The Blow Up)
* **Starting Bankroll:** $10.00
* **Peak Bankroll:** $26.39
* **Current Bankroll:** **$0.98** 🩸 (Effectively liquidated)
* **Max Drawdown:** 96%+ 
* **Breakdown by Model Inside Container:**
  * `h60 (v1)` (3% Kelly): 2,272 trades, 52.02% win rate. **Net Contribution: +$7.09**
  * `h300 (v1)` (12% Kelly): 2,272 trades, 50.62% win rate. **Net Contribution: -$16.11**
* **Why it failed:** Removing the confidence threshold was fatal. By forcing the models to trade through low-edge chop, their win rates plummeted to a coin flip (~50-52%). A 51% win rate cannot mathematically sustain a 12% compounding Kelly bet against Polymarket's fees. It bled the account dry.

### The v2 Specialist (The Survivor)
* **Starting Bankroll:** $10.00
* **Peak Bankroll:** $36.58
* **Current Bankroll:** **$29.16** 🟢 (Up 191%)
* **Max Drawdown:** ~89%
* **Why it survived:** Even though the `h60 v2` model is deeply flawed (see threshold analysis below), the container survived and tripled its money purely because of **conservative risk management**. By restricting bets to 5% of the bankroll, it weathered the drawdowns.

---

## 4. The Threshold Sweep: Probability Calibration Insights
The most important discovery of the audit was found when we analyzed win rates across different confidence levels. We asked: *Did the v2 and v3 models get worse, or do they just need stronger gates?*

**1. `h60 v3` is actually a monster model.**
At the standard 0.55 gate, `v3` loses heavily (48.72% win rate). But its probabilities are simply "inflated" compared to v1. When we raise the gate to **0.60**, it filters out the noise and achieves a **54.43% win rate** over 305 trades, averaging +$0.71 per trade. 
*Action:* Do not retrain v3. Just raise its threshold to 0.60.

**2. `h60 v2` is fundamentally broken.**
When we raised the confidence gate on `v2`, its win rate actually *dropped* from 53% down to 44%. It exhibits the dangerous trait of being overconfident when it is wrong. It should be decommissioned immediately. 

**3. `h300 v1` remains the King.**
At the 0.55 gate, it is the most robust performer. At an extreme 0.60 gate, its win rate hits an absurd **63.6%** (though trades become very rare).

---

## 5. The Master Grid Search Simulation (All Models, All Containers)
To definitively answer the question of maximum potential, we aggregated **every single paper trade log** across all three containers (`paper_trader`, `btc900s-trader`, `h60v2-trader`), combining all raw probability distributions. 

We mapped the entire trade stream over the last month and ran a sweeping grid search (Gates: 0.500 to 0.750, Kelly: 1% to 50%) to find the absolute maximum compounding peak from a $10.00 start. 

### The Findings (The $14k Breakthrough)
When looking at the *entire* dataset and correctly grouping by Asset and Model, we found that `h300 v1` on `BTCUSDT` is capable of generating staggering exponential returns.

**1. The True Holy Grail: `h300 (v1)` on BTCUSDT @ 900s**
* **Optimal Gate:** `0.560`
* **Optimal Kelly Fraction:** `33%`
* **Trades Executed:** 129
* **Win Rate:** 67.44%
* **Peak Bankroll:** **$13,986.46** (from $10.00)
* *Note:* At a 0.56 gate, the model achieves a phenomenal 67% win rate. Because of this massive edge, the Kelly criterion allows for a massive 33% position size, compounding $10 into $14,000 in a single month.

**2. The Sniper: `h60 (v1)` on BTCUSDT @ 900s**
* **Optimal Gate:** `0.685`
* **Optimal Kelly Fraction:** `50%`
* **Trades Executed:** 26
* **Win Rate:** 76.92%
* **Peak Bankroll:** **$431.75** (from $10.00)

**3. The Surprise: `h60 (v2)` on BTCUSDT @ 900s**
* **Optimal Gate:** `0.640`
* **Optimal Kelly Fraction:** `50%`
* **Trades Executed:** 23
* **Win Rate:** 78.26%
* **Peak Bankroll:** **$387.86** (from $10.00)
* *Note:* While `v2` is broken at lower confidences, at extreme confidences (>0.64), it is hyper-accurate. 

---

## 6. Model Decay & Retraining Feasibility
Since `h300_v1` was trained in late March, we conducted a deep chronological decay analysis to determine if the $14k mathematical peak was heavily front-loaded. 

### Performance Deterioration
The chronological win rate of `h300_v1` on BTCUSDT @ 900s (gated at 0.560) shows definitive decay:
* **Week 13 & 14 (Late March/Early April):** 75.00% win rate
* **Week 15 & 16 (Mid April):** 65% - 75% win rate
* **Week 17 (Late April):** 50.00% win rate
* **Week 18 (Current Week):** 50.00% win rate

The edge has vanished entirely over the last 1.5 weeks. The model is now acting as a coin flip and **must be retrained** to adapt to the current market regime.

### Retraining Reproducibility
To ensure we can duplicate the winning model exactly, an audit of the server's training infrastructure was performed:
1. **Pristine Codebase:** The core code in `/feature_engineering/`, `/models/`, and `/validation/run_training.py` has not been modified since **March 22** and **March 26**.
2. **Exact Configuration Saved:** Inside the `/data/models/latest_h300/` directory, the model saved its exact configuration (`config_snapshot.json`). This gives us the precise blueprint:
   * 33 exact features (e.g., `mlofi`, `vwap_deviation`, `mlofi_30s_std`).
   * LGBM hyperparameters (`learning_rate: 0.05`, `num_leaves: 63`, `n_estimators: 500`).
   * The specific data boundaries (`TRAIN_END: 2025-12-31`, `VAL_END: 2026-02-15`).

We are 100% capable of cloning the environment and retraining the exact same architecture on updated dates without introducing unknown variables.

---

## 7. Final Recommendations
1. **Kill the Aggressive Compounder (`btc900s-trader`).** Trading without a confidence threshold is mathematical suicide.
2. **Clone and Retrain `h300_v1`.** 
   * Spin up a cloned container.
   * Update the `TRAIN_END` and `VAL_END` dates in `run_training.py` to include the post-crash data from March and April.
   * Retrain the model using the exact blueprint found in `latest_h300/config_snapshot.json`.
3. **Deploy the "Master Compounder" Strategy:**
   * Once retrained, deploy the new `h300` on **BTCUSDT ONLY** with a strict **0.560 Confidence Gate** and a **33% Kelly Fraction**.
   * Deploy `h60 (v1) @ 900s` on **BTCUSDT ONLY** with a **0.685 Confidence Gate** to act as a hyper-accurate sniper.
