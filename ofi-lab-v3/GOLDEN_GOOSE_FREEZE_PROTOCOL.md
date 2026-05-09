# GOLDEN GOOSE FREEZE PROTOCOL
**Project:** DirectionPredictionSystem — `polymarket-ofi`
**Model:** `h300_v1` | BTCUSDT @ 900s | Gate 0.560 | 67.44% Win Rate
**Frozen:** 2026-04-28
**Status:** 🔒 READ-ONLY ARCHIVE — DO NOT MODIFY

---

## PART 1: WHAT THE GOLDEN GOOSE IS (Theory of Operation)

### The Signal Chain (How It Prints Money)

The system works as a pipeline from raw exchange data to a Polymarket limit order. Every stage matters. A break at any stage silently kills the edge without an error.

```
[Bybit WebSocket L2 Feed]
         │
         ▼
  download_orderbook.py
  Captures raw 200-level order book snapshots every ~1s
  Writes: /data/parquet/orderbook/{SYMBOL}/{DATE}_ob200.parquet
  Format: timestamp_ms | bids[200] | asks[200]
         │
         ▼
  build_features.py           ← PIPELINE STEP 1
  Reconstructs L2 order book from snapshots
  Computes: MLOFI, OFI, spread, mid-price, VWAP
  Writes: /data/features/{SYMBOL}/{DATE}.parquet
  Columns: 22 base features + cts
         │
         ▼
  add_rolling_features.py     ← PIPELINE STEP 2
  Adds time-windowed rolling stats (30s, 60s means + stds)
  Writes: /data/features_v2/{SYMBOL}/{DATE}.parquet
  Columns: 29 features + cts
         │
         ▼
  build_features_v3.py        ← PIPELINE STEP 3
  Adds VWAP enrichment: vwap_2m_deviation, vwap_dev_velocity, vwap_dev_30s_std
  Adds OFI enrichment: mlofi_momentum
  Adds cross-asset features (BTC/ETH/SOL/XRP correlation signals)
  Writes: /data/features_v3/{SYMBOL}/{DATE}.parquet
  Columns: 33 features + cts  ← MUST MATCH config_snapshot FEATURE_COLS
         │
         ▼
  run_training.py --horizon 300 --feature-dir /data/features_v3
  LightGBM binary classifier
  Label: 1 if close_price[t+300s] > close_price[t], else 0
  Output: /data/models/run_{TIMESTAMP}/model.lgb
         │
         ▼
  paper_trader.py (Container: polymarket-ofi-paper-trader)
  Loads model from /data/models/latest_h300/ (symlink)
  Streams live Bybit L2 → runs same feature pipeline in-memory → model.predict_proba()
  Gate: confidence >= 0.55 (DEPLOY_GATE in paper_trader.py)
  *** H300_SUPPRESS_DURATIONS = {"h300": {300}} ***
      ↑ CRITICAL: h300 is ONLY deployed on 900s contracts, NOT 300s
      ↑ This is intentional — the 5-min signal fires on 15-min contracts
  If confidence >= gate: discover next Polymarket BTC Up/Down 900s contract
  Compute Polymarket CLOB midpoint for contract
  Post limit order at midpoint ± edge
  Log to: /data/logs/BTC_900s_paper_trader.jsonl
```

### Why h300_v1 Dominates on 900s (The Emergent Property)

The model was trained to predict 5-minute direction (`HORIZON_SECONDS=300`).
It is deployed on 15-minute (900s) contracts. This works because:

1. **The signal is "early"** — order flow imbalance (MLOFI) detects institutional
   positioning that takes 15 minutes to fully materialize in price
2. **The longer contract has less noise** — 15 minutes is enough time for the
   directional signal to overcome microstructure randomness
3. **The gate amplifies this** — at confidence >= 0.56, the model is only firing
   when the order flow signal is massive and unambiguous

**PRESERVE THIS SUPPRESSION — IT IS THE GOLDEN GOOSE'S SECRET.**
Never deploy h300 on 300s contracts. Never remove `H300_SUPPRESS_DURATIONS`.

### The Exact Golden Numbers

