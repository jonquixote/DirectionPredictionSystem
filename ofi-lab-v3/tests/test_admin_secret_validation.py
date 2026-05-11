"""C27: Admin secret validation tests.

Verify V3_ADMIN_SECRET is required in prod mode.
Warn but allow in dev mode.
"""
import os
import pytest
from dashboard_api.services.admin_auth import validate_admin_secret


def test_prod_mode_without_secret_raises(monkeypatch):
    """Prod mode without V3_ADMIN_SECRET should raise ValueError."""
    monkeypatch.delenv("V3_ADMIN_SECRET", raising=False)
    with pytest.raises(ValueError, match="V3_ADMIN_SECRET.*prod"):
        validate_admin_secret(env="prod")


def test_prod_mode_with_secret_passes(monkeypatch):
    """Prod mode with V3_ADMIN_SECRET should pass."""
    monkeypatch.setenv("V3_ADMIN_SECRET", "test-secret-key-64-chars" + "x" * 40)
    # Should not raise
    validate_admin_secret(env="prod")


def test_dev_mode_without_secret_warns(monkeypatch, caplog):
    """Dev mode without V3_ADMIN_SECRET should log warning but not raise."""
    monkeypatch.delenv("V3_ADMIN_SECRET", raising=False)
    # Should not raise
    validate_admin_secret(env="dev")
    # Should log warning
    assert "V3_ADMIN_SECRET" in caplog.text or "random tokens" in caplog.text.lower()


def test_dev_mode_with_secret_passes(monkeypatch):
    """Dev mode with V3_ADMIN_SECRET should pass."""
    monkeypatch.setenv("V3_ADMIN_SECRET", "test-secret-key-64-chars" + "x" * 40)
    # Should not raise
    validate_admin_secret(env="dev")


def test_other_env_mode_without_secret_warns(monkeypatch, caplog):
    """Non-prod/non-dev mode without secret should log warning but not raise."""
    monkeypatch.delenv("V3_ADMIN_SECRET", raising=False)
    # Should not raise
    validate_admin_secret(env="staging")
    # Should log warning
    assert "V3_ADMIN_SECRET" in caplog.text or "random tokens" in caplog.text.lower()
