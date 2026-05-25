"""Gold Miner Report v1 — Phase 1 of adaptive governance plan.

Mines all (model × symbol × market_window) cells via the dashboard analysis API
and produces tier verdicts: gold / silver / watch / blocked.

Usage:
    python -m scripts.gold_miner_report \
        --api-url http://localhost:8081 \
        --auth-user admin \
        --auth-pass "$DASHBOARD_PASS" \
        --out-dir docs_artifacts \
        --since-hours 48

Credentials fall back to env vars DASHBOARD_USER / DASHBOARD_PASS if args
are not provided.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

try:
    import httpx
except ImportError:
    print("ERROR: httpx is required. Install with: pip install httpx", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# BH-FDR — use statsmodels if available, otherwise manual inline
# ---------------------------------------------------------------------------

def bh_fdr(pvals: list[float], alpha: float = 0.05) -> list[float]:
    """Benjamini-Hochberg FDR correction. Returns adjusted p-values."""
    try:
        from statsmodels.stats.multitest import multipletests
        if not pvals:
            return []
        _, p_adj, _, _ = multipletests(pvals, alpha=alpha, method="fdr_bh")
        return [float(x) for x in p_adj]
    except ImportError:
        pass
    # Manual BH
    n = len(pvals)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: pvals[i])
    adj = [0.0] * n
    cummin = 1.0
    for rank, i in enumerate(reversed(order), start=1):
        idx_in_sorted = n - rank
        raw = pvals[i] * n / (idx_in_sorted + 1)
        cummin = min(cummin, raw)
        adj[i] = min(cummin, 1.0)
    return adj


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval."""
    if n == 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------

