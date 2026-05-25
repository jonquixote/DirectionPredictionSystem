"""Per-regime threshold override resolution.

Shared between dashboard simulate path (`services/analysis.py:_apply_filter_row`)
and live trader path (`trading/boundary_scorer.py`) so the two cannot drift.

Schema:

    {
      "confidence_threshold": 0.52,
      "ev_threshold": 0.0,
      "regime_gates": {
        "volatility": {
          "high":   {"confidence_threshold": 0.58, "ev_threshold": 0.01},
          "low":    {"confidence_threshold": 0.50}
        },
        "liquidity": { "thin": {"confidence_threshold": 0.60} }
      }
    }

Axes processed in order ``volatility``, ``liquidity``, ``trend``. Later axes
override earlier ones for keys they specify (most-specific override wins).
"""
from __future__ import annotations

from typing import Mapping, Optional

_AXES = ("volatility", "liquidity", "trend")


def resolve_thresholds(
    filter_config: Optional[Mapping],
    regime: Optional[Mapping],
    base_confidence: Optional[float],
    base_ev: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    """Return (effective_confidence_threshold, effective_ev_threshold).

    ``regime`` is a mapping with keys ``volatility``, ``liquidity``, ``trend``
    (any subset). When no override applies, returns the base thresholds.
    """
    ct = base_confidence
    ev = base_ev
    if not filter_config:
        return ct, ev
    gates = filter_config.get("regime_gates") if isinstance(filter_config, Mapping) else None
    if not gates or not isinstance(gates, Mapping):
        return ct, ev
    if not regime:
        return ct, ev
    for axis in _AXES:
        axis_gates = gates.get(axis)
        if not isinstance(axis_gates, Mapping):
            continue
        value = regime.get(axis)
        if not value:
            continue
        gate = axis_gates.get(value)
        if not isinstance(gate, Mapping):
            continue
        if "confidence_threshold" in gate:
            ct = gate["confidence_threshold"]
        if "ev_threshold" in gate:
            ev = gate["ev_threshold"]
    return ct, ev


def validate_regime_gates(regime_gates: object) -> None:
    """Raise ValueError if regime_gates schema malformed.

    Allowed structure: {axis: {regime_value: {confidence_threshold|ev_threshold: float}}}
    """
    if regime_gates is None:
        return
    if not isinstance(regime_gates, Mapping):
        raise ValueError("regime_gates must be a mapping")
    for axis, axis_gates in regime_gates.items():
        if axis not in _AXES:
            raise ValueError(f"regime_gates axis must be one of {_AXES}, got {axis!r}")
        if not isinstance(axis_gates, Mapping):
            raise ValueError(f"regime_gates[{axis!r}] must be a mapping")
        for value, gate in axis_gates.items():
            if not isinstance(value, str):
                raise ValueError(f"regime_gates[{axis!r}] keys must be strings")
            if not isinstance(gate, Mapping):
                raise ValueError(f"regime_gates[{axis!r}][{value!r}] must be a mapping")
            for k, v in gate.items():
                if k not in {"confidence_threshold", "ev_threshold"}:
                    raise ValueError(
                        f"regime_gates[{axis!r}][{value!r}] only confidence_threshold/ev_threshold supported, got {k!r}"
                    )
                if not isinstance(v, (int, float)):
                    raise ValueError(
                        f"regime_gates[{axis!r}][{value!r}][{k!r}] must be numeric, got {type(v).__name__}"
                    )
