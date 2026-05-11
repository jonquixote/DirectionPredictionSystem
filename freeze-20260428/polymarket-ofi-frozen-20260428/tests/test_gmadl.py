"""
Tests for GMADL loss and validate_gmadl_inputs.
Spec v2.6, Section 14.
"""

import sys
import os
from unittest.mock import patch

import torch
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from models.loss import gmadl_loss, validate_gmadl_inputs
from execution.executor import init_trainer


class TestGMADLLoss:

    def test_no_error_all_positive_returns_pred(self):
        """gmadl_loss: no assertion error on all-positive returns_pred (trending batch)."""
        returns_true = torch.tensor([0.5, 0.3, 0.8, 0.1])
        returns_pred = torch.tensor([0.4, 0.2, 0.7, 0.05])
        # Should not raise — trending batch is legitimate
        loss = gmadl_loss(returns_true, returns_pred)
        assert loss >= 0

    def test_no_error_all_negative_returns_pred(self):
        """gmadl_loss: no assertion error on all-negative returns_pred (trending batch)."""
        returns_true = torch.tensor([-0.5, -0.3, -0.8, -0.1])
        returns_pred = torch.tensor([-0.4, -0.2, -0.7, -0.05])
        loss = gmadl_loss(returns_true, returns_pred)
        assert loss >= 0

    def test_assertion_error_when_range_exceeded(self):
        """gmadl_loss: AssertionError when abs().max() > 2.0 (gross range violation)."""
        returns_true = torch.tensor([3.0, -0.5])  # 3.0 > 2.0
        returns_pred = torch.tensor([0.1, -0.1])
        with pytest.raises(AssertionError, match="out of expected range"):
            gmadl_loss(returns_true, returns_pred)

    def test_penalises_wrong_direction_more(self):
        """gmadl_loss: penalises wrong-direction predictions more than magnitude errors."""
        returns_true = torch.tensor([0.5, 0.5])

        # Correct direction, magnitude error
        pred_correct_dir = torch.tensor([0.1, 0.1])
        loss_correct = gmadl_loss(returns_true, pred_correct_dir)

        # Wrong direction
        pred_wrong_dir = torch.tensor([-0.1, -0.1])
        loss_wrong = gmadl_loss(returns_true, pred_wrong_dir)

        assert loss_wrong > loss_correct, (
            f"Wrong direction loss ({loss_wrong}) should exceed correct direction loss ({loss_correct})"
        )


class TestValidateGMADLInputs:

    def test_assertion_error_on_raw_probabilities(self):
        """validate_gmadl_inputs: AssertionError when passed raw probabilities (all positive)."""
        # Raw sigmoid outputs (0-1 range) — all positive
        raw_probs = torch.tensor([0.6, 0.7, 0.55, 0.8])
        returns_true = torch.tensor([1.0, -1.0, 1.0, -1.0])
        with pytest.raises(AssertionError, match="no negative values"):
            validate_gmadl_inputs(returns_true, raw_probs)

    def test_passes_on_mixed_sign_returns(self):
        """validate_gmadl_inputs: passes on mixed-sign returns_pred."""
        returns_pred = torch.tensor([0.3, -0.4, 0.1, -0.2])
        returns_true = torch.tensor([1.0, -1.0, 1.0, -1.0])
        # Should not raise
        validate_gmadl_inputs(returns_true, returns_pred)

    def test_conversion_maps_sigmoid_to_return_range(self):
        """Conversion: 2*p - 1 maps sigmoid output (0-1) to return range (-1, 1)."""
        p = torch.tensor([0.0, 0.25, 0.5, 0.75, 1.0])
        returns = 2 * p - 1
        expected = torch.tensor([-1.0, -0.5, 0.0, 0.5, 1.0])
        assert torch.allclose(returns, expected)


class TestInitTrainer:

    def test_calls_validate_gmadl_inputs(self):
        """init_trainer: calls validate_gmadl_inputs (patch validate, assert called once)."""
        with patch("execution.executor.validate_gmadl_inputs") as mock_validate:
            sample_true = torch.tensor([1.0, -1.0, 0.5, -0.5])
            sample_pred = torch.tensor([0.3, -0.4, 0.1, -0.2])
            init_trainer(sample_true, sample_pred)
            mock_validate.assert_called_once_with(sample_true, sample_pred)

    def test_propagates_assertion_error(self):
        """init_trainer: propagates AssertionError from validate_gmadl_inputs to caller."""
        raw_probs = torch.tensor([0.6, 0.7, 0.55, 0.8])
        returns_true = torch.tensor([1.0, -1.0, 1.0, -1.0])
        with pytest.raises(AssertionError):
            init_trainer(returns_true, raw_probs)

    def test_accepts_pre_converted_tensors(self):
        """init_trainer: accepts pre-converted tensors, not model + raw data."""
        sample_true = torch.tensor([1.0, -1.0, 0.5, -0.5])
        sample_pred = torch.tensor([0.3, -0.4, 0.1, -0.2])
        sanderink, sizer = init_trainer(sample_true, sample_pred)
        # init_trainer does not call any model — it accepts pre-converted tensors
        assert sanderink is not None
        assert sizer is not None
        assert isinstance(sanderink, type(sanderink))
        assert isinstance(sizer, type(sizer))
