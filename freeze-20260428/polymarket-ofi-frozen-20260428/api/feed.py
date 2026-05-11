from __future__ import annotations
"""
Abstract order book feed interface.
All exchange feeds must implement this ABC.
Nothing outside the feed layer should know which exchange it's talking to.
"""

from abc import ABC, abstractmethod
from typing import Callable


class AbstractOrderBookFeed(ABC):
    """
    Exchange-agnostic order book feed interface.

    Implementations: BybitOrderBookManager (primary).
    Future: CoinbaseOrderBookManager, etc.
    """

    @abstractmethod
    def connect(self) -> None:
        """Open WebSocket connection and begin streaming."""
        ...

    @abstractmethod
    def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to order book updates for given symbols."""
        ...

    @abstractmethod
    def get_snapshot(self, symbol: str) -> dict:
        """
        Return current full order book snapshot.
        Returns: {"bids": [(price, size), ...], "asks": [(price, size), ...]}
        Bids sorted descending, asks sorted ascending.
        """
        ...

    @abstractmethod
    def on_update(self, callback: Callable) -> None:
        """
        Register callback fired on every incremental update.
        Callback signature: callback(symbol: str, bids: list, asks: list)
        """
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection cleanly."""
        ...
