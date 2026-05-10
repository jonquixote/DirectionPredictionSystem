# ofi-lab-v3/tests/fixtures/

Pytest test fixtures for v3.

## Generated at runtime

- `tiny_model_path` (conftest fixture) — small LightGBM model trained on synthetic data, shaped like `V3_FEATURE_COLS`. Created in a session-scoped tmp dir; not committed to the repo.
- `synthetic_minute_bars_path` (conftest fixture) — 10-row parquet with V3_FEATURE_COLS + ts_ms + mid_price columns. For replay smoke test.

## Committed fixtures

- `v2_logs/predictions_h300.jsonl` — sample v2 predictions JSONL for migration test
- `v2_logs/paper_trades_h300.jsonl` — sample v2 paper trades JSONL for migration test
