# VPS Architecture & Deployment Guide: Golden Goose V2

This document provides a comprehensive operational guide for managing, syncing, and deploying the live trading system to the remote Virtual Private Server (VPS). It is intended for future developers to understand the infrastructure without needing to reverse-engineer Docker commands or hunt for SSH keys.

## 1. Server Access & Discovery

The live trading system is hosted on a Google Cloud VM (referred to as Server 2 or `vps-n2`), but is accessed directly via raw SSH rather than through the `gcloud` CLI.

- **IP Address:** `34.67.75.48`
- **Username:** `johnny`
- **SSH Key:** `~/.ssh/id_vps_n2`

**To connect to the server:**
```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48
```

The core repository mirror on the VPS is located at `/home/johnny/ofi-lab/`.

## 2. Container Configuration & Memory State

The live trading system runs inside a single, monolithic Docker container.

- **Container Name:** `h300-retrain-shifted-v2-clone`
- **Image Name:** `golden-goose-v2:clone`
- **Internal Working Directory:** `/app`

> [!WARNING]
> **Do not indiscriminately run `docker rm` on this container.** 
> The container was booted with ~20 distinct environment variables injected directly into its configuration (via `docker run` or `--env-file`). These variables include `KALSHI_API_KEY_ID`, `EXCHANGE=kalshi`, `DASHBOARD_PASSWORD`, and various `KALSHI_LIVE_*` settings. Destroying the container means you must perfectly reconstruct this environment variable block to start it again.

### The `/data` Volume Mount
The container mounts the host's `/data` directory to `/data` internally. **This is where all persistent state lives.**
- `/data/models/` and `/data/models_retrain_shifted/` (The LightGBM models)
- `/data/kalshi_orders.jsonl` (The live trading ledger)
- `/data/logs_model_a_clone/` (System logs)
- `/data/kalshi_private_key.pem` (The RSA private key for Kalshi API signing)

### In-Memory Kill Switch
The Kalshi live trading kill switch (`KalshiLiveTrader.enabled`) is **memory-only**.
Upon container restart, it defaults to the `KALSHI_LIVE_ENABLED` environment variable. 
You can dynamically inject state into the active memory via the API (port `8080`):
```bash
# Enable trading
curl -X POST http://localhost:8080/kalshi/enable -H "Authorization: Bearer <DASHBOARD_PASSWORD>"

# Disable trading
curl -X POST http://localhost:8080/kalshi/disable -H "Authorization: Bearer <DASHBOARD_PASSWORD>"
```

## 3. Synchronization Protocol

The VPS does **not** use `git pull` for deployments. The local Mac `ofi-lab` folder is pushed directly to the VPS using `rsync`.

**The exact command to sync from local to VPS:**
```bash
rsync -avz --exclude '.git' --exclude '__pycache__' -e "ssh -o StrictHostKeyChecking=no -i ~/.ssh/id_vps_n2" /Users/johnny/Code/DirectionPredictionSystem/ofi-lab/ johnny@34.67.75.48:/home/johnny/ofi-lab/
```

> [!TIP]
> Always run this command before deploying. The `exclude` flags ensure you don't overwrite server-specific Python environments or send unnecessary version control history over the network.

## 4. The Hot-Patch Deployment Workflow

Because the container has critical environment variables baked into its configuration, **the safest way to update Python logic is to patch the running container** rather than rebuilding the image and recreating the container.

### Step 1: Sync the Code
Run the `rsync` command above to get the latest code onto the VPS filesystem (`/home/johnny/ofi-lab/`).

### Step 2: Inject Files into the Container
SSH into the VPS and use `docker cp` to copy the updated files from the host into the container's `/app` directory.
```bash
ssh -i ~/.ssh/id_vps_n2 johnny@34.67.75.48

# Example: Updating the API Server and Live Trader
docker cp /home/johnny/ofi-lab/trading/api_server.py h300-retrain-shifted-v2-clone:/app/trading/api_server.py
docker cp /home/johnny/ofi-lab/execution/kalshi_live_trader.py h300-retrain-shifted-v2-clone:/app/execution/kalshi_live_trader.py
```

### Step 3: Restart the Container
A simple restart reloads the Python processes with the newly copied files while perfectly preserving the volume mounts and injected environment variables.
```bash
docker restart h300-retrain-shifted-v2-clone
```

> [!IMPORTANT]
> **The 30-Minute Warmup Rule**
> Every time you restart the container, the WebSocket streams are reset. The feature engineering pipeline requires 30 minutes of live Orderbook (L2) data to calculate its Moving Average Deviations (MAD). **The model will completely skip all predictions for exactly 30 minutes after a restart.**

## 5. Rebuilding the Docker Image (Hard Deploy)

If you modify `requirements.txt` or need to alter the `Dockerfile.clone`, a hot-patch won't work. You must rebuild the image.

1. SSH into the VPS and navigate to the project root:
   ```bash
   cd /home/johnny/ofi-lab
   ```
2. Build the new image:
   ```bash
   docker build -t golden-goose-v2:clone -f Dockerfile.clone .
   ```
3. To apply the new image, you must stop and remove the old container, then execute a `docker run` command. **You must ensure you pass the `--env-file .env` flag or manually specify all Kalshi environment variables**, otherwise the container will fail to authenticate with the exchange.
