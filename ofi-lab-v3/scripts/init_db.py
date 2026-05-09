"""Bootstrap an empty v3 SQLite database.

Usage:
    python -m scripts.init_db --db /data/v3.db --bootstrap-reason "v3 launch"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as a script from the project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage.db import open_database, init_schema, DEFAULT_DB_PATH
from storage.registry_state import RegistryState


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=DEFAULT_DB_PATH)
    p.add_argument("--bootstrap-reason", default="initial v3 boot")
    args = p.parse_args()

    conn = open_database(args.db)
    init_schema(conn)
    rs = RegistryState(conn)
    rs.bootstrap_if_empty(reason=args.bootstrap_reason)
    print(f"v3.db initialized at {args.db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
