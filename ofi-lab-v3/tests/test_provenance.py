# ofi-lab-v3/tests/test_provenance.py
import pytest

from storage.provenance import (
    ProvenanceEnvelope,
    sha256_file,
    sha256_canonical_json,
    feature_names_hash,
    policy_config_hash,
    calibration_map_hash,
)


def test_sha256_file_is_deterministic(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world")
    h1 = sha256_file(str(p))
    h2 = sha256_file(str(p))
    assert h1 == h2
    assert len(h1) == 64
    # Known SHA-256 of "hello world"
    assert h1 == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_sha256_canonical_json_independent_of_key_order():
    a = {"b": 1, "a": 2, "c": [3, 1, 2]}
    b = {"c": [3, 1, 2], "a": 2, "b": 1}
    assert sha256_canonical_json(a) == sha256_canonical_json(b)


def test_feature_names_hash_is_order_independent():
    h1 = feature_names_hash(["mlofi", "ofi", "spread"])
    h2 = feature_names_hash(["spread", "mlofi", "ofi"])
    assert h1 == h2


def test_feature_names_hash_changes_when_member_changes():
    h1 = feature_names_hash(["mlofi", "ofi"])
    h2 = feature_names_hash(["mlofi", "ofi_v2"])
    assert h1 != h2


def test_policy_config_hash_canonicalizes():
    a = {"confidence_threshold": 0.55, "kelly_fraction": 0.25,
         "per_symbol": {"BTCUSDT": 0.55, "SOLUSDT": 0.58}}
    b = {"per_symbol": {"SOLUSDT": 0.58, "BTCUSDT": 0.55},
         "kelly_fraction": 0.25, "confidence_threshold": 0.55}
    assert policy_config_hash(a) == policy_config_hash(b)


def test_calibration_map_hash_changes_with_bins():
    a = {"method": "binmap", "bins": [{"raw": 0.52, "calibrated": 0.50}]}
    b = {"method": "binmap", "bins": [{"raw": 0.52, "calibrated": 0.51}]}
    assert calibration_map_hash(a) != calibration_map_hash(b)


def test_envelope_roundtrip_to_dict():
    env = ProvenanceEnvelope(
        model_name="900s_btc_v3_20260315",
        model_artifact_hash="a" * 64,
        feature_names_hash="b" * 64,
        feature_version="v3",
        training_horizon_seconds=900,
        train_window_start="2025-04-01",
        train_window_end="2026-03-15",
        train_cutoff="2026-03-15",
        registry_load_generation=1,
        policy_config_hash="c" * 64,
        decision_policy_version=7,
        calibration_map_hash="d" * 64,
        platform="paper",
    )
    d = env.to_dict()
    env2 = ProvenanceEnvelope.from_dict(d)
    assert env == env2


def test_envelope_rejects_invalid_platform():
    with pytest.raises(ValueError):
        ProvenanceEnvelope(
            model_name="m", model_artifact_hash="a"*64, feature_names_hash="b"*64,
            feature_version="v3", training_horizon_seconds=900,
            train_window_start=None, train_window_end=None, train_cutoff=None,
            registry_load_generation=0, policy_config_hash="c"*64,
            decision_policy_version=0, calibration_map_hash="d"*64,
            platform="bogus",
        )
