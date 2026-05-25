"""Manual tier promotion / demotion CLI for model governance.

Usage:
    python -m scripts.promote_model --model NAME --to-tier TIER --reason "..." [--db /data/v3.db] [--user opus]

TIER must be one of: gold, silver, watch, retired
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone


_VALID_TIERS = frozenset({"gold", "silver", "watch", "retired"})
_KELLY_BY_TIER: dict[str, float] = {
    "gold": 1.0,
    "silver": 0.3,
    "watch": 0.0,
    "retired": 0.0,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def promote_model(
    *,
    conn: sqlite3.Connection,
    model_name: str,
    to_tier: str,
    reason: str,
    user: str = "manual",
) -> dict:
    """Promote or demote a model to a new tier.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        model_name: Name of the model in model_registry
        to_tier: Target tier (gold, silver, watch, retired)
        reason: Human-readable reason logged to governance_actions
        user: Triggered-by label (e.g. 'opus', 'admin')

    Returns:
        dict with before/after state

    Raises:
        SystemExit(1) on validation failure.
    """
    if to_tier not in _VALID_TIERS:
        print(f"ERROR: invalid tier '{to_tier}'. Must be one of {sorted(_VALID_TIERS)}", file=sys.stderr)
        sys.exit(1)

    row = conn.execute(
        "SELECT name, tier, kelly_multiplier, symbol, training_horizon_seconds, train_days "
        "FROM model_registry WHERE name = ?",
        (model_name,),
    ).fetchone()
    if not row:
        print(f"ERROR: model '{model_name}' not found in model_registry", file=sys.stderr)
        sys.exit(1)

    from_tier = row["tier"] or "watch"
    if from_tier == to_tier:
        print(f"NOOP: model '{model_name}' is already tier='{to_tier}'")
        return {
            "model_name": model_name,
            "from_tier": from_tier,
            "to_tier": to_tier,
            "kelly_before": row["kelly_multiplier"],
            "kelly_after": _KELLY_BY_TIER[to_tier],
            "changed": False,
        }

    symbol = row["symbol"]
    horizon = row["training_horizon_seconds"]
    train_days = row["train_days"]
    cell_key = f"{symbol}_{horizon}_{train_days}" if symbol and horizon else None
    now_iso = _utc_now_iso()
    new_kelly = _KELLY_BY_TIER[to_tier]

    # Retired models get paper_active=0 as well
    extra_sets = ""
    if to_tier == "retired":
        extra_sets = ", paper_active = 0"

    conn.execute(
        f"UPDATE model_registry SET tier = ?, kelly_multiplier = ?, "
        f"tier_assigned_at = ?, tier_assigned_by = ?{extra_sets} "
        f"WHERE name = ?",
        (to_tier, new_kelly, now_iso, f"manual:{user}", model_name),
    )

    reason_json = json.dumps({"reason": reason, "user": user})
    conn.execute(
        "INSERT INTO governance_actions "
        "(ts, model_name, action, from_tier, to_tier, triggered_by, reason_json, cell_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            now_iso, model_name,
            "promote" if _tier_rank(to_tier) > _tier_rank(from_tier) else "demote",
            from_tier, to_tier,
            f"manual:{user}",
            reason_json,
            cell_key,
        ),
    )
    conn.commit()

    print(f"tier: {from_tier} → {to_tier}")
    print(f"kelly_multiplier: {row['kelly_multiplier']:.3f} → {new_kelly:.3f}")
    print(f"tier_assigned_by: manual:{user}")
    print(f"ts: {now_iso}")
    if to_tier == "retired":
        print("paper_active: 1 → 0  (model unloaded on next fleet reload)")

    return {
        "model_name": model_name,
        "from_tier": from_tier,
        "to_tier": to_tier,
        "kelly_before": float(row["kelly_multiplier"]) if row["kelly_multiplier"] is not None else 0.0,
        "kelly_after": new_kelly,
        "changed": True,
    }


def _tier_rank(tier: str) -> int:
    return {"retired": -1, "watch": 0, "silver": 1, "gold": 2}.get(tier, 0)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Manually promote or demote a model's governance tier"
    )
    p.add_argument("--model", required=True, help="Model name in model_registry")
    p.add_argument(
        "--to-tier",
        required=True,
        choices=sorted(_VALID_TIERS),
        help="Target tier",
    )
    p.add_argument("--reason", required=True, help="Human-readable reason for this change")
    p.add_argument("--db", default="/data/v3.db", help="SQLite database path")
    p.add_argument("--user", default="manual", help="Operator label (e.g. opus, admin)")
    args = p.parse_args()

    try:
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
    except Exception as e:
        print(f"ERROR: could not open database '{args.db}': {e}", file=sys.stderr)
        sys.exit(1)

    result = promote_model(
        conn=conn,
        model_name=args.model,
        to_tier=args.to_tier,
        reason=args.reason,
        user=args.user,
    )
    sys.exit(0 if result.get("changed") or not result.get("changed") else 1)


if __name__ == "__main__":
    main()
