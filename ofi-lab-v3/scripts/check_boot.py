import os, sys
sys.path.insert(0, "/app")

# Simulate what paper_trader.py does at module load
_env_file = "/data/kalshi.env"
if os.path.exists(_env_file):
    with open(_env_file) as _ef:
        for _line in _ef:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Now check what the env says
print("After loading kalshi.env into os.environ:")
for k in ["KALSHI_LIVE_ENABLED", "KALSHI_CONFIDENCE_GATE", "KALSHI_KELLY_FRACTION", "PAPER_CONFIDENCE_THRESHOLD", "EXCHANGE"]:
    print(f"  {k}: {os.environ.get(k, 'NOT SET')}")

# Now create a fresh trader and see what it picks up
from execution.kalshi_live_trader import KalshiLiveTrader
trader = KalshiLiveTrader()
print(f"\nFresh trader config:")
print(f"  enabled:         {trader._config.enabled}")
print(f"  confidence_gate: {trader._config.confidence_gate}")
print(f"  kelly_fraction:  {trader._config.kelly_fraction}")
print(f"  allow_list:      {trader._config.allow_list}")
print(f"  series_ticker:   {trader._config.series_ticker}")
