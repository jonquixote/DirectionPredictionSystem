#!/usr/bin/env bash
# ofi-lab-v3/scripts/entrypoint_v3.sh
set -euo pipefail

if [ ! -f "${STORAGE_DB_PATH}" ]; then
  echo "[entrypoint] bootstrapping ${STORAGE_DB_PATH}"
  python -m scripts.init_db --db "${STORAGE_DB_PATH}" \
    --bootstrap-reason "container start"
fi

# If a v2 snapshot directory is mounted at /data/v2_snapshot, run the
# migration on first boot. Idempotent.
if [ -d "/data/v2_snapshot" ] && [ -z "${SKIP_MIGRATION:-}" ]; then
  echo "[entrypoint] running migration"
  python -m scripts.migrate_jsonl_to_sqlite \
    --db "${STORAGE_DB_PATH}" --source /data/v2_snapshot
fi

exec python -m trading.paper_trader "$@"
