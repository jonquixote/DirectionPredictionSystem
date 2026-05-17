"""TDD tests for the training dispatch router."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Create a test client with mocked paths."""
    monkeypatch.setenv("V3_FEATURE_DIR", str(tmp_path / "features"))
    monkeypatch.setenv("V3_OUTPUT_ROOT", str(tmp_path / "models"))
    monkeypatch.setenv("V3_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("V3_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("V3_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("V3_PYTHON", "python3")
    monkeypatch.setenv("ADMIN_SECRET", "test-secret-123")
    monkeypatch.setenv("V3_ENV", "dev")

    # Create feature dirs so data-status has something to scan
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]:
        d = tmp_path / "features" / sym
        d.mkdir(parents=True)
        # Create a fake parquet file
        (d / f"2026-05-01_{sym}_features.parquet").touch()
        (d / f"2026-05-11_{sym}_features.parquet").touch()

    # Must import AFTER env vars are set so the module picks them up
    import importlib
    import dashboard_api.routers.training as training_mod
    importlib.reload(training_mod)

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(training_mod.router, prefix="/api")

    # Reset global state between tests
    training_mod._current_job = None

    return TestClient(app)


def test_status_returns_idle_when_no_job(client):
    """GET /api/training/status should return idle when nothing is running."""
    resp = client.get("/api/training/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "idle"


def test_dispatch_creates_background_job(client):
    """POST /api/training/dispatch should accept valid config and return 202."""
    with patch("dashboard_api.routers.training.subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc

        resp = client.post("/api/training/dispatch", json={
            "symbols": ["BTCUSDT"],
            "horizons": [300],
            "train_days": [90],
            "train_end": "2026-05-11",
        })
        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert body["state"] == "running"


def test_dispatch_rejects_empty_symbols(client):
    """POST with empty symbols should return 422."""
    resp = client.post("/api/training/dispatch", json={
        "symbols": [],
        "horizons": [300],
        "train_days": [90],
        "train_end": "2026-05-11",
    })
    assert resp.status_code == 422


def test_dispatch_rejects_unknown_symbol(client):
    """POST with unknown symbol should return 422."""
    resp = client.post("/api/training/dispatch", json={
        "symbols": ["DOGEUSDT"],
        "horizons": [300],
        "train_days": [90],
        "train_end": "2026-05-11",
    })
    assert resp.status_code == 422


def test_data_status_returns_date_ranges(client):
    """GET /api/training/data-status should return per-symbol date info."""
    resp = client.get("/api/training/data-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "symbols" in body
    btc = body["symbols"]["BTCUSDT"]
    assert btc["files"] == 2
    assert btc["start"] == "2026-05-01"
    assert btc["end"] == "2026-05-11"
