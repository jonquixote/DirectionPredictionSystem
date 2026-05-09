import time

import pytest

from execution.calibration import (
    ProbabilityCalibrator, MIN_SAMPLES_PER_BIN,
    CalibratorRegistry,
)


def test_min_samples_per_bin_is_twenty():
    assert MIN_SAMPLES_PER_BIN == 20


def test_per_model_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    reg = CalibratorRegistry(base_dir=str(tmp_path))
    a = reg.get("900s_btc_v3_20260315", "BTCUSDT", 900)
    b = reg.get("60s_btc_v3_20260315", "BTCUSDT", 60)
    assert a is not b
    a.record_outcome(0.55, True)
    a.record_outcome(0.55, True)
    # b unaffected
    assert len(b._outcomes) == 0


def test_min_samples_per_bin_filters_overfit_extremes(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    reg = CalibratorRegistry(base_dir=str(tmp_path))
    c = reg.get("m", "S", 900)
    # 25 samples at conf=0.52 (eligible), 19 samples at conf=0.54 (filtered)
    for _ in range(25):
        c.record_outcome(0.52, True)
    for _ in range(19):
        c.record_outcome(0.54, True)
    refit_keys = {b["raw"] for b in c._bins}
    assert 0.52 in refit_keys
    assert 0.54 not in refit_keys


def test_last_refit_at_updates_after_refit(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    reg = CalibratorRegistry(base_dir=str(tmp_path))
    c = reg.get("m", "S", 900)
    assert c.last_refit_at is None
    for _ in range(40):  # exceeds MIN_REFIT_SAMPLES=30
        c.record_outcome(0.52, True)
    assert c.last_refit_at is not None


def test_is_stale_after_24h(tmp_path, monkeypatch):
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    reg = CalibratorRegistry(base_dir=str(tmp_path))
    c = reg.get("m", "S", 900)
    for _ in range(40):
        c.record_outcome(0.52, True)
    # Force last_refit_at into the past
    c.last_refit_at = c.last_refit_at - 25 * 3600
    assert c.is_stale(now_epoch=time.time())
