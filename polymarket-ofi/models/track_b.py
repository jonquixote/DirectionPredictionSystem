from __future__ import annotations
"""
Track B: CNN-BiLSTM-Attention sequence model.
Spec v2.6, Section 8.

1-min OHLCV + order book, 30-60 bar lookback.
CNN → BiLSTM → Attention → sigmoid output.

num_layers=2 on BiLSTM: PyTorch silently ignores dropout when num_layers=1.
Using 2 layers makes dropout active on the inter-layer connection.
"""

import torch
import torch.nn as nn


class TrackB_SequenceModel(nn.Module):
    """
    Track B: Sequence input.
    CNN → BiLSTM → Additive Attention → sigmoid output.

    Additive attention (scalar projection across full 128-dim BiLSTM output).
    If model underfits sequence structure, multi-head attention is the
    natural upgrade — replace nn.Linear(128,1) with nn.MultiheadAttention.
    """

    def __init__(
        self, input_channels: int, seq_len: int = 60, dropout: float = 0.3
    ):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(input_channels, 64, kernel_size=3, padding=1),
            nn.ReLU(),
            nn.Conv1d(64, 128, kernel_size=3, padding=1),
            nn.ReLU(),
        )
        # num_layers=2 required for dropout to be active between layers.
        self.bilstm = nn.LSTM(
            128,
            64,
            num_layers=2,
            batch_first=True,
            bidirectional=True,
            dropout=dropout,
        )
        self.attention = nn.Linear(128, 1)
        self.fc = nn.Sequential(
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (batch, seq_len, input_channels)
        x = self.conv(x.permute(0, 2, 1)).permute(0, 2, 1)
        lstm_out, _ = self.bilstm(x)
        attn_weights = torch.softmax(self.attention(lstm_out), dim=1)
        context = (attn_weights * lstm_out).sum(dim=1)
        return self.fc(context).squeeze(-1)
