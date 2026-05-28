"""Manual tier promotion / demotion CLI for model governance.

Usage:
    python -m scripts.promote_model --model NAME --to-tier TIER --reason "..." [--db /data/v3.db] [--user opus]

TIER must be one of: gold, silver, watch, retired

Phase 57: --market-window 300|900|1800|all selects which window(s) to update in
model_window_tier. Default (no flag) = primary window only.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Optional


_VALID_TIERS = frozenset({"gold", "silver", "watch", "retired"})
_KELLY_BY_TIER: dict[str, float] = {
    "gold": 1.0,
    "silver": 0.3,
    "watch": 0.0,
    "retired": 0.0,
}
_VALID_WINDOWS = (300, 900, 1800)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def promote_model(
    *,
    conn: sqlite3.Connection,
    model_name: str,
    to_tier: str,
    reason: str,
    user: str = "manual",
    market_window: Optional[str] = None,
) -> dict:
    """Promote or demote a model to a new tier.

    Args:
        conn: SQLite connection with row_factory = sqlite3.Row
        model_name: Name of the model in model_registry
        to_tier: Target tier (gold, silver, watch, retired)
        reason: Human-readable reason logged to governance_actions
        user: Triggered-by label (e.g. 'opus', 'admin')
        market_window: None = primary window only; '300'|'900'|'1800' = that window;
                       'all' = all 3 windows. Phase 57.

    Returns:
        dict with before/after state

    Raises:
        SystemExit(1) on validation failure.
    """
    if to_tier not in _VALID_TIERS:
        print(f"ERROR: invalid tier '{to_tier}'. Must be one of {sorted(_VALID_TIERS)}", file=sys.stderr)
        sys.exit(1)

    row = conn.execute(
        "SELECT name, tier, kelly_multiplier, symbol, training_horizon_seconds, train_days, "
        "COALESCE(primary_market_window_seconds, training_horizon_seconds) AS primary_window "
        "FROM model_registry WHERE name = ?",
        (model_name,),
    ).fetchone()
    if not row:
        print(f"ERROR: model '{model_name}' not found in model_registry", file=sys.stderr)
        sys.exit(1)

    from_tier = row["tier"] or "watch"
    symbol = row["symbol"]
    horizon = row["training_horizon_seconds"]
    train_days = row["train_days"]
    primary_window_int = int(row["primary_window"]) if row["primary_window"] else 900
    cell_key = f"{symbol}_{horizon}_{train_days}" if symbol and horizon else None
    now_iso = _utc_now_iso()
    new_kelly = _KELLY_BY_TIER[to_tier]

    # Determine which windows to update
    if market_window == "all":
        target_windows = list(_VALID_WINDOWS)
    elif market_window is not None:
        try:
            target_windows = [int(market_window)]
        except ValueError:
            print(f"ERROR: invalid --market-window '{market_window}'. Use 300, 900, 1800, or all.", file=sys.stderr)
            sys.exit(1)
    else:
        # Default: primary window only
        target_windows = [primary_window_int]

    # Phase 57: UPDATE model_window_tier for the selected windows
    updated_windows = []
    for w in target_windows:
        existing_mwt = conn.execute(
            "SELECT tier FROM model_window_tier WHERE model_name=? AND market_window_seconds=?",
            (model_name, w),
        ).fetchone()
        if existing_mwt is None:
            # Insert row if missing
            conn.execute(
                "INSERT OR IGNORE INTO model_window_tier "
                "(model_name, market_window_seconds, tier, kelly_multiplier, tier_assigned_at, tier_assigned_by) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (model_name, w, to_tier, new_kelly, now_iso, f"manual:{user}"),
            )
        else:
            conn.execute(
                "UPDATE model_window_tier SET tier=?, kelly_multiplier=?, "
                "tier_assigned_at=?, tier_assigned_by=? "
                "WHERE model_name=? AND market_window_seconds=?",
                (to_tier, new_kelly, now_iso, f"manual:{user}", model_name, w),
            )
        updated_windows.append(w)

    # Also update model_registry.tier_assigned_at as breadcrumb
    # Keep model_registry.tier/kelly for backward compat (still readable by old code)
    extra_sets = ""
    if to_tier == "retired":
        extra_sets = ", paper_active = 0"

    conn.execute(
        f"UPDATE model_registry SET tier = ?, kelly_multiplier = ?, "
        f"tier_assigned_at = ?, tier_assigned_by = ?{extra_sets} "
        f"WHERE name = ?",
        (to_tier, new_kelly, now_iso, f"manual:{user}", model_name),
    )

    reason_json = json.dumps({"reason": reason, "user": user, "windows": updated_windows})
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

    if from_tier == to_tier:
        print(f"NOOP: model '{model_name}' is already tier='{to_tier}' (windows={updated_windows} updated anyway)")
        return {
            "model_name": model_name,
            "from_tier": from_tier,
            "to_tier": to_tier,
            "kelly_before": row["kelly_multiplier"],
            "kelly_after": new_kelly,
            "changed": False,
            "windows_updated": updated_windows,
        }

    print(f"tier: {from_tier} → {to_tier}")
    print(f"kelly_multiplier: {row['kelly_multiplier'] or 0.0:.3f} → {new_kelly:.3f}")
    print(f"tier_assigned_by: manual:{user}")
    print(f"ts: {now_iso}")
    print(f"windows_updated: {updated_windows}")
    if to_tier == "retired":
        print("paper_active: 1 → 0  (model unloaded on next fleet reload)")

    return {
        "model_name": model_name,
        "from_tier": from_tier,
        "to_tier": to_tier,
        "kelly_before": float(row["kelly_multiplier"]) if row["kelly_multiplier"] is not None else 0.0,
        "kelly_after": new_kelly,
        "changed": True,
        "windows_updated": updated_windows,
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
    p.add_argument(
        "--market-window",
        default=None,
        help="Window to update: 300, 900, 1800, or 'all'. Default: primary window only.",
    )
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
        market_window=args.market_window,
    )
    sys.exit(0 if result.get("changed") or not result.get("changed") else 1)


if __name__ == "__main__":
    main()
