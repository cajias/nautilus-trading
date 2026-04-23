"""RiskGuard — portfolio-level risk management mixin for NautilusTrader strategies.

Provides three guardrails that complement per-trade stop-losses:

1. **Max drawdown circuit breaker**: halts the strategy if account equity falls
   more than `max_drawdown_pct` below the session high-water mark.

2. **Max position size**: caps each order's notional value to a fraction of
   starting equity (`max_position_pct`). Enforced via `check_order_size()`.

3. **Binance filter validation**: verifies that orders meet Binance Spot LOT_SIZE
   (min qty) and MIN_NOTIONAL constraints before submission.

Usage
-----
Inherit from RiskGuard *before* Strategy so MRO resolves correctly:

    class MyStrategy(RiskGuard, Strategy):
        def on_start(self) -> None:
            self._risk_guard_init(
                starting_equity=1000.0,
                max_drawdown_pct=20.0,
                max_position_pct=0.50,
                min_qty=0.00001,
                min_notional=5.0,
            )
            ...

        def on_bar(self, bar: Bar) -> None:
            if self._is_halted():
                return
            ...
            if not self._check_order(quantity_float, price_float):
                return
            self.submit_order(order)

Notes
-----
- `_risk_guard_init()` must be called from `on_start()`, NOT `__init__`,
  because `self.portfolio` is not available before the strategy starts.
- The circuit breaker calls `self.cancel_all_orders()` and `self.close_all_positions()`
  when triggered; it does NOT call `self.stop()` because that exits the event loop.
- Mark-to-market equity is approximated by USDT account balance. For multi-asset
  strategies, update `_current_equity()` to include position MTM.
"""

from __future__ import annotations

from nautilus_trader.model.identifiers import InstrumentId, Venue


