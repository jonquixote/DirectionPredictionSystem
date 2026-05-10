"""Smoke tests on PaperTrader v3 init wiring."""


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_trader_has_sqlite_ledger(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    from storage.sqlite_ledger import SQLiteLedger
    assert isinstance(t.sqlite_ledger, SQLiteLedger)


def test_trader_has_calibrator_registry(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    from execution.calibration import CalibratorRegistry
    assert isinstance(t.calibrators, CalibratorRegistry)


def test_registry_state_bootstrapped(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    assert t.registry_state.current_generation() == 0


def test_policy_snapshot_captured_at_boot(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    v, h = t.policy_snapshot.current()
    assert v == 0
    assert len(h) == 64


def test_legacy_jsonl_ledger_still_present(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    from trading.ledger import Ledger
    assert all(isinstance(l, Ledger) for l in t.ledgers.values())


def test_model_artifact_hash_computed_at_init(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    env = t._model_envelopes["900s_btc_v3_20260315"]
    assert "model_artifact_hash" in env
    assert len(env["model_artifact_hash"]) == 64
    assert "feature_names_hash" in env
    assert len(env["feature_names_hash"]) == 64