| Parameter | Value | Source |
|:----------|:------|:-------|
| Model file | `model.lgb` | `/data/models/latest_h300/` |
| Model MD5 | `8c25ce5c0e56fec14989a130094af0f9` | Verified 2026-04-27 |
| Horizon | 300s (5-min direction) | `config_snapshot.json` |
| Feature schema | 33 columns | `feature_names.json` |
| Feature dir | `/data/features_v3/` | Confirmed via column matching |
| TRAIN_END | 2025-12-31 | `config_snapshot.json` |
| VAL_END | 2026-02-15 | `config_snapshot.json` |
| n_estimators | 500 | `config_snapshot.json` |
| learning_rate | 0.05 | `config_snapshot.json` |
| num_leaves | 63 | `config_snapshot.json` |
| max_depth | 6 | `config_snapshot.json` |
| min_child_samples | 100 | `config_snapshot.json` |
| subsample | 0.8 | `config_snapshot.json` |
| colsample_bytree | 0.8 | `config_snapshot.json` |
| random_state | 42 | `config_snapshot.json` |
| Deploy gate | 0.55 (paper_trader default) | `paper_trader.py` |
| Deploy asset | BTCUSDT only | Confirmed from logs |
| Deploy duration | 900s only | `H300_SUPPRESS_DURATIONS` |
| Optimal live gate | 0.560 | Master grid search |
| Peak win rate | 67.44% at gate 0.56 | 129 trades, W13–W16 |

---

## PART 2: FREEZE EXECUTION SCRIPT

Run these commands IN ORDER. Do not proceed to the next step until the current
step is verified. Each step includes a verification check.

### STEP 0: Pre-flight Check

```bash
# Confirm container is still running
ssh polymarket-server "docker ps | grep polymarket-ofi-paper-trader"

# Confirm model file hash has not changed
ssh polymarket-server "md5sum /data/models/latest_h300/model.lgb"
# EXPECTED: 8c25ce5c0e56fec14989a130094af0f9  /data/models/latest_h300/model.lgb

# Confirm disk space for image save (~3-5GB compressed)
ssh polymarket-server "df -h /data"
```

### STEP 1: Commit the Running Container to a Frozen Image

```bash
# Commit the live container (captures runtime state + all installed packages)
ssh polymarket-server "docker commit \
  --message 'GOLDEN GOOSE FREEZE 2026-04-28: h300_v1 peak model, BTC 900s, 67.44% WR' \
  polymarket-ofi-paper-trader \
  golden-goose-h300v1:frozen-20260428"

# Verify image was created
ssh polymarket-server "docker images | grep golden-goose-h300v1"
```

### STEP 2: Save and Compress the Docker Image

```bash
# Save to compressed tar.gz (~3-5 GB depending on layers)
ssh polymarket-server "docker save golden-goose-h300v1:frozen-20260428 | gzip -9 > /data/golden-goose-h300v1-frozen-20260428.tar.gz"

# Verify size and generate checksum
ssh polymarket-server "ls -lh /data/golden-goose-h300v1-frozen-20260428.tar.gz"
ssh polymarket-server "md5sum /data/golden-goose-h300v1-frozen-20260428.tar.gz | tee /data/golden-goose-h300v1-frozen-20260428.tar.gz.md5"
```

### STEP 3: Archive All Model Artifacts

```bash
# Create a timestamped artifact archive
ssh polymarket-server "cd /data && tar -czf golden-goose-artifacts-20260428.tar.gz \
  models/latest_h300/ \
  models/run_20260326_092407/ \
  models/run_20260326_081107/ \
  logs/BTC_900s_paper_trader.jsonl \
  logs/training_v3_h300.log"

# Verify archive contents
ssh polymarket-server "tar -tzf /data/golden-goose-artifacts-20260428.tar.gz"
```

### STEP 4: Archive All Training Data (Parquet + Features)