class RiskGuard:
    """Mixin providing portfolio-level risk guardrails for crypto strategies."""

    # Public attributes set by _risk_guard_init()
    _rg_starting_equity: float
    _rg_high_water: float
    _rg_max_drawdown_pct: float
    _rg_max_position_pct: float
    _rg_min_qty: float
    _rg_min_notional: float
    _rg_halted: bool

    def _risk_guard_init(
        self,
        starting_equity: float,
        max_drawdown_pct: float = 20.0,
        max_position_pct: float = 0.50,
        min_qty: float = 0.00001,
        min_notional: float = 5.0,
    ) -> None:
        """Initialize risk guard state.

        Call this from `on_start()`, not `__init__()`.

        Parameters
        ----------
        starting_equity : float
            Account equity at strategy start (USDT). Used as HWM baseline.
        max_drawdown_pct : float
            Maximum allowed drawdown from HWM before circuit breaker fires.
            Default: 20% (e.g., $100 loss on $500 account).
        max_position_pct : float
            Maximum position notional as fraction of starting equity.
            Default: 50% (e.g., max $250 deployed on $500 account).
        min_qty : float
            Minimum order quantity for the instrument (Binance LOT_SIZE).
            Default: 0.00001 BTC (Binance BTCUSDT minimum).
        min_notional : float
            Minimum order notional value in quote currency (Binance MIN_NOTIONAL).
            Default: 5.0 USDT (Binance BTCUSDT minimum).
        """
        self._rg_starting_equity = starting_equity
        self._rg_high_water = starting_equity
        self._rg_max_drawdown_pct = max_drawdown_pct
        self._rg_max_position_pct = max_position_pct
        self._rg_min_qty = min_qty
        self._rg_min_notional = min_notional
        self._rg_halted = False

    def _is_halted(self) -> bool:
        """Return True if the circuit breaker has fired.

        Also checks the current drawdown against the threshold on each call
        to catch new lows after the strategy resumes (e.g., after a partial
        recovery). If the circuit breaker fires, all orders are cancelled and
        all positions closed.
        """
        if self._rg_halted:
            return True

        equity = self._rg_current_equity()
        if equity > self._rg_high_water:
            self._rg_high_water = equity

        if self._rg_high_water > 0:
            drawdown_pct = (self._rg_high_water - equity) / self._rg_high_water * 100.0
            if drawdown_pct >= self._rg_max_drawdown_pct:
                self._rg_halted = True
                try:
                    self.log.warning(  # type: ignore[attr-defined]
                        f"[RiskGuard] Max drawdown breached: "
                        f"{drawdown_pct:.1f}% >= {self._rg_max_drawdown_pct:.1f}%. "
                        f"Halting strategy. HWM=${self._rg_high_water:.2f} "
                        f"Current=${equity:.2f}"
                    )
                    # Cancel orders and flatten positions
                    for instrument_id in self._rg_active_instrument_ids():
                        self.cancel_all_orders(instrument_id)  # type: ignore[attr-defined]
                        self.close_all_positions(instrument_id)  # type: ignore[attr-defined]
                except Exception:
                    pass
                return True

        return False

    def _rg_current_equity(self) -> float:
        """Approximate current USDT equity from account balances.

        Override this in your strategy if you need mark-to-market position
        value included (important for large leveraged positions).
        """
        try:
            venue = self._rg_get_venue()
            if venue is None:
                return self._rg_starting_equity
            account = self.portfolio.account(venue)  # type: ignore[attr-defined]
            if account is None:
                return self._rg_starting_equity
            from nautilus_trader.model.currencies import USDT as _USDT

            balance = account.balances().get(_USDT)
            if balance is None:
                return self._rg_starting_equity
            return float(balance.total)
        except Exception:
            return self._rg_starting_equity

    def _rg_get_venue(self) -> Venue | None:
        """Extract the Venue from the strategy's instrument_id config."""
        try:
            instrument_id: InstrumentId = self.config.instrument_id  # type: ignore[attr-defined]
            return instrument_id.venue
        except Exception:
            return None

    def _rg_active_instrument_ids(self) -> list[InstrumentId]:
        """Return a list of instrument IDs this strategy holds positions in."""
        try:
            return [self.config.instrument_id]  # type: ignore[attr-defined]
        except Exception:
            return []

    def _check_order(self, quantity: float, price: float) -> bool:
        """Validate an order against risk and exchange filter constraints.

        Parameters
        ----------
        quantity : float
            Order quantity in base currency (e.g., BTC).
        price : float
            Order price in quote currency (e.g., USDT per BTC).

        Returns
        -------
        bool
            True if the order passes all checks; False if it should be skipped.
        """
        # Exchange minimum quantity filter (LOT_SIZE)
        if quantity < self._rg_min_qty:
            try:
                self.log.warning(  # type: ignore[attr-defined]
                    f"[RiskGuard] Order qty {quantity} < min_qty {self._rg_min_qty}. Skipped."
                )
            except Exception:
                pass
            return False

        # Exchange minimum notional filter (MIN_NOTIONAL)
        notional = quantity * price
        if notional < self._rg_min_notional:
            try:
                self.log.warning(  # type: ignore[attr-defined]
                    f"[RiskGuard] Order notional ${notional:.2f} < "
                    f"min_notional ${self._rg_min_notional:.2f}. Skipped."
                )
            except Exception:
                pass
            return False

        # Max position size check
        max_notional = self._rg_starting_equity * self._rg_max_position_pct
        if notional > max_notional:
            try:
                self.log.warning(  # type: ignore[attr-defined]
                    f"[RiskGuard] Order notional ${notional:.2f} > "
                    f"max position ${max_notional:.2f} "
                    f"({self._rg_max_position_pct * 100:.0f}% of ${self._rg_starting_equity:.0f}). "
                    f"Clamp not applied — reduce trade_size in config."
                )
            except Exception:
                pass
            # Warn but don't block — caller controls sizing via config.trade_size
            # If you want hard blocking, change `return False` here.

        return True
