"""Tests for scripts.train_fleet."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def test_train_fleet_dry_run_enumerates_84_models(tmp_path, monkeypatch):
    """Test that enumerate_fleet produces 84 models (4 symbols × 7 horizons × 3 train_days)."""
    from scripts.train_fleet import enumerate_fleet

    fleet = enumerate_fleet(
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        horizons=[60, 180, 300, 600, 900, 1200, 1800],
        train_days_list=[90, 180, 330],
    )
    assert len(fleet) == 4 * 7 * 3
    for cell in fleet:
        assert "symbol" in cell
        assert "horizon" in cell
        assert "train_days" in cell
        assert cell["name"].startswith("h")
        assert "_v3_" in cell["name"]
        assert cell["name"].endswith("d")


def test_train_fleet_resume_skips_complete(tmp_path):
    """Test that filter_pending correctly skips completed models."""
    state = tmp_path / "fleet_state.json"
    state.write_text(
        json.dumps(
            {
                "completed": ["h60_btc_v3_90d"],
                "failed": [],
                "in_progress": [],
            }
        )
    )
    from scripts.train_fleet import filter_pending

    fleet = [
        {"name": "h60_btc_v3_90d"},
        {"name": "h60_btc_v3_180d"},
    ]
    pending = filter_pending(fleet, state)
    assert len(pending) == 1
    assert pending[0]["name"] == "h60_btc_v3_180d"


def test_train_one_success(tmp_path):
    """Test that train_one successfully calls subprocess and registers model."""
    from scripts.train_fleet import train_one

    cell = {"symbol": "BTCUSDT", "horizon": 300, "train_days": 90, "name": "h300_btc_v3_90d"}

    # Mock subprocess.run to avoid actual training
    with patch("scripts.train_fleet.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)

        result = train_one(
            cell,
            train_end="2026-04-26",
            feature_dir="/fake/features",
            output_root=str(tmp_path),
            evaluation_windows=[300, 900, 1800],
            db_path=None,  # Skip registration for this test
        )

    assert result["name"] == "h300_btc_v3_90d"
    assert result["status"] == "DONE"
    assert "duration_s" in result

    # Verify subprocess was called with correct args
    assert mock_run.called
    call_args = mock_run.call_args[0][0]
    assert "--horizon" in call_args
    assert "300" in call_args
    assert "--symbol" in call_args
    assert "BTCUSDT" in call_args


def test_train_one_failure(tmp_path):
    """Test that train_one reports FAILED status on subprocess error."""
    from scripts.train_fleet import train_one

    cell = {"symbol": "ETHUSDT", "horizon": 180, "train_days": 180, "name": "h180_eth_v3_180d"}

    # Mock subprocess.run to raise an exception
    with patch("scripts.train_fleet.subprocess.run") as mock_run:
        mock_run.side_effect = Exception("Subprocess failed")

        result = train_one(
            cell,
            train_end="2026-04-26",
            feature_dir="/fake/features",
            output_root=str(tmp_path),
            evaluation_windows=[300, 900, 1800],
            db_path=None,
        )

    assert result["name"] == "h180_eth_v3_180d"
    assert result["status"] == "FAILED"
    assert "err" in result
    assert "duration_s" in result
