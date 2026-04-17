"""Round 11 configuration for the crypto competition evaluator.

Defines the hidden evaluation window, initial capital, and the instrument
allowlist that submissions must target. Importable from both the evaluator
and from test fixtures so overrides stay in one place.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

ROUND_CONFIG: dict[str, Any] = {
    "round": 11,
    "eval_period": {"start": "2025-10-01", "end": "2025-12-31"},
    "initial_capital_usdt": Decimal("1000.00"),
    "instruments_allowlist": [
        "BTCUSDT.BINANCE",
        "ETHUSDT.BINANCE",
        "BNBUSDT.BINANCE",
        "SOLUSDT.BINANCE",
        "AVAXUSDT.BINANCE",
        "LINKUSDT.BINANCE",
        "DOGEUSDT.BINANCE",
        "XRPUSDT.BINANCE",
    ],
}
