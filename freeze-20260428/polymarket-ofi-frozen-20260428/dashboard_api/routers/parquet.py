from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from services.auth import verify_credentials
from services.parquet_reader import get_ohlcv, get_feature_timeseries

router = APIRouter(tags=["parquet"], dependencies=[Depends(verify_credentials)])

@router.get("/parquet/price")
async def read_parquet_price(
    symbol: str = Query(..., description="E.g. BTCUSDT"),
    interval: str = Query("1m", description="1m, 5m, 15m, 1h"),
    from_ms: int = Query(0),
    to_ms: int = Query(2000000000000)
):
    """OHLCV candle data from Parquet files."""
    return await get_ohlcv(symbol, interval, from_ms, to_ms)

@router.get("/parquet/features")
async def read_parquet_features(
    symbol: str = Query(..., description="E.g. BTCUSDT"),
    features: str = Query(..., description="Comma-separated feature names"),
    from_ms: int = Query(0),
    to_ms: int = Query(2000000000000)
):
    """Feature timeseries data aligned to price."""
    feature_list = [f.strip() for f in features.split(',')]
    result = {}
    
    # In a full implementation we would merge these or read multiple columns efficiently
    # Here we just read them one by one
    for feature in feature_list:
        data = await get_feature_timeseries(symbol, feature, from_ms, to_ms)
        result[feature] = data
        
    return {"data": result, "meta": {"symbol": symbol, "from_ms": from_ms, "to_ms": to_ms}}
