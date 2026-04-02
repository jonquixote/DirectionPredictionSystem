import os
import pandas as pd
import numpy as np
import time
import json
import asyncio
from typing import Dict, List, Any

from cachetools import TTLCache, cached

PARQUET_DIR = os.environ.get("PARQUET_DIR", "/data/parquet")
MODEL_DIR = os.environ.get("MODEL_DIR", "/data/models")

# In-memory cache for fast lookups
_latest_features_cache = TTLCache(maxsize=100, ttl=60)

async def get_ohlcv(symbol: str, interval: str, from_ms: int, to_ms: int) -> dict:
    """
    Load OHLCV candles. Because the backend doesn't aggregate intervals yet,
    we look for aggregated daily/monthly files or default to empty.
    Returns: {"data": [...], "data_gap": bool}
    """
    # Try finding files in the pattern
    # Real implementations scan directory, we simplify here to check a known pattern
    # Example: /data/parquet/orderbook/{symbol}/...
    path = os.path.join(PARQUET_DIR, "orderbook", symbol)
    if not os.path.exists(path):
        return {"data": [], "data_gap": True, "gap_ranges": [{"from_ms": from_ms, "to_ms": to_ms}]}
    
    # We load everything matching the time filter via pyarrow pushdown
    all_files = [os.path.join(path, f) for f in os.listdir(path) if f.endswith(".parquet")]
    if not all_files:
        return {"data": [], "data_gap": True, "gap_ranges": [{"from_ms": from_ms, "to_ms": to_ms}]}
    
    try:
        # Avoid blocking async loop by reading in thread pool
        # In python 3.11 we should run this in executor, for simplicity here we assume small files / index reading
        df = await asyncio.to_thread(
            pd.read_parquet,
            path, # read directory
            filters=[
                [("timestamp", ">=", from_ms), ("timestamp", "<=", to_ms)]
            ],
            engine="pyarrow"
        )
        if df.empty:
            return {"data": [], "data_gap": True}
            
        return {
            "data": df.fillna("").to_dict(orient="records"),
            "data_gap": False
        }
    except Exception as e:
        print(f"Parquet error parsing OHLCV {symbol}: {e}")
        return {"data": [], "data_gap": True}

async def get_feature_timeseries(symbol: str, feature: str, from_ms: int, to_ms: int) -> dict:
    # Look for feature parquets
    path = os.path.join(PARQUET_DIR, "features", f"{symbol}.parquet")
    if not os.path.exists(path):
        return {"data": [], "data_gap": True}
        
    try:
        df = await asyncio.to_thread(
            pd.read_parquet,
            path,
            columns=["timestamp", feature],
            filters=[
                [("timestamp", ">=", from_ms), ("timestamp", "<=", to_ms)]
            ]
        )
        if df.empty:
            return {"data": [], "data_gap": True}
        
        # Rename timestamp to timestamp_ms
        if "timestamp" in df.columns:
            df = df.rename(columns={"timestamp": "timestamp_ms"})
            
        return {
            "data": df.fillna("").to_dict(orient="records"),
            "data_gap": False
        }
    except Exception as e:
        print(f"Parquet error parsing feature {feature} for {symbol}: {e}")
        return {"data": [], "data_gap": True}

async def get_all_features_latest(symbol: str) -> dict:
    if symbol in _latest_features_cache:
        return _latest_features_cache[symbol]
        
    path = os.path.join(PARQUET_DIR, "features", f"{symbol}.parquet")
    if not os.path.exists(path):
        return {}
    
    try:
        # Load just the last row by reading the metadata/tail.
        # This is a bit tricky with raw pandas.read_parquet without loading everything.
        # Fallback: load file and tail(1).
        df = await asyncio.to_thread(pd.read_parquet, path)
        if df.empty:
            return {}
            
        row = df.iloc[-1].fillna("").to_dict()
        _latest_features_cache[symbol] = row
        return row
    except Exception as e:
        return {}

async def get_feature_distributions(symbol: str, model_version: str) -> dict:
    # Try loading training stat
    train_stats_path = os.path.join(MODEL_DIR, model_version, "feature_stats.json")
    if not os.path.exists(train_stats_path):
        # Fallback empty distribution
        return {}
        
    with open(train_stats_path, "r") as f:
        train_stats = json.load(f)
        
    return train_stats
