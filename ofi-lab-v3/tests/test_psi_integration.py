import pytest

from storage.psi_integration import compute_psi_against_reference


def test_psi_zero_when_distributions_identical():
    a = [0.1] * 50 + [0.9] * 50
    psi = compute_psi_against_reference(current=a, reference=a, bins=10)
    assert psi < 0.001


def test_psi_high_when_distributions_diverge():
    a = [0.1] * 100
    b = [0.9] * 100
    psi = compute_psi_against_reference(current=a, reference=b, bins=10)
    assert psi > 0.20


def test_psi_handles_empty_inputs():
    psi = compute_psi_against_reference(current=[], reference=[0.1, 0.5], bins=5)
    assert psi == 0.0
