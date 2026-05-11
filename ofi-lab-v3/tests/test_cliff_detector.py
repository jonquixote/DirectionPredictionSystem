from storage.cliff_detector import (
    detect_ev_cliff, detect_calibration_cliff, CliffResult,
)


def test_no_cliff_when_metrics_stable():
    # Recent 20 EV ≈ baseline ≈ 0.02
    result = detect_ev_cliff(
        recent_ev=[0.02] * 20, baseline_ev=0.02,
        cliff_drop_threshold=0.05,
    )
    assert result.triggered is False


def test_cliff_when_recent_ev_drops_below_threshold():
    result = detect_ev_cliff(
        recent_ev=[-0.04] * 20, baseline_ev=0.02,
        cliff_drop_threshold=0.05,
    )
    assert result.triggered is True
    assert "drop" in result.reason.lower() or "cliff" in result.reason.lower()


def test_calibration_cliff_when_error_spikes():
    result = detect_calibration_cliff(
        recent_error=0.18, baseline_error=0.04,
        spike_threshold=0.10,
    )
    assert result.triggered is True


def test_no_calibration_cliff_when_error_stable():
    result = detect_calibration_cliff(
        recent_error=0.05, baseline_error=0.04, spike_threshold=0.10,
    )
    assert result.triggered is False
