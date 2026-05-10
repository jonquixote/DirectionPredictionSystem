"""Tests for PaperTrader._build_envelope and provenance envelope construction."""


def make_trader(tmp_path, monkeypatch, tiny_model_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "v3.db"))
    monkeypatch.setenv("KALSHI_CALIBRATION_DIR", str(tmp_path))
    from trading.paper_trader import PaperTrader
    return PaperTrader(
        model_paths={"900s_btc_v3_20260315": tiny_model_path},
        log_dir=str(tmp_path / "logs"),
        confidence_threshold=0.55,
    )


def test_envelope_carries_full_provenance(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    env = t._build_envelope("900s_btc_v3_20260315", platform="paper")
    assert env.model_name == "900s_btc_v3_20260315"
    assert len(env.model_artifact_hash) == 64
    assert len(env.feature_names_hash) == 64
    assert len(env.policy_config_hash) == 64
    assert len(env.calibration_map_hash) == 64
    assert env.feature_version == "v3"
    assert env.training_horizon_seconds == 900
    assert env.platform == "paper"
    assert env.registry_load_generation == 0
    assert env.decision_policy_version == 0


def test_envelope_picks_up_policy_change(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    env_a = t._build_envelope("900s_btc_v3_20260315", platform="paper")
    # Simulate runtime config change
    t.filters["confidence_threshold"] = 0.60
    t.policy_snapshot.capture(t._capture_policy_dict(), initiated_by="api")
    env_b = t._build_envelope("900s_btc_v3_20260315", platform="paper")
    assert env_b.decision_policy_version == env_a.decision_policy_version + 1
    assert env_b.policy_config_hash != env_a.policy_config_hash
    # Model artifact hash unchanged
    assert env_b.model_artifact_hash == env_a.model_artifact_hash


def test_envelope_kalshi_platform(tmp_path, monkeypatch, tiny_model_path):
    t = make_trader(tmp_path, monkeypatch, tiny_model_path)
    env = t._build_envelope("900s_btc_v3_20260315", platform="kalshi")
    assert env.platform == "kalshi"
