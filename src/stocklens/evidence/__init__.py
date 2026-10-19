"""The Evidence lab: does a signal family actually work on *this* stock?

A data aggregator shows RSI, moving averages and ratios and never asks whether
any of them has predicted anything for the stock on the page. This package
asks, on the stock's own history, walk-forward, with the statistics that stop
a search from fooling itself.
"""

from __future__ import annotations

from .walkforward import test_families

__all__ = ["test_families"]
