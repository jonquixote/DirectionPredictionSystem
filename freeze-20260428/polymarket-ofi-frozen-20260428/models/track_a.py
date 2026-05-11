from __future__ import annotations
"""
Track A: Tabular MLP.
Spec v2.6, Section 8.

64 MI-selected features → [256 → 128 → 64] → sigmoid output.
Architecture consistent with Kuznetsov et al. 2026 (CEUR-WS proceedings).
"""

import torch
import torch.nn as nn


class TrackA_MLP(nn.Module):
    """
    Track A: Tabular input.
    64 MI-selected features → [256 → 128 → 64] → sigmoid output.
    """

    def __init__(self, input_dim: int = 64, dropout: float = 0.3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 256),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)
