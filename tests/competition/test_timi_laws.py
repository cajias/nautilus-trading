"""Tests for ``competition/timi_laws.py``.

Each test writes a minimal Python snippet to ``tmp_path``, parses it, and
runs one of the three public law-check functions directly. A positive test
confirms the happy-path fixture passes all three checks; the three negative
tests each violate exactly one law; the two edge tests pin down the
subtleties (lifecycle exemption, runtime Decimal).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPETITION_DIR = _REPO_ROOT / "competition"
if str(_COMPETITION_DIR) not in sys.path:
    sys.path.insert(0, str(_COMPETITION_DIR))

from timi_laws import (  # noqa: E402
    check_functional_cohesion,
    check_parameter_externalization,
    check_unidirectional_dependency,
    enforce_laws,
)


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_PASSING_STRATEGY = '''\
"""Happy-path TiMi strategy for tests."""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class TimiConfigBTCUSDT(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    sma_fast: int = 10
    sma_slow: int = 30
    trade_size: Decimal = Decimal("0.001")


def compute_sma(values: list[float], period: int) -> float:
    """Simple moving average helper (function layer)."""
    if len(values) < period:
        return 0.0
    return sum(values[-period:]) / period


class TimiStrategyBTCUSDT(Strategy):
    """Strategy layer: reads config, calls helpers, submits orders."""

    def __init__(self, config: TimiConfigBTCUSDT) -> None:
        super().__init__(config)
        self._closes: list[float] = []

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self._closes.append(float(bar.close))
        if len(self._closes) < self.config.sma_slow:
            return
        fast = compute_sma(self._closes, self.config.sma_fast)
        slow = compute_sma(self._closes, self.config.sma_slow)
        if fast > slow and self.portfolio.is_flat(self.config.instrument_id):
            quantity = Decimal(str(bar.close)) * self.config.trade_size
            self.log.info(f"signal fast={fast} slow={slow} qty={quantity}")

    def on_stop(self) -> None:
        self._closes.clear()
'''


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Positive test
# ---------------------------------------------------------------------------


def test_passing_strategy_has_no_violations(tmp_path: Path) -> None:
    """The reference TiMi strategy fixture must pass all three laws."""
    strategy = _write(tmp_path, "strategy.py", _PASSING_STRATEGY)
    tree = _parse(strategy)

    cohesion = check_functional_cohesion(tree, strategy)
    externalization = check_parameter_externalization(tree, strategy)
    dependency = check_unidirectional_dependency(tmp_path)

    assert cohesion == [], [v.render() for v in cohesion]
    assert externalization == [], [v.render() for v in externalization]
    assert dependency == [], [v.render() for v in dependency]

    assert enforce_laws(tmp_path) == []


# ---------------------------------------------------------------------------
# Law 1 negative: cohesion god-function on a non-exempt method
# ---------------------------------------------------------------------------


def test_law1_violation_god_function(tmp_path: Path) -> None:
    """A ``compute_signal`` method packed with ~60 AST nodes must fail law 1."""
    lines: list[str] = [
        "from nautilus_trader.trading.strategy import Strategy",
        "",
        "",
        "class TimiStrategyBTC(Strategy):",
        "    def compute_signal(self, closes):",
    ]
    # Generate 30 sequential assignments -> ~60 AST nodes (Assign + Name + Num each).
    for i in range(30):
        lines.append(f"        x_{i} = closes[-1]")
    lines.append("        return x_0")
    source = "\n".join(lines) + "\n"
    strategy = _write(tmp_path, "strategy.py", source)
    tree = _parse(strategy)

    violations = check_functional_cohesion(tree, strategy)
    assert len(violations) == 1
    assert violations[0].law == "cohesion"
    assert "compute_signal" in violations[0].message


# ---------------------------------------------------------------------------
# Law 2 negative: helper imports back from strategy
# ---------------------------------------------------------------------------


def test_law2_violation_circular_import(tmp_path: Path) -> None:
    """A ``_helpers`` module that imports from ``strategy`` must fail law 2."""
    _write(tmp_path, "strategy.py", _PASSING_STRATEGY)
    _write(
        tmp_path,
        "_helpers.py",
        "from strategy import TimiStrategyBTCUSDT\n\n"
        "def helper() -> None:\n"
        "    return None\n",
    )

    violations = check_unidirectional_dependency(tmp_path)
    assert len(violations) == 1
    assert violations[0].law == "dependency"
    assert violations[0].file.name == "_helpers.py"


# ---------------------------------------------------------------------------
# Law 3 negative: Decimal("0.5") in a Strategy method body
# ---------------------------------------------------------------------------


def test_law3_violation_decimal_literal(tmp_path: Path) -> None:
    """Passing ``Decimal("0.5")`` to ``order_factory.market`` must fail law 3."""
    source = '''\
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class TimiConfigBTC(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType


class TimiStrategyBTC(Strategy):
    def __init__(self, config: TimiConfigBTC) -> None:
        super().__init__(config)

    def custom_trade(self, bar: Bar) -> None:
        quantity = Decimal("0.5")
        self.log.info(f"qty={quantity}")
'''
    strategy = _write(tmp_path, "strategy.py", source)
    tree = _parse(strategy)

    violations = check_parameter_externalization(tree, strategy)
    assert len(violations) == 1
    assert violations[0].law == "externalization"
    assert "0.5" in violations[0].message or "0.5" in str(violations[0].message)


# ---------------------------------------------------------------------------
# Edge 1: exempt lifecycle method with high complexity must pass law 1
# ---------------------------------------------------------------------------


def test_law1_edge_lifecycle_exempt(tmp_path: Path) -> None:
    """``on_bar`` with cyclomatic complexity ~15 must still pass law 1."""
    lines: list[str] = [
        "from nautilus_trader.trading.strategy import Strategy",
        "",
        "",
        "class TimiStrategyBTC(Strategy):",
        "    def on_bar(self, bar) -> None:",
    ]
    # Build a branchy on_bar: 14 if-statements + return for cyclomatic ~= 15.
    for i in range(14):
        lines.append(f"        if bar.close > {i}:")
        lines.append(f"            self.log.info('branch {i}')")
    lines.append("        return None")
    source = "\n".join(lines) + "\n"
    strategy = _write(tmp_path, "strategy.py", source)
    tree = _parse(strategy)

    violations = check_functional_cohesion(tree, strategy)
    assert violations == [], [v.render() for v in violations]


# ---------------------------------------------------------------------------
# Edge 2: Decimal(str(bar.close)) inside a strategy method must pass law 3
# ---------------------------------------------------------------------------


def test_law3_edge_runtime_decimal(tmp_path: Path) -> None:
    """``Decimal(str(bar.close))`` must pass law 3 (traces to runtime data)."""
    source = '''\
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class TimiConfigBTC(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType


class TimiStrategyBTC(Strategy):
    def __init__(self, config: TimiConfigBTC) -> None:
        super().__init__(config)

    def consume(self, bar: Bar) -> None:
        price = Decimal(str(bar.close))
        self.log.info(f"price={price}")
'''
    strategy = _write(tmp_path, "strategy.py", source)
    tree = _parse(strategy)

    violations = check_parameter_externalization(tree, strategy)
    assert violations == [], [v.render() for v in violations]
