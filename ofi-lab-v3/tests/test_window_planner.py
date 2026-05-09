import pytest

from storage.window_planner import plan_resolution_rows, ResolutionRow


def test_h300_baseline_emits_native_900_plus_three_evaluations():
    rows = plan_resolution_rows(
        boundary_ms=1_700_000_000_000,
        training_horizon_seconds=900,
        evaluation_windows=[300, 900, 1800, 3600],
    )
    # 1 native + 3 evaluation (900 excluded from evaluation set)
    assert len(rows) == 4
    native = [r for r in rows if r.resolution_type == "native"]
    evals = [r for r in rows if r.resolution_type == "evaluation"]
    assert len(native) == 1
    assert native[0].market_window_seconds == 900
    assert native[0].ts_resolve_at_ms == 1_700_000_000_000 + 900_000
    eval_windows = sorted(r.market_window_seconds for r in evals)
    assert eval_windows == [300, 1800, 3600]


def test_h60_emits_native_60_plus_four_evaluations():
    rows = plan_resolution_rows(
        boundary_ms=1_700_000_000_000,
        training_horizon_seconds=60,
        evaluation_windows=[300, 900, 1800, 3600],
    )
    assert len(rows) == 5
    native = next(r for r in rows if r.resolution_type == "native")
    assert native.market_window_seconds == 60
    assert native.ts_resolve_at_ms == 1_700_000_000_000 + 60_000


def test_native_horizon_already_in_evaluation_set_is_not_duplicated():
    rows = plan_resolution_rows(
        boundary_ms=1_700_000_000_000,
        training_horizon_seconds=300,
        evaluation_windows=[300, 900],
    )
    # Native at 300, evaluation at 900 only.
    assert len(rows) == 2
    assert sum(1 for r in rows if r.market_window_seconds == 300) == 1


def test_resolve_at_ms_is_per_window():
    rows = plan_resolution_rows(
        boundary_ms=2_000_000,
        training_horizon_seconds=900,
        evaluation_windows=[300, 1800],
    )
    by_w = {r.market_window_seconds: r.ts_resolve_at_ms for r in rows}
    assert by_w[900] == 2_000_000 + 900_000
    assert by_w[300] == 2_000_000 + 300_000
    assert by_w[1800] == 2_000_000 + 1_800_000


def test_invalid_horizon_rejected():
    with pytest.raises(ValueError):
        plan_resolution_rows(
            boundary_ms=0,
            training_horizon_seconds=0,
            evaluation_windows=[900],
        )
