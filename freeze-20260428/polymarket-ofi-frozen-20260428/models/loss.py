from __future__ import annotations
"""
GMADL loss function and input validation.
Spec v2.6, Section 8.

Source: Bieganowski & Ślepaczuk arXiv:2412.18405
INPUTS MUST BE RETURNS, NOT PROBABILITIES.
"""

import torch


def gmadl_loss(
    returns_true: torch.Tensor,
    returns_pred: torch.Tensor,
    a: float = 1.0,
    b: float = 2.0,
) -> torch.Tensor:
    """
    GMADL loss function.
    Formula: (1/N) * sum(|R_i - R_hat_i| * (1 - a*sign(R_i*R_hat_i))^b)
    Parameters a and b tune directional accuracy vs return magnitude balance.

    INPUTS MUST BE RETURNS, NOT PROBABILITIES.
    Both tensors must be in the range [-2, 2] (signed return scale).
    If calling with sigmoid outputs (0-1 range), convert first:
        returns_pred = 2 * p_model - 1
    """
    assert returns_true.abs().max() <= 2.0, (
        "returns_true out of expected range — probabilities? Expected returns in [-2, 2]"
    )
    assert returns_pred.abs().max() <= 2.0, (
        "returns_pred out of expected range — pass 2*p-1, not raw p"
    )

    direction_term = (1 - a * torch.sign(returns_true * returns_pred)).pow(b)
    return torch.mean(torch.abs(returns_true - returns_pred) * direction_term)


def validate_gmadl_inputs(
    returns_true: torch.Tensor,
    returns_pred: torch.Tensor,
) -> None:
    """
    Call ONCE during model initialisation with a representative warm-up batch.
    Do NOT call inside the training loop — a legitimately all-bullish batch
    produces all-positive returns_pred, which would fail the min() < 0 check.

    Checking at init with a known mixed-direction sample catches the
    configuration mistake (passing raw probabilities instead of 2*p-1).
    """
    assert returns_pred.min() < -0.01, (
        "returns_pred contains no negative values in validation sample — "
        "pass 2*p-1, not raw probabilities. "
        f"Got min={returns_pred.min():.4f}, max={returns_pred.max():.4f}"
    )
    assert returns_true.abs().max() <= 2.0, (
        "returns_true out of expected range in validation sample — "
        "probabilities? Expected returns in [-2, 2]"
    )
    assert returns_pred.abs().max() <= 2.0, (
        "returns_pred out of expected range in validation sample — "
        f"got max abs = {returns_pred.abs().max():.4f}"
    )
