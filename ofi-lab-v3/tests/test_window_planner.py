import pytest

from storage.window_planner import plan_resolution_rows, ResolutionRow

_BASE = 1_735_689_600_000


def test_all_rows_are_evaluation():
    rows = plan_resolution_rows(
        boundary_ms=_BASE,
        training_horizon_seconds=900,
        evaluation_windows=[300, 900, 1800],
    )
    assert all(r.resolution_type == "evaluation" for r in rows)


def test_alignment_gate_at_five_min_boundary():
    rows = plan_resolution_rows(
        boundary_ms=_BASE,
        training_horizon_seconds=900,
        evaluation_windows=[300, 900, 1800],
    )
    windows = sorted(r.market_window_seconds for r in rows)
    assert windows == [300, 900, 1800]


def test_900_and_1800_excluded_at_non_aligned_boundary():
    boundary = _BASE + 300_000
    rows = plan_resolution_rows(
        boundary_ms=boundary,
        training_horizon_seconds=900,
        evaluation_windows=[300, 900, 1800],
    )
    windows = sorted(r.market_window_seconds for r in rows)
    assert windows == [300]


def test_1800_excluded_at_15_min_boundary():
    boundary = _BASE + 900_000
    rows = plan_resolution_rows(
        boundary_ms=boundary,
        training_horizon_seconds=900,
        evaluation_windows=[300, 900, 1800],
    )
    windows = sorted(r.market_window_seconds for r in rows)
    assert windows == [300, 900]


def test_resolve_at_ms_is_per_window():
    rows = plan_resolution_rows(
        boundary_ms=_BASE,
        training_horizon_seconds=900,
        evaluation_windows=[300, 1800],
    )
    by_w = {r.market_window_seconds: r.ts_resolve_at_ms for r in rows}
    assert by_w[300] == _BASE + 300_000
    assert by_w[1800] == _BASE + 1_800_000


def test_invalid_horizon_rejected():
    with pytest.raises(ValueError):
        plan_resolution_rows(
            boundary_ms=0,
            training_horizon_seconds=0,
            evaluation_windows=[900],
        )
