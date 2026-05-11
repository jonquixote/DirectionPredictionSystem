import pytest

from filters.pipeline import FilterPipeline, FilterDecision, FilterStage


def make_stage(name, decision):
    """Stage factory: returns FilterStage that records its name and
    yields the given decision."""
    def stage_fn(ctx):
        ctx["trace"].append(name)
        return decision
    return FilterStage(name=name, fn=stage_fn)


def test_pipeline_runs_stages_in_order_until_first_block():
    p = FilterPipeline([
        make_stage("calibration", FilterDecision.pass_()),
        make_stage("paper_filter", FilterDecision.block(reason="below_confidence")),
        make_stage("live_eligibility", FilterDecision.pass_()),
    ])
    ctx = {"trace": []}
    result = p.run(ctx)
    assert ctx["trace"] == ["calibration", "paper_filter"]
    assert result.passed is False
    assert result.blocked_at == "paper_filter"
    assert result.reason == "below_confidence"


def test_pipeline_passes_through_when_all_stages_pass():
    p = FilterPipeline([
        make_stage("a", FilterDecision.pass_()),
        make_stage("b", FilterDecision.pass_()),
        make_stage("c", FilterDecision.pass_()),
    ])
    ctx = {"trace": []}
    result = p.run(ctx)
    assert ctx["trace"] == ["a", "b", "c"]
    assert result.passed is True
    assert result.blocked_at is None


def test_pipeline_collects_filter_evals_for_decision_trace():
    p = FilterPipeline([
        FilterStage(
            name="ev_threshold",
            fn=lambda ctx: FilterDecision.eval_(
                threshold=0.0, input_value=0.018, passed=True,
            ),
        ),
        FilterStage(
            name="confidence",
            fn=lambda ctx: FilterDecision.eval_(
                threshold=0.55, input_value=0.51, passed=False,
                reason="below_confidence",
            ),
        ),
    ])
    ctx = {"trace": []}
    result = p.run(ctx)
    assert result.passed is False
    assert len(result.evals) == 2
    assert result.evals[0].name == "ev_threshold"
    assert result.evals[0].input_value == 0.018
    assert result.evals[1].input_value == 0.51
    assert result.evals[1].passed is False


# ── Task 21: PaperFilter tests ────────────────────────────────


def test_paper_filter_blocks_below_confidence():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.51,
        "ev": 0.020,
        "spread_pct": 0.0001,
        "utc_hour": 12,
        "blackout_hours": [],
    }
    decision = stage.fn(ctx)
    assert decision.passed is False
    assert decision.reason == "below_confidence"
    assert decision.input_value == 0.51


def test_paper_filter_blocks_negative_ev_even_with_high_confidence():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.60,
        "ev": -0.005,  # confidence high, but EV negative
        "spread_pct": 0.0001,
        "utc_hour": 12,
        "blackout_hours": [],
    }
    decision = stage.fn(ctx)
    assert decision.passed is False
    assert decision.reason == "negative_ev"


def test_paper_filter_blocks_blackout_hour():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(
        confidence_threshold=0.55, ev_threshold=0.0,
    )
    ctx = {
        "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
        "utc_hour": 22, "blackout_hours": [21, 22, 23],
    }
    decision = stage.fn(ctx)
    assert decision.passed is False
    assert decision.reason == "blackout"


def test_paper_filter_passes_when_all_gates_clear():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
        "utc_hour": 12, "blackout_hours": [21, 22, 23],
    }
    decision = stage.fn(ctx)
    assert decision.passed is True


# ── Task 22: LiveEligibility + PlatformExecution tests ────────


def test_live_eligibility_blocks_when_lifecycle_not_live_active():
    from filters.live_eligibility import build_live_eligibility_stage
    stage = build_live_eligibility_stage()
    ctx = {"lifecycle_state": "live_suspended"}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "lifecycle_state_not_live_active"


def test_live_eligibility_passes_for_live_active():
    from filters.live_eligibility import build_live_eligibility_stage
    stage = build_live_eligibility_stage()
    ctx = {"lifecycle_state": "live_active", "kalshi_live_enabled": True}
    d = stage.fn(ctx)
    assert d.passed is True


def test_live_eligibility_blocks_when_kalshi_flag_disabled():
    from filters.live_eligibility import build_live_eligibility_stage
    stage = build_live_eligibility_stage()
    ctx = {"lifecycle_state": "live_active", "kalshi_live_enabled": False}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "kalshi_live_disabled"


def test_platform_execution_blocks_on_empty_book():
    from filters.platform_execution import build_kalshi_execution_stage
    stage = build_kalshi_execution_stage()
    ctx = {"kalshi_book_has_quotes": False, "kalshi_market_exists": True,
           "extreme_price": False}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "empty_book"


def test_platform_execution_blocks_on_missing_market():
    from filters.platform_execution import build_kalshi_execution_stage
    stage = build_kalshi_execution_stage()
    ctx = {"kalshi_book_has_quotes": True, "kalshi_market_exists": False,
           "extreme_price": False}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "no_market"


def test_platform_execution_passes_with_healthy_book():
    from filters.platform_execution import build_kalshi_execution_stage
    stage = build_kalshi_execution_stage()
    ctx = {"kalshi_book_has_quotes": True, "kalshi_market_exists": True,
           "extreme_price": False}
    d = stage.fn(ctx)
    assert d.passed is True


# ── Task 23: Model conflict suppression ───────────────────────


def test_paper_filter_blocks_on_model_conflict():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
        "utc_hour": 12, "blackout_hours": [],
        "model_conflict": True,
    }
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "model_conflict"


def test_paper_filter_passes_when_no_conflict_specified():
    from filters.paper_filter import build_paper_filter_stage
    stage = build_paper_filter_stage(confidence_threshold=0.55, ev_threshold=0.0)
    ctx = {
        "calibrated_p": 0.60, "ev": 0.020, "spread_pct": 0.0001,
        "utc_hour": 12, "blackout_hours": [],
        # model_conflict absent → treat as no conflict
    }
    d = stage.fn(ctx)
    assert d.passed is True


# ── Task 24: Staleness filters ────────────────────────────────


def test_stale_price_blocks_when_book_age_exceeds_max():
    from filters.staleness import build_stale_price_stage
    stage = build_stale_price_stage(max_age_seconds=30)
    ctx = {"book_age_seconds": 45.0}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "stale_price"
    assert d.input_value == 45.0


def test_stale_price_passes_when_book_fresh():
    from filters.staleness import build_stale_price_stage
    stage = build_stale_price_stage(max_age_seconds=30)
    ctx = {"book_age_seconds": 5.0}
    d = stage.fn(ctx)
    assert d.passed is True


def test_empty_book_blocks_when_quotes_missing():
    from filters.staleness import build_stale_book_stage
    stage = build_stale_book_stage()
    ctx = {"book_has_quotes": False}
    d = stage.fn(ctx)
    assert d.passed is False
    assert d.reason == "empty_book"


def test_empty_book_passes_when_quotes_present():
    from filters.staleness import build_stale_book_stage
    stage = build_stale_book_stage()
    ctx = {"book_has_quotes": True}
    d = stage.fn(ctx)
    assert d.passed is True
