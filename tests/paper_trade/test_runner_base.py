"""ABC behavior: subclasses must override main()."""

from __future__ import annotations

import pytest
from nautilus_trading.paper_trade.runner_base import PaperTradeRunner


def test_paper_trade_runner_is_abstract():
    """Instantiating the ABC directly must raise TypeError."""
    with pytest.raises(TypeError, match="abstract"):
        PaperTradeRunner()  # type: ignore[abstract]


def test_paper_trade_runner_subclass_without_main_is_abstract():
    """A subclass that forgets to override main() is still abstract."""

    class Incomplete(PaperTradeRunner):
        pass

    with pytest.raises(TypeError, match="abstract"):
        Incomplete()  # type: ignore[abstract]


def test_paper_trade_runner_subclass_with_main_instantiates():
    """A subclass that overrides main() instantiates cleanly."""

    class Concrete(PaperTradeRunner):
        def main(self) -> None:
            return None

    assert Concrete().main() is None