def make_client(api_url: str, user: str, pwd: str, timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(
        base_url=api_url.rstrip("/"),
        auth=(user, pwd),
        timeout=timeout,
        follow_redirects=True,
    )


def get(client: httpx.Client, path: str, params: dict | None = None) -> dict | list | None:
    """GET JSON; return parsed body or None on error."""
    try:
        r = client.get(path, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"_error": str(exc)}


def post(client: httpx.Client, path: str, body: dict) -> dict | None:
    """POST JSON body; return parsed body or None on error."""
    try:
        r = client.post(path, json=body)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        return {"_error": str(exc)}


# ---------------------------------------------------------------------------
# Envelope unwrapping helpers
# ---------------------------------------------------------------------------

def unwrap(resp: Any) -> Any:
    """Unwrap {status, result, ...} envelope or pass-through lists."""
    if resp is None:
        return None
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        if "_error" in resp:
            return resp
        if "result" in resp:
            return resp["result"]
        return resp
    return resp


def is_error(data: Any) -> bool:
    return isinstance(data, dict) and "_error" in data


def resp_status(resp: Any) -> str:
    if isinstance(resp, dict):
        return resp.get("status", "ok")
    return "ok"


# ---------------------------------------------------------------------------
# Per-cell data gathering
# ---------------------------------------------------------------------------

def fetch_leaderboard(client, symbol, window, min_samples, since_ms):
    params = {
        "symbol": symbol,
        "window": window,
        "min_samples": min_samples,
        "metric": "ev_per_trade",
        "limit": 20,
    }
    if since_ms:
        params["since_ms"] = since_ms
    return get(client, "/api/analysis/leaderboard", params)


def fetch_threshold_grid(client, symbol, window, min_samples, since_ms):
    params = {
        "symbol": symbol,
        "window": window,
        "min_samples": min_samples,
    }
    if since_ms:
        params["since_ms"] = since_ms
    return get(client, "/api/analysis/threshold-grid", params)


def fetch_regime_matrix(client, symbol, window, since_ms):
    params = {"symbol": symbol, "window": window, "min_cell_n": 30}
    if since_ms:
        params["since_ms"] = since_ms
    return get(client, "/api/analysis/regime-matrix", params)


def fetch_grid_search(client, symbol, window, min_n_passed, since_ms):
    params = {
        "symbol": symbol,
        "window": window,
        "top_k": 20,
        "min_n_passed": min_n_passed,
        "apply_fdr": "true",
    }
    if since_ms:
        params["since_ms"] = since_ms
    return get(client, "/api/analysis/grid-search", params)


def fetch_decay_filter(client, symbol, window, since_ms):
    params = {"symbol": symbol, "window": window, "min_bucket_n": 30}
    if since_ms:
        params["since_ms"] = since_ms
    return get(client, "/api/analysis/decay-filter", params)


def fetch_committee_sim(client, symbol, window, since_ms):
    params = {"symbol": symbol, "window": window, "strategy": "avg"}
    if since_ms:
        params["since_ms"] = since_ms
    return get(client, "/api/analysis/committee-sim", params)


def post_walk_forward(client, filter_config, symbol, window, since_ms):
    body = {
        "filter_config": filter_config,
        "symbol": symbol,
        "window": window,
        "n_folds": 5,
    }
    if since_ms:
        body["since_ms"] = since_ms
    return post(client, "/api/analysis/walk-forward", body)


def post_train_test(client, filter_config, symbol, window, since_ms):
    body = {
        "filter_config": filter_config,
        "symbol": symbol,
        "window": window,
        "train_frac": 0.7,
    }
    if since_ms:
        body["since_ms"] = since_ms
    return post(client, "/api/analysis/train-test", body)


# ---------------------------------------------------------------------------
# Decay slope helper
# ---------------------------------------------------------------------------

def extract_decay_slope_negative(decay_resp: Any, model_name: str, symbol: str, window: int) -> bool | None:
    """True if recommended_min_recency_weighted_ev suggests decay (None means data absent)."""
    result = unwrap(decay_resp)
    if is_error(result) or result is None:
        return None
    models = result.get("models", []) if isinstance(result, dict) else []
    for m in models:
        if m.get("model_name") == model_name and m.get("window") == window:
            # Check if highest-decay bucket has negative EV indicators
            buckets = m.get("buckets", [])
            # If recommended threshold is None and we have buckets, check top bucket
            rec = m.get("recommended_min_recency_weighted_ev")
            if rec is None and buckets:
                # No bucket meets wr >= 0.52 — treat as negative slope flag
                positive_wr_buckets = [b for b in buckets if b.get("win_rate") is not None and b["win_rate"] >= 0.52]
                return len(positive_wr_buckets) == 0
            return False
    return None


# ---------------------------------------------------------------------------
# Tier assignment logic
# ---------------------------------------------------------------------------

def assign_tier(
    n_passed: int,
    wilson_ci_lower: float | None,
    recency_weighted_ev: float | None,
    wf_ev_pos_folds: int | None,
    wf_n_folds: int | None,
    train_test_gap_pct: float | None,
    cross_cell_adj_p: float | None,
    raw_p: float | None,
    decay_slope_negative: bool | None,
    walk_forward_skipped: bool,
    errors: list[str],
) -> tuple[str, str]:
    """Returns (tier, reason)."""

    # Blocked: insufficient data
    if n_passed < 30:
        return "blocked", f"n_passed={n_passed} < 30"
    if wilson_ci_lower is None:
        return "blocked", "wilson_ci_lower is null"

    # Check gold criteria
    gold_criteria_failed = []
    if n_passed < 50:
        gold_criteria_failed.append(f"n_passed={n_passed}<50")
    if wilson_ci_lower is None or wilson_ci_lower <= 0.515:
        gold_criteria_failed.append(f"wilson_ci_lower={wilson_ci_lower:.4f}<=0.515")
    if recency_weighted_ev is None or recency_weighted_ev <= 0:
        gold_criteria_failed.append(f"rwev={recency_weighted_ev} not>0")
    if not walk_forward_skipped:
        if wf_ev_pos_folds is None or wf_n_folds is None or wf_n_folds == 0:
            gold_criteria_failed.append("walk_forward_no_data")
        elif wf_ev_pos_folds < (2 / 3) * wf_n_folds:
            gold_criteria_failed.append(
                f"wf_ev_pos_folds={wf_ev_pos_folds}/{wf_n_folds}<2/3"
            )
    else:
        gold_criteria_failed.append("walk_forward_skipped:insufficient_data")

    if train_test_gap_pct is not None and abs(train_test_gap_pct) > 4.0:
        gold_criteria_failed.append(f"tt_gap_pct={train_test_gap_pct:.2f}>4pp")

    if cross_cell_adj_p is None or cross_cell_adj_p >= 0.05:
        gold_criteria_failed.append(
            f"cross_cell_adj_p={cross_cell_adj_p} not<0.05"
        )

    if not gold_criteria_failed:
        return "gold", "all gold criteria met"

    # Silver: Wilson CI lower > 0.50 AND either walk_forward thin or tt_gap ≤ 6pp
    if wilson_ci_lower is not None and wilson_ci_lower > 0.50:
        silver_reasons = []
        if walk_forward_skipped:
            silver_reasons.append("walk_forward_skipped:insufficient_data")
        if train_test_gap_pct is not None and abs(train_test_gap_pct) <= 6.0:
            silver_reasons.append(f"tt_gap_pct={train_test_gap_pct:.2f}<=6pp")
        elif train_test_gap_pct is None:
            silver_reasons.append("tt_gap_pct=null(thin)")
        failed_non_ci = [c for c in gold_criteria_failed
                        if "wilson_ci_lower" not in c and "walk_forward_skipped" not in c
                        and "cross_cell_adj_p" not in c]
        # Silver if Wilson is OK and failures are limited to walk_forward/tt or fdr only
        soft_failures_only = all(
            any(kw in c for kw in ["walk_forward", "tt_gap", "cross_cell_adj_p", "rwev", "wf_ev"])
            for c in gold_criteria_failed
        )
        if soft_failures_only:
            reason = "wilson_ci_lower>{:.3f}>0.50; {}".format(
                wilson_ci_lower,
                "; ".join(silver_reasons) if silver_reasons else "minor gaps"
            )
            return "silver", reason

    # Watch: statistically significant raw p but fails ≥ 2 other criteria
    if raw_p is not None and raw_p < 0.05 and len(gold_criteria_failed) >= 2:
        return "watch", f"raw_p={raw_p:.4f}<0.05 but {len(gold_criteria_failed)} gold criteria failed: {'; '.join(gold_criteria_failed[:3])}"

    # Default: blocked/insufficient
    return "blocked", f"no significant edge or insufficient data; {'; '.join(gold_criteria_failed[:3])}"


# ---------------------------------------------------------------------------
# Main report builder
# ---------------------------------------------------------------------------

def run_report(
    api_url: str,
    auth_user: str,
    auth_pass: str,
    out_dir: str,
    since_hours: float,
    symbols: list[str],
    windows: list[int],
    min_samples: int,
) -> None:
    start_time = time.time()
    now_utc = datetime.now(timezone.utc)
    since_ms = int((now_utc.timestamp() - since_hours * 3600) * 1000) if since_hours > 0 else None

    os.makedirs(out_dir, exist_ok=True)
    stamp = now_utc.strftime("%Y%m%d_%H%M")

    print(f"[gold_miner] Starting run at {now_utc.isoformat()}")
    print(f"[gold_miner] API: {api_url}  since_hours={since_hours}  symbols={symbols}  windows={windows}")

    client = make_client(api_url, auth_user, auth_pass)

    # ------------------------------------------------------------------
    # Per-(symbol, window) cell data collection
    # ------------------------------------------------------------------
    # cell_data: list of per-(symbol,window) context dicts
    cell_data: list[dict] = []
    # model_rows: flat list — one entry per (model, symbol, window)
    all_model_rows: list[dict] = []
    # For cross-cell BH-FDR
    all_raw_pvals: list[float] = []
    raw_pval_idx: list[int] = []  # index into all_model_rows

    for symbol in symbols:
        for window in windows:
            print(f"[gold_miner]  cell {symbol}/{window}s ...")

            cell = {
                "symbol": symbol,
                "window": window,
                "leaderboard": None,
                "threshold_grid": None,
                "regime_matrix": None,
                "grid_search": None,
                "decay_filter": None,
                "committee_sim": None,
                "errors": [],
            }

            # 1. Leaderboard
            lb_resp = fetch_leaderboard(client, symbol, window, min_samples, since_ms)
            if is_error(lb_resp):
                cell["errors"].append(f"leaderboard: {lb_resp['_error']}")
            else:
                cell["leaderboard"] = lb_resp  # already a list

            # 2. Threshold grid
            tg_resp = fetch_threshold_grid(client, symbol, window, min_samples, since_ms)
            if is_error(tg_resp):
                cell["errors"].append(f"threshold_grid: {tg_resp['_error']}")
            else:
                cell["threshold_grid"] = tg_resp

            # 3. Regime matrix
            rm_resp = fetch_regime_matrix(client, symbol, window, since_ms)
            if is_error(rm_resp):
                cell["errors"].append(f"regime_matrix: {rm_resp['_error']}")
            else:
                cell["regime_matrix"] = rm_resp

            # 4. Grid search (once per cell)
            gs_resp = fetch_grid_search(client, symbol, window, min_samples, since_ms)
            if is_error(gs_resp):
                cell["errors"].append(f"grid_search: {gs_resp['_error']}")
            else:
                cell["grid_search"] = gs_resp

            # 5. Decay filter
            df_resp = fetch_decay_filter(client, symbol, window, since_ms)
            if is_error(df_resp):
                cell["errors"].append(f"decay_filter: {df_resp['_error']}")
            else:
                cell["decay_filter"] = df_resp

            # 6. Committee sim
            cs_resp = fetch_committee_sim(client, symbol, window, since_ms)
            if is_error(cs_resp):
                cell["errors"].append(f"committee_sim: {cs_resp['_error']}")
            else:
                cell["committee_sim"] = cs_resp

            cell_data.append(cell)

            # ------------------------------------------------------------------
            # Extract per-model rows from leaderboard
            # ------------------------------------------------------------------
            lb_list = cell["leaderboard"]
            if not lb_list or not isinstance(lb_list, list):
                print(f"[gold_miner]    no leaderboard rows for {symbol}/{window}s")
                continue

            # Extract best filter_config from grid search
            gs_result = unwrap(cell["grid_search"])
            best_filter_config: dict = {}
            if gs_result and not is_error(gs_result):
                top_list = gs_result.get("top", []) if isinstance(gs_result, dict) else []
                if top_list:
                    best_filter_config = top_list[0].get("filter_config", {})

            # Build per-model threshold lookup from threshold_grid
            tg_result = cell["threshold_grid"]
            threshold_by_model: dict[str, dict] = {}
            if isinstance(tg_result, dict):
                for m in tg_result.get("models", []):
                    threshold_by_model[m["model_name"]] = m.get("best_threshold", {}) or {}

            for lb_row in lb_list:
                model_name = lb_row.get("model_name", "")
                if not model_name:
                    continue

                n_passed = lb_row.get("n_samples", 0)
                win_rate = lb_row.get("win_rate")
                ci_lo = lb_row.get("win_rate_ci_lo")
                ev_per_trade = lb_row.get("ev_per_trade")
                raw_p = lb_row.get("p_value_vs_50pct")

                # recency_weighted_ev: not directly in leaderboard — use ev_per_trade as proxy
                # (the leaderboard doesn't expose decay table's rwev directly)
                rwev = ev_per_trade  # best available proxy from leaderboard

                # Decay slope
                decay_slope_negative = extract_decay_slope_negative(
                    cell["decay_filter"], model_name, symbol, window
                )

                # Best threshold from threshold grid
                best_threshold = threshold_by_model.get(model_name, {})
                best_confidence_threshold = (
                    best_threshold.get("threshold") if best_threshold else None
                )

                # Regime favorites from regime matrix
                rm_result = unwrap(cell["regime_matrix"])
                regime_summary: str = ""
                if rm_result and not is_error(rm_result) and isinstance(rm_result, dict):
                    for rm_model in rm_result.get("models", []):
                        if rm_model.get("model_name") == model_name:
                            cells_above = [
                                c for c in rm_model.get("cells", [])
                                if c.get("win_rate") is not None and c["win_rate"] > 0.55
                            ]
                            if cells_above:
                                regime_summary = "; ".join(
                                    "{}={:.2f}".format(
                                        c.get("regime_label", "?"), c["win_rate"]
                                    )
                                    for c in cells_above[:3]
                                )
                            break

                row_idx = len(all_model_rows)
                row = {
                    "model_name": model_name,
                    "symbol": symbol,
                    "window": window,
                    "n_passed": n_passed,
                    "win_rate": win_rate,
                    "wilson_ci_lower": ci_lo,
                    "wilson_ci_upper": lb_row.get("win_rate_ci_hi"),
                    "recency_weighted_ev": rwev,
                    "ev_per_trade": ev_per_trade,
                    "roi_pct": lb_row.get("roi_pct"),
                    "sharpe": lb_row.get("sharpe"),
                    "raw_p": raw_p,
                    "cross_cell_adj_p": None,  # filled after BH pass
                    "best_confidence_threshold": best_confidence_threshold,
                    "decay_slope_negative": decay_slope_negative,
                    "best_filter_config": best_filter_config,
                    "regime_summary": regime_summary,
                    # Walk-forward and train-test: populated after POST calls
                    "wf_ev_pos_folds": None,
                    "wf_n_folds": None,
                    "wf_mean_roi_pct": None,
                    "wf_robust": None,
                    "walk_forward_skipped": False,
                    "walk_forward_skip_reason": None,
                    "train_win_rate": None,
                    "test_win_rate": None,
                    "train_test_gap_pct": None,
                    "tier": "blocked",
                    "tier_reason": "pending",
                    "kelly_multiplier": 0.0,
                    "cell_errors": list(cell["errors"]),
                }

                # Collect p-value for cross-cell BH-FDR
                if raw_p is not None:
                    all_raw_pvals.append(float(raw_p))
                    raw_pval_idx.append(row_idx)

                all_model_rows.append(row)

    print(f"[gold_miner] Collected {len(all_model_rows)} model-cell rows. Running walk-forward/train-test (once per cell)...")

    # ------------------------------------------------------------------
    # Walk-forward + train-test ONCE per (symbol, window) cell
    # (grid_search best_filter_config is already per-cell, not per-model)
    # ------------------------------------------------------------------
    # Build per-cell WF/TT results
    cell_wf_results: dict[tuple, dict] = {}  # (symbol, window) -> parsed result
    cell_tt_results: dict[tuple, dict] = {}

    # Collect unique cells and their best filter configs
    cell_filter_configs: dict[tuple, dict] = {}
    for row in all_model_rows:
        ck = (row["symbol"], row["window"])
        if ck not in cell_filter_configs:
            cell_filter_configs[ck] = row["best_filter_config"] or {}

    for (symbol, window), fc in cell_filter_configs.items():
        print(f"[gold_miner]   walk-forward + train-test for {symbol}/{window}s ...")

        wf_resp = post_walk_forward(client, fc, symbol, window, since_ms)
        if is_error(wf_resp):
            cell_wf_results[(symbol, window)] = {
                "skipped": True,
                "skip_reason": f"error: {wf_resp['_error']}",
            }
        else:
            wf_status = resp_status(wf_resp)
            if wf_status in ("insufficient_samples", "no_data"):
                msg = wf_resp.get("message", "") if isinstance(wf_resp, dict) else ""
                cell_wf_results[(symbol, window)] = {
                    "skipped": True,
                    "skip_reason": f"insufficient_data: {msg}",
                }
            elif wf_status == "ok" or "result" in (wf_resp or {}):
                wf_result = unwrap(wf_resp)
                if wf_result and isinstance(wf_result, dict):
                    folds = wf_result.get("folds", [])
                    n_folds = len(folds)
                    ev_pos = sum(
                        1 for f in folds
                        if f.get("roi_pct") is not None and f["roi_pct"] > 0
                    )
                    cell_wf_results[(symbol, window)] = {
                        "skipped": False,
                        "skip_reason": None,
                        "wf_ev_pos_folds": ev_pos,
                        "wf_n_folds": n_folds,
                        "wf_mean_roi_pct": wf_result.get("mean_roi_pct"),
                        "wf_robust": wf_result.get("robust"),
                    }
                else:
                    cell_wf_results[(symbol, window)] = {
                        "skipped": True,
                        "skip_reason": "empty_result",
                    }
            else:
                cell_wf_results[(symbol, window)] = {
                    "skipped": True,
                    "skip_reason": f"unexpected_status:{wf_status}",
                }

        # Train-test
        tt_resp = post_train_test(client, fc, symbol, window, since_ms)
        if is_error(tt_resp):
            cell_tt_results[(symbol, window)] = {}
        else:
            tt_status = resp_status(tt_resp)
            if tt_status == "ok" or "result" in (tt_resp or {}):
                tt_result = unwrap(tt_resp)
                if tt_result and isinstance(tt_result, dict):
                    train_m = tt_result.get("train", {}) or {}
                    test_m = tt_result.get("test", {}) or {}
                    delta = tt_result.get("win_rate_delta")
                    cell_tt_results[(symbol, window)] = {
                        "train_win_rate": train_m.get("win_rate"),
                        "test_win_rate": test_m.get("win_rate"),
                        "train_test_gap_pct": round(delta * 100, 3) if delta is not None else None,
                    }
                else:
                    cell_tt_results[(symbol, window)] = {}
            else:
                cell_tt_results[(symbol, window)] = {}

    # Apply cell-level WF/TT results to each model row
    for row in all_model_rows:
        ck = (row["symbol"], row["window"])
        wf = cell_wf_results.get(ck, {"skipped": True, "skip_reason": "cell_not_run"})
        tt = cell_tt_results.get(ck, {})

        row["walk_forward_skipped"] = wf.get("skipped", True)
        row["walk_forward_skip_reason"] = wf.get("skip_reason")
        row["wf_ev_pos_folds"] = wf.get("wf_ev_pos_folds")
        row["wf_n_folds"] = wf.get("wf_n_folds")
        row["wf_mean_roi_pct"] = wf.get("wf_mean_roi_pct")
        row["wf_robust"] = wf.get("wf_robust")
        row["train_win_rate"] = tt.get("train_win_rate")
        row["test_win_rate"] = tt.get("test_win_rate")
        row["train_test_gap_pct"] = tt.get("train_test_gap_pct")

    # ------------------------------------------------------------------
    # Cross-cell BH-FDR
    # ------------------------------------------------------------------
    print(f"[gold_miner] Applying cross-cell BH-FDR to {len(all_raw_pvals)} p-values...")
    if all_raw_pvals:
        adj_pvals = bh_fdr(all_raw_pvals)
        for row_idx, adj_p in zip(raw_pval_idx, adj_pvals):
            all_model_rows[row_idx]["cross_cell_adj_p"] = round(adj_p, 6)

    # ------------------------------------------------------------------
    # Tier assignment
    # ------------------------------------------------------------------
    print("[gold_miner] Assigning tiers...")
    for row in all_model_rows:
        tier, reason = assign_tier(
            n_passed=row["n_passed"] or 0,
            wilson_ci_lower=row["wilson_ci_lower"],
            recency_weighted_ev=row["recency_weighted_ev"],
            wf_ev_pos_folds=row["wf_ev_pos_folds"],
            wf_n_folds=row["wf_n_folds"],
            train_test_gap_pct=row["train_test_gap_pct"],
            cross_cell_adj_p=row["cross_cell_adj_p"],
            raw_p=row["raw_p"],
            decay_slope_negative=row["decay_slope_negative"],
            walk_forward_skipped=row["walk_forward_skipped"],
            errors=row["cell_errors"],
        )
        row["tier"] = tier
        row["tier_reason"] = reason
        kelly_map = {"gold": 1.0, "silver": 0.3, "watch": 0.0, "blocked": 0.0}
        row["kelly_multiplier"] = kelly_map.get(tier, 0.0)

    # ------------------------------------------------------------------
    # Sort: gold first, then silver, watch, blocked; within tier by ev_per_trade desc
    # ------------------------------------------------------------------
    TIER_ORDER = {"gold": 0, "silver": 1, "watch": 2, "blocked": 3}

    def sort_key(r):
        return (
            TIER_ORDER.get(r["tier"], 9),
            -(r["ev_per_trade"] or float("-inf")),
        )

    all_model_rows.sort(key=sort_key)

    # ------------------------------------------------------------------
    # Summary counts
    # ------------------------------------------------------------------
    tier_counts: dict[str, int] = {"gold": 0, "silver": 0, "watch": 0, "blocked": 0}
    for row in all_model_rows:
        tier_counts[row["tier"]] = tier_counts.get(row["tier"], 0) + 1

    print(f"[gold_miner] Tier counts: {tier_counts}")

    elapsed = time.time() - start_time

    # ------------------------------------------------------------------
    # Write JSON
    # ------------------------------------------------------------------
    json_path = os.path.join(out_dir, f"gold_miner_report_{stamp}.json")
    json_payload = {
        "metadata": {
            "run_timestamp_utc": now_utc.isoformat(),
            "since_hours": since_hours,
            "symbols": symbols,
            "windows": windows,
            "total_cells_evaluated": len(all_model_rows),
            "tier_counts": tier_counts,
            "confidence_for_live": "paper_only",
            "elapsed_seconds": round(elapsed, 1),
        },
        "rows": all_model_rows,
    }
    with open(json_path, "w") as f:
        json.dump(json_payload, f, indent=2, default=str)
    print(f"[gold_miner] JSON written: {json_path}")

    # ------------------------------------------------------------------
    # Write CSV
    # ------------------------------------------------------------------
    csv_path = os.path.join(out_dir, f"gold_miner_report_{stamp}.csv")
    csv_fields = [
        "tier", "model_name", "symbol", "window", "n_passed",
        "win_rate", "wilson_ci_lower", "wilson_ci_upper",
        "recency_weighted_ev", "ev_per_trade", "roi_pct", "sharpe",
        "raw_p", "cross_cell_adj_p",
        "best_confidence_threshold",
        "wf_ev_pos_folds", "wf_n_folds", "wf_mean_roi_pct", "wf_robust",
        "walk_forward_skipped", "walk_forward_skip_reason",
        "train_win_rate", "test_win_rate", "train_test_gap_pct",
        "decay_slope_negative",
        "kelly_multiplier",
        "tier_reason",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for row in all_model_rows:
            writer.writerow(row)
    print(f"[gold_miner] CSV written: {csv_path}")

    # ------------------------------------------------------------------
    # Write Markdown
    # ------------------------------------------------------------------
    md_path = os.path.join(out_dir, f"gold_miner_report_{stamp}.md")
    lines = []

    # Metadata block
    lines.append("# Gold Miner Report v1\n")
    lines.append("```")
    lines.append(f"run_timestamp_utc  : {now_utc.isoformat()}")
    lines.append(f"since_hours        : {since_hours}")
    lines.append(f"symbols            : {', '.join(symbols)}")
    lines.append(f"windows_seconds    : {', '.join(str(w) for w in windows)}")
    lines.append(f"total_cells        : {len(all_model_rows)}")
    lines.append(f"gold               : {tier_counts.get('gold', 0)}")
    lines.append(f"silver             : {tier_counts.get('silver', 0)}")
    lines.append(f"watch              : {tier_counts.get('watch', 0)}")
    lines.append(f"blocked            : {tier_counts.get('blocked', 0)}")
    lines.append(f"confidence_for_live: paper_only")
    lines.append(f"elapsed_seconds    : {round(elapsed, 1)}")
    lines.append("```\n")

    # Summary table
    lines.append("## Ranked Summary Table\n")
    col_w = {
        "model_name": 45,
        "symbol": 9,
        "window": 7,
        "tier": 8,
        "win_rate": 9,
        "wilson_ci": 16,
        "rwev": 10,
        "wf_folds": 9,
        "tt_gap": 8,
        "reason": 55,
    }
    header = (
        f"| {'model':<{col_w['model_name']}} "
        f"| {'symbol':<{col_w['symbol']}} "
        f"| {'window':>{col_w['window']}} "
        f"| {'tier':<{col_w['tier']}} "
        f"| {'win_rate':>{col_w['win_rate']}} "
        f"| {'wilson_ci':<{col_w['wilson_ci']}} "
        f"| {'rwev':>{col_w['rwev']}} "
        f"| {'wf_pos/tot':>{col_w['wf_folds']}} "
        f"| {'tt_gap%':>{col_w['tt_gap']}} "
        f"| {'reason':<{col_w['reason']}} |"
    )
    sep = (
        f"| {'-'*col_w['model_name']} "
        f"| {'-'*col_w['symbol']} "
        f"| {'-'*col_w['window']} "
        f"| {'-'*col_w['tier']} "
        f"| {'-'*col_w['win_rate']} "
        f"| {'-'*col_w['wilson_ci']} "
        f"| {'-'*col_w['rwev']} "
        f"| {'-'*col_w['wf_folds']} "
        f"| {'-'*col_w['tt_gap']} "
        f"| {'-'*col_w['reason']} |"
    )
    lines.append(header)
    lines.append(sep)

    for row in all_model_rows:
        wr = f"{row['win_rate']:.4f}" if row["win_rate"] is not None else "N/A"
        ci_lo = row.get("wilson_ci_lower")
        ci_hi = row.get("wilson_ci_upper")
        ci_str = f"[{ci_lo:.4f},{ci_hi:.4f}]" if ci_lo is not None and ci_hi is not None else "N/A"
        rwev_str = f"{row['recency_weighted_ev']:.5f}" if row["recency_weighted_ev"] is not None else "N/A"
        wf_pos = row.get("wf_ev_pos_folds")
        wf_tot = row.get("wf_n_folds")
        wf_str = f"{wf_pos}/{wf_tot}" if wf_pos is not None and wf_tot is not None else ("skip" if row.get("walk_forward_skipped") else "N/A")
        tt = row.get("train_test_gap_pct")
        tt_str = f"{tt:.2f}" if tt is not None else "N/A"
        reason = row.get("tier_reason", "")[:col_w["reason"]]
        lines.append(
            f"| {row['model_name']:<{col_w['model_name']}} "
            f"| {row['symbol']:<{col_w['symbol']}} "
            f"| {row['window']:>{col_w['window']}} "
            f"| {row['tier']:<{col_w['tier']}} "
            f"| {wr:>{col_w['win_rate']}} "
            f"| {ci_str:<{col_w['wilson_ci']}} "
            f"| {rwev_str:>{col_w['rwev']}} "
            f"| {wf_str:>{col_w['wf_folds']}} "
            f"| {tt_str:>{col_w['tt_gap']}} "
            f"| {reason:<{col_w['reason']}} |"
        )

    lines.append("")

    # Per-qualifying-cell sections (gold + silver + watch)
    qualifying = [r for r in all_model_rows if r["tier"] in ("gold", "silver", "watch")]
    lines.append(f"\n## Per-Cell Detail ({len(qualifying)} qualifying cells)\n")

    for row in qualifying:
        lines.append(
            f"### {row['tier'].upper()} — {row['model_name']} | {row['symbol']} / {row['window']}s\n"
        )
        lines.append(f"- **Tier:** {row['tier']}  |  **Kelly multiplier:** {row['kelly_multiplier']}")
        lines.append(f"- **Reason:** {row['tier_reason']}")
        lines.append(f"- **n_passed:** {row['n_passed']}  |  **win_rate:** {row['win_rate']}  |  **wilson_ci:** [{row['wilson_ci_lower']}, {row['wilson_ci_upper']}]")
        lines.append(f"- **ev_per_trade (rwev proxy):** {row['ev_per_trade']}  |  **roi_pct:** {row['roi_pct']}  |  **sharpe:** {row['sharpe']}")
        lines.append(f"- **raw_p:** {row['raw_p']}  |  **cross_cell_adj_p:** {row['cross_cell_adj_p']}")
        lines.append(f"- **best_confidence_threshold:** {row['best_confidence_threshold']}")
        lines.append(f"- **decay_slope_negative:** {row['decay_slope_negative']}")
        if row["walk_forward_skipped"]:
            lines.append(f"- **walk_forward_skipped:** {row['walk_forward_skip_reason']}")
        else:
            lines.append(f"- **walk_forward:** {row['wf_ev_pos_folds']}/{row['wf_n_folds']} EV-positive folds  |  mean_roi: {row['wf_mean_roi_pct']}  |  robust: {row['wf_robust']}")
        lines.append(f"- **train_test:** train_wr={row['train_win_rate']}  test_wr={row['test_win_rate']}  gap={row['train_test_gap_pct']}pp")
        if row.get("regime_summary"):
            lines.append(f"- **regime_favorites:** {row['regime_summary']}")
        lines.append("")
        lines.append("**Recommended filter_config (copy-paste):**")
        lines.append("```json")
        lines.append(json.dumps(row["best_filter_config"], indent=2))
        lines.append("```")
        lines.append("")
        if row["cell_errors"]:
            lines.append("**Errors in this cell:**")
            for e in row["cell_errors"]:
                lines.append(f"- {e}")
        lines.append("")

    md_content = "\n".join(lines)
    with open(md_path, "w") as f:
        f.write(md_content)
    print(f"[gold_miner] Markdown written: {md_path}")
    print(f"[gold_miner] Done in {round(elapsed, 1)}s")

    # Print top 3
    top3 = [r for r in all_model_rows if r["tier"] in ("gold", "silver")][:3]
    if not top3:
        top3 = [r for r in all_model_rows if r["tier"] == "watch"][:3]
    if top3:
        print(f"\n[gold_miner] Top {len(top3)} qualifying models:")
        for r in top3:
            print(f"  [{r['tier'].upper()}] {r['model_name']}  {r['symbol']}/{r['window']}s  win_rate={r['win_rate']}  rwev={r['recency_weighted_ev']}")

    return md_path, json_path, csv_path, tier_counts, elapsed


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Gold Miner Report v1")
    parser.add_argument("--api-url", default="http://localhost:8081",
                        help="Dashboard API base URL (default: http://localhost:8081)")
    parser.add_argument("--auth-user", default=None,
                        help="HTTP Basic auth username (or DASHBOARD_USER env var)")
    parser.add_argument("--auth-pass", default=None,
                        help="HTTP Basic auth password (or DASHBOARD_PASS env var)")
    parser.add_argument("--out-dir", default="docs_artifacts",
                        help="Output directory for artifacts (default: docs_artifacts)")
    parser.add_argument("--since-hours", type=float, default=48.0,
                        help="Only include predictions from the last N hours (default: 48)")
    parser.add_argument("--symbols", nargs="+",
                        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
                        help="Symbol universe")
    parser.add_argument("--windows", nargs="+", type=int,
                        default=[300, 900, 1800],
                        help="Market window seconds")
    parser.add_argument("--min-samples", type=int, default=30,
                        help="Min samples for leaderboard inclusion (default: 30)")
    args = parser.parse_args()

    auth_user = args.auth_user or os.environ.get("DASHBOARD_USER", "admin")
    auth_pass = args.auth_pass or os.environ.get("DASHBOARD_PASS", "")

    if not auth_pass:
        print("WARNING: No auth password provided. Set --auth-pass or DASHBOARD_PASS env var.", file=sys.stderr)

    run_report(
        api_url=args.api_url,
        auth_user=auth_user,
        auth_pass=auth_pass,
        out_dir=args.out_dir,
        since_hours=args.since_hours,
        symbols=args.symbols,
        windows=args.windows,
        min_samples=args.min_samples,
    )


if __name__ == "__main__":
    main()
