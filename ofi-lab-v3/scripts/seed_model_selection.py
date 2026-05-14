"""Seed model_selection table with default 'all' strategy for every active (symbol, window).

Run once after schema migration adds the model_selection table.
Safe to re-run (uses INSERT OR REPLACE).
"""
import argparse
import sqlite3


def seed_model_selection(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT DISTINCT symbol, training_horizon_seconds "
        "FROM model_registry WHERE paper_active = 1 AND lifecycle_state != 'suspended'"
    ).fetchall()

    count = 0
    for r in rows:
        conn.execute(
            "INSERT OR REPLACE INTO model_selection "
            "(symbol, market_window_seconds, strategy, selected_model_name, committee_config_json) "
            "VALUES (?, ?, 'all', NULL, '{}')",
            (r["symbol"], r["training_horizon_seconds"]),
        )
        count += 1
    conn.commit()
    return count


def main():
    p = argparse.ArgumentParser(description="Seed model_selection with default 'all' strategy")
    p.add_argument("--db", default="/data/v3.db", help="SQLite database path")
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    n = seed_model_selection(conn)
    print(f"seeded {n} rows into model_selection (strategy='all')")


if __name__ == "__main__":
    main()
