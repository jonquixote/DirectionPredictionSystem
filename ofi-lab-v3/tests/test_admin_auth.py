"""C11: Confirmation token tests."""
import time
import pytest

from dashboard_api.services.admin_auth import (
    issue_confirmation_token,
    verify_confirmation_token,
    ConfirmationError,
    _USED_TOKENS,
)


@pytest.fixture(autouse=True)
def _clear_used_tokens():
    _USED_TOKENS.clear()
    yield
    _USED_TOKENS.clear()


def test_issued_token_verifies_within_ttl():
    tok = issue_confirmation_token(action="enable_live", target="h60_xrp", by="op")
    payload = verify_confirmation_token(tok, action="enable_live", target="h60_xrp")
    assert payload["by"] == "op"


def test_wrong_action_rejects():
    tok = issue_confirmation_token(action="enable_live", target="h60_xrp", by="op")
    with pytest.raises(ConfirmationError):
        verify_confirmation_token(tok, action="rollback", target="h60_xrp")


def test_expired_token_rejects(monkeypatch):
    tok = issue_confirmation_token(action="enable_live", target="h60_xrp", by="op")
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 600)
    with pytest.raises(ConfirmationError):
        verify_confirmation_token(tok, action="enable_live", target="h60_xrp")


def test_token_single_use():
    tok = issue_confirmation_token(action="enable_live", target="h60_xrp", by="op")
    verify_confirmation_token(tok, action="enable_live", target="h60_xrp")
    with pytest.raises(ConfirmationError):
        verify_confirmation_token(tok, action="enable_live", target="h60_xrp")
