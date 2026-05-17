# Fleet Retraining & Modularity Plan

> **Goal:** Fix the catastrophic training data bug, establish a modular and scriptable retraining pipeline, and expose training controls to the React frontend.

**Date:** 2026-05-12

## Current State of the Data
An investigation of the VPS reveals exactly what data is present in the `/data/features_v3/` directory:
- **BTCUSDT:** 365 days (2025-04-29 to 2026-04-28)
- **ETHUSDT:** 365 days (2025-04-29 to 2026-04-28)
- **SOLUSDT:** 365 days (2025-04-29 to 2026-04-28)
- **XRPUSDT:** 365 days (2025-04-29 to 2026-04-28)

**Crucial Finding:** The data is currently stale by about 14 days. The last available `.parquet` feature file is from April 28th, 2026. 

---

## Prerequisites for Tonight's Training (Data Gap)

If you want to train 84 new models tonight at 00:01 UTC with data up to 00:00 UTC, the system requires the missing 14 days of feature files (from April 29th to May 12th). 

**What needs to be done:**
1. You (or the data pipeline script that originally generated `features_v3`) must generate the 1-minute feature `.parquet` files for the missing 14 days.
2. These files must be uploaded to the VPS and placed into their respective directories: `/data/features_v3/BTCUSDT/`, etc.
3. Once the files are present, the training pipeline will automatically pick them up when scanning the directory.

---

## Phase 1: Fixing the Root Cause (The Data Mix Bug)

The reason all 84 models trained on identical data is located in `validation/run_training.py`. We must fix two functions to ensure models actually respect their specific symbols and training windows.

### 1. Fix `load_features()`
Currently, it loops over *all* symbols and concatenates them.
**Fix:** Modify the function signature to `load_features(feature_dir: Path, target_symbol: str)` and only load files from `/data/features_v3/<target_symbol>`.

### 2. Fix the Train Window Split
Currently, it only truncates the end date (`df[df["date"] <= train_end]`).
**Fix:** Modify Phase 3 of the script to strictly enforce both bounds:
```python
df_train = df[(df["date"] >= train_start) & (df["date"] <= train_end)].reset_index(drop=True)
```

---

## Phase 2: CLI Modularity (Surgical Updates)

> **Important Note:** We must be very surgical with these changes. The core LightGBM training process is fundamentally working well, so we will not cut or alter the underlying training mechanics. We will only patch the data loading boundaries and expose the existing parameters.

The current script (`scripts/train_fleet.py`) is designed as a "train everything all at once" hammer. We will modularize it so you can surgically run specific subsets via the CLI.

**Enhancements to `train_fleet.py`:**
- **Walk-Forward Validation Toggle:** Expose a `--walk-forward` flag (defaulting to off/skipped, as it is now) so you can optionally enable it.
- **Explicit Filtering:** Ensure the script respects granular inputs so you can train just one model, a subset, or the entire fleet.
- **Customizable Windows:** Support arbitrary training windows (e.g. 46 days, 90 days).
- Example surgical usage: 
  `python scripts/train_fleet.py --symbols BTCUSDT,ETHUSDT --horizons 900 --train-days 46 --train-end 2026-05-12 --walk-forward`

---

## Phase 3: Frontend Training Control (The "Command Center")

To allow you to fully control training from the React frontend, we will build a bridge between the dashboard UI and the Python CLI.

### 1. Backend: FastAPI Training Router (`dashboard_api/routers/training.py`)
- Create a `POST /api/training/dispatch` endpoint.
- The endpoint will accept JSON configuration: `{ symbols: ["BTCUSDT", "ALL"], horizons: [900, 1800], train_days: [46, 90], train_end: "2026-05-12", walk_forward: false }`.
- Using FastAPI's `BackgroundTasks`, it will invoke `subprocess.Popen` to run `scripts/train_fleet.py` with the provided arguments.
- Create a `GET /api/training/status` endpoint to tail the training logs and report progress back to the UI.

### 2. Frontend: The "Fleet Training" Tab
- Build a new React page (`dashboard/src/pages/FleetTraining.tsx`).
- Provide explicit UI controls for:
  - **Symbols:** Checkboxes for BTC, ETH, SOL, XRP (with "Select All" / "Select Some").
  - **Prediction Window (Horizon):** Toggles for 60s, 300s, 900s, etc.
  - **Training Window:** Fully customizable input (e.g., 90 days, 180 days, 46 days).
  - **Walk-Forward Validation:** On/Off toggle (defaults to Off).
  - **Date Boundaries:** DatePicker for `train_end` (defaulting to today).
- Add a "Dispatch Training" button that can launch a single model or the entire fleet based on the selections.
- Add a live terminal window component to stream the training logs so you can watch LightGBM iterate and save the models in real-time.

---

## Execution Order
1. **You:** Run the external data pipeline to generate the May 2026 `.parquet` feature files and drop them in `/data/features_v3/`.
2. **Me:** Implement Phase 1 (Fixing `run_training.py`).
3. **Me:** Implement Phase 2 (Enhancing `train_fleet.py` modularity).
4. **Me:** Implement Phase 3 (FastAPI router + React Frontend).
