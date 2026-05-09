"""Provenance envelope and hash utilities for v3.

Every prediction and trade carries a ProvenanceEnvelope so that any
record can be unambiguously tied back to the exact (model artifact,
feature schema, policy config, calibration map, registry generation)
that produced it. Hashes are computed deterministically from canonical
representations so that semantically equivalent inputs always produce
the same hash.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from typing import Any, Iterable, Optional

VALID_PLATFORMS = {"paper", "kalshi", "polymarket"}


def sha256_file(path: str) -> str:
    """SHA-256 hex digest of a file's full contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_canonical_json(obj: Any) -> str:
    """SHA-256 of obj serialized with sorted keys and no whitespace.

    Stable across dict insertion orders. NaN/Inf rejected — call sites
    must clean numeric values before hashing.
    """
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                         allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def feature_names_hash(names: Iterable[str]) -> str:
    """Hash a feature-name list. Order independent (sorted before hashing)."""
    return sha256_canonical_json(sorted(names))


def policy_config_hash(config: dict) -> str:
    """Hash the active policy config snapshot."""
    return sha256_canonical_json(config)


def calibration_map_hash(cal_map: dict) -> str:
    """Hash the active calibration bin map.

    Expects {"method": ..., "bins": [{"raw": ..., "calibrated": ...}, ...]}.
    """
    return sha256_canonical_json(cal_map)


@dataclass(frozen=True)
class ProvenanceEnvelope:
    """Full identity context for a single prediction or trade.

    All hash fields are 64-character lowercase hex SHA-256 digests.
    All counters are non-negative monotonic integers.
    """
    model_name: str
    model_artifact_hash: str
    feature_names_hash: str
    feature_version: str
    training_horizon_seconds: int
    train_window_start: Optional[str]
    train_window_end: Optional[str]
    train_cutoff: Optional[str]
    registry_load_generation: int
    policy_config_hash: str
    decision_policy_version: int
    calibration_map_hash: str
    platform: str

    def __post_init__(self) -> None:
        if self.platform not in VALID_PLATFORMS:
            raise ValueError(
                f"invalid platform {self.platform!r}, expected one of {VALID_PLATFORMS}"
            )
        for field, value in [
            ("model_artifact_hash", self.model_artifact_hash),
            ("feature_names_hash", self.feature_names_hash),
            ("policy_config_hash", self.policy_config_hash),
            ("calibration_map_hash", self.calibration_map_hash),
        ]:
            if not (isinstance(value, str) and len(value) == 64):
                raise ValueError(f"{field} must be 64-char hex, got {value!r}")
        if self.training_horizon_seconds <= 0:
            raise ValueError("training_horizon_seconds must be positive")
        if self.registry_load_generation < 0:
            raise ValueError("registry_load_generation must be >= 0")
        if self.decision_policy_version < 0:
            raise ValueError("decision_policy_version must be >= 0")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProvenanceEnvelope":
        return cls(**d)
