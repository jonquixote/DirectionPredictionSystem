"""Load fleet from model_registry table."""
import json
import sqlite3


def load_active_fleet(conn: sqlite3.Connection) -> list[dict]:
    """
    Load all paper_active, non-suspended models from model_registry.

    Args:
        conn: SQLite database connection with row_factory set to sqlite3.Row

    Returns:
        List of model dicts with keys: name, symbol, training_horizon_seconds,
        artifact_path, feature_names_path, evaluation_windows (as list),
        generation, is_baseline, lifecycle_state
    """
    rows = conn.execute(
        """
        SELECT name, symbol, training_horizon_seconds, artifact_path,
        feature_names_path, evaluation_windows, generation, is_baseline,
        lifecycle_state, filter_config_json, platform_active_json
        FROM model_registry
        WHERE paper_active = 1 AND lifecycle_state != 'suspended'
        ORDER BY is_baseline DESC, training_horizon_seconds, name
        """
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["evaluation_windows"] = json.loads(
            d["evaluation_windows"] or "[300,900,1800]"
        )
        d["filter_config"] = json.loads(
            d.pop("filter_config_json", None) or "{}"
        )
        d["platform_active"] = json.loads(
            d.pop("platform_active_json", None) or '{"paper":true,"kalshi":false,"polymarket":false}'
        )
        out.append(d)
    return out
