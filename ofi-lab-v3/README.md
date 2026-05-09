# Polymarket Crypto Direction Prediction System

Model trained, validated, and deployed on **Bybit spot**. Historical L2 data sourced from Bybit's free historical data portal. Live data via Bybit spot WebSocket `orderbook.200.{symbol}`. No Binance data used anywhere in the pipeline.

Architecture includes `AbstractOrderBookFeed` for future venue additions (e.g. Coinbase). Signal transferability to other venues is an empirical question, not an assumption.

## Architecture

```
polymarket-ofi/
├── api/
│   ├── feed.py            # AbstractOrderBookFeed (ABC)
│   ├── bybit.py           # BybitOrderBookManager (primary)
│   ├── binance.py         # BinanceOrderBookManager (legacy, not used)
│   └── polymarket.py      # Polymarket CLOB book parser
├── data/
│   ├── download_orderbook.py  # Bybit L2 ZIP → Parquet downloader
│   └── download_klines.py     # Bybit OHLCV REST → Parquet downloader
├── feature_engineering/
│   ├── features.py        # FeatureBuilder (tiers 1A–3)
│   ├── mlofi.py           # Multi-Level OFI calculator
│   └── selection.py       # MI-based feature selection
├── models/
│   ├── track_a.py         # MLP (64→256→128→64→1)
│   ├── track_b.py         # CNN-BiLSTM-Attention
│   ├── meta_learner.py    # Isotonic calibration + logistic stacking
│   └── loss.py            # GMADL loss
├── validation/
│   ├── splitter.py        # Temporal split + boundary enforcement
│   ├── walk_forward.py    # Walk-forward validation
│   └── leakage_check.py   # Accuracy > 62% leakage flag
├── execution/
│   ├── executor.py        # Main execution loop
│   ├── gates.py           # Structural, adverse selection, Sanderink gates
│   └── fee_regime.py      # Fee regime checker
├── monitoring/
│   ├── psi.py             # Distribution + predictive PSI
│   └── alerts.py          # Alert system
├── trade_logging/
│   ├── writer.py          # Thread-safe SQLite logger
│   └── schema.sql         # candidate_trades schema
├── config.py              # All configuration (Bybit endpoints, etc.)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Data Sources

| Source | Type | Coverage |
|--------|------|----------|
| Bybit S3 (`quote-saver.bycsi.com`) | L2 order book (200 levels) | 2025-04-29 → present |
| Bybit REST (`/v5/market/kline`) | 1-min OHLCV | Full history |
| Bybit WebSocket (`orderbook.200.{symbol}`) | Live L2 stream | Real-time |

## Deployment

```bash
# Build and run
docker compose build
docker compose run --rm -v /data:/data training bash

# Download historical data
python -m data.download_orderbook --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT \
    --start-date 2025-04-29 --end-date 2026-03-23 --output-dir /data/parquet/orderbook --parallel

# Download OHLCV
python -m data.download_klines --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT \
    --start-date 2025-04-29 --end-date 2026-03-23 --output-dir /data/parquet/klines

# Run tests
python -m pytest tests/ -v
```

## Bankroll

- **$100** paper trading budget
- **200 resolved markets** gate before any live capital
- Fee-only break-even at p ≈ 0.50: ~50.8%
- Practical break-even with spread/slippage: 51–52%