```bash
# This is large (~40-50GB). Use rsync to pull to local NAS or external storage.
# Do NOT store on VPS alone — single point of failure.

# Option A: rsync to local machine (slow but safest)
rsync -avzP --progress \
  polymarket-server:/data/parquet/orderbook/ \
  ~/Code/DirectionPredictionSystem/freeze-20260428/parquet/orderbook/

rsync -avzP --progress \
  polymarket-server:/data/features_v3/ \
  ~/Code/DirectionPredictionSystem/freeze-20260428/features_v3/

# Option B: tar on server first, then pull (faster transfer)
ssh polymarket-server "tar -czf /data/golden-goose-features-v3-20260428.tar.gz /data/features_v3/"
scp polymarket-server:/data/golden-goose-features-v3-20260428.tar.gz ~/Code/DirectionPredictionSystem/freeze-20260428/
```

### STEP 5: Archive the Codebase (Frozen Snapshot)

```bash
# Pull the exact code that generated h300_v1
rsync -avzP \
  polymarket-server:/app/DirectionPredictionSystem/polymarket-ofi/ \
  ~/Code/DirectionPredictionSystem/freeze-20260428/polymarket-ofi-frozen-20260428/

# Generate file manifest with timestamps (proves nothing was modified)
ssh polymarket-server "find /app/DirectionPredictionSystem/polymarket-ofi -type f \
  -exec stat --format='%Y %n' {} \; | sort > /tmp/codebase_manifest_20260428.txt"
scp polymarket-server:/tmp/codebase_manifest_20260428.txt \
  ~/Code/DirectionPredictionSystem/freeze-20260428/codebase_manifest_20260428.txt
```

### STEP 6: Pull All Archives to Local Machine

```bash
# Pull everything
scp polymarket-server:/data/golden-goose-h300v1-frozen-20260428.tar.gz \
  ~/Code/DirectionPredictionSystem/freeze-20260428/

scp polymarket-server:/data/golden-goose-artifacts-20260428.tar.gz \
  ~/Code/DirectionPredictionSystem/freeze-20260428/

scp polymarket-server:/data/golden-goose-h300v1-frozen-20260428.tar.gz.md5 \
  ~/Code/DirectionPredictionSystem/freeze-20260428/
```

### STEP 7: Verify the Freeze is Restorable

```bash
# Test that the image can be loaded from archive (on any machine)
docker load < ~/Code/DirectionPredictionSystem/freeze-20260428/golden-goose-h300v1-frozen-20260428.tar.gz

# Verify the model binary inside the restored image
docker run --rm golden-goose-h300v1:frozen-20260428 \
  md5sum /data/models/latest_h300/model.lgb
# EXPECTED: 8c25ce5c0e56fec14989a130094af0f9
```

### STEP 8: Write the Freeze Receipt

Create `~/Code/DirectionPredictionSystem/freeze-20260428/FREEZE_RECEIPT.txt`:
```
GOLDEN GOOSE FREEZE RECEIPT
============================
Date: 2026-04-28
Operator: [YOUR NAME]

Container image:    golden-goose-h300v1-frozen-20260428.tar.gz
Image MD5:          [fill in from step 2]
Model binary MD5:   8c25ce5c0e56fec14989a130094af0f9
Artifacts archive:  golden-goose-artifacts-20260428.tar.gz
Features archive:   golden-goose-features-v3-20260428.tar.gz
Code snapshot:      polymarket-ofi-frozen-20260428/
Trade log:          logs/BTC_900s_paper_trader.jsonl

Restore test:       PASSED [date] — model MD5 verified inside restored container
```

---

## PART 3: RESTORE PROCEDURE

If you ever need to bring the exact original system back from the frozen archive:

```bash
# 1. Load the frozen Docker image
docker load < /path/to/golden-goose-h300v1-frozen-20260428.tar.gz

# 2. Start the container exactly as the original ran
docker run -d \
  --name golden-goose-restored \
  --restart unless-stopped \
  -v /data:/data \
  golden-goose-h300v1:frozen-20260428 \
  python trading/paper_trader.py

# 3. Verify model hash inside running container
docker exec golden-goose-restored md5sum /data/models/latest_h300/model.lgb
# MUST MATCH: 8c25ce5c0e56fec14989a130094af0f9
```
