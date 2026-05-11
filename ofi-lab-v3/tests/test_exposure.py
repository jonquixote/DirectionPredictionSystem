import pytest

from execution.exposure import ExposureCap, ExposureBlock


def test_initial_open_count_is_zero():
    cap = ExposureCap(max_open_per_model={"alpha": 3})
    assert cap.open_count("alpha") == 0


def test_record_open_increments_count():
    cap = ExposureCap(max_open_per_model={"alpha": 3})
    cap.record_open("alpha", trade_id="t1")
    cap.record_open("alpha", trade_id="t2")
    assert cap.open_count("alpha") == 2


def test_record_close_decrements_count():
    cap = ExposureCap(max_open_per_model={"alpha": 3})
    cap.record_open("alpha", trade_id="t1")
    cap.record_close("alpha", trade_id="t1")
    assert cap.open_count("alpha") == 0


def test_can_open_blocks_when_at_cap():
    cap = ExposureCap(max_open_per_model={"alpha": 2})
    cap.record_open("alpha", trade_id="t1")
    cap.record_open("alpha", trade_id="t2")
    result = cap.can_open("alpha")
    assert result.allowed is False
    assert result.current == 2
    assert result.cap == 2


def test_can_open_allows_under_cap():
    cap = ExposureCap(max_open_per_model={"alpha": 5})
    cap.record_open("alpha", trade_id="t1")
    result = cap.can_open("alpha")
    assert result.allowed is True
    assert result.current == 1


def test_unlimited_when_no_cap_for_model():
    cap = ExposureCap(max_open_per_model={})
    result = cap.can_open("any_model")
    assert result.allowed is True
    assert result.cap is None
