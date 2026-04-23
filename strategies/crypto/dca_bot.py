from decimal import Decimal

from nautilus_trader.config import PositiveInt, StrategyConfig
from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.trading.strategy import Strategy

from strategies.crypto.risk_guard import RiskGuard


class DCABotConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    buy_amount: Decimal  # Fixed dollar amount per buy (e.g. "25.0")
    buy_interval_bars: PositiveInt = 60  # Buy every N bars (e.g. 60 x 1h = ~2.5 days)
    rsi_period: PositiveInt = 14  # RSI period for optional filter
    rsi_overbought: float = 0.70  # Skip buying when RSI above this (NautilusTrader RSI is [0,1])
    use_rsi_filter: bool = True  # Enable/disable RSI filter
    take_profit_pct: float = 0.0  # 0 = disabled, e.g. 0.10 = 10% take profit
    stop_loss_pct: float = 0.0  # 0 = disabled, e.g. 0.05 = 5% stop loss
    # RSI-based exit (debate finding: DCA needs an exit strategy)
    rsi_exit_threshold: float = 0.80  # Sell when RSI exceeds this (NautilusTrader RSI is [0,1])
    use_rsi_exit: bool = True  # Enable RSI-based profit taking
    partial_exit_pct: float = 0.5  # Sell this fraction on RSI exit (keep rest for DCA)


class DCABotStrategy(RiskGuard, Strategy):
    """Dollar Cost Averaging bot — periodically buys a fixed dollar amount.

    Optionally filters buys using RSI (skip when overbought) and supports
    take-profit / stop-loss exits based on average entry price.
    """

    def __init__(self, config: DCABotConfig) -> None:
        super().__init__(config)
        self.instrument: Instrument | None = None
        self.rsi = RelativeStrengthIndex(config.rsi_period)
        self._bar_count: int = 0
        self._last_rsi_exit_bar: int = -999
        self._reset_tracking()

    def on_start(self) -> None:
        self.instrument = self.cache.instrument(self.config.instrument_id)
        if self.instrument is None:
            self.log.error(f"Could not find instrument for {self.config.instrument_id}")
            self.stop()
            return

        self.register_indicator_for_bars(self.config.bar_type, self.rsi)
        self.subscribe_bars(self.config.bar_type)
        self._bar_count = 0
        self._last_rsi_exit_bar = -999
        self._reset_tracking()

        # Portfolio-level risk guardrails
        self._risk_guard_init(
            starting_equity=float(self.config.buy_amount) * 20,  # rough estimate
            max_drawdown_pct=20.0,
            max_position_pct=0.50,
        )

    def on_bar(self, bar: Bar) -> None:
        if self._is_halted():
            return

        if not self.indicators_initialized():
            self.log.info(
                f"Warming up indicators [{self.cache.bar_count(self.config.bar_type)}]",
            )
            return

        self._bar_count += 1

        # Check exit conditions first (before potentially adding to position)
        self._check_exit_conditions(bar)

        # Execute periodic DCA buy
        if self._bar_count % self.config.buy_interval_bars == 0:
            # RSI filter: skip buying when overbought
            if self.config.use_rsi_filter and self.rsi.value > self.config.rsi_overbought:
                self.log.info(
                    f"Skipping DCA buy — RSI {self.rsi.value:.2f} > "
                    f"{self.config.rsi_overbought:.2f} (overbought)",
                )
                return

            self._execute_buy(bar)

    def _execute_buy(self, bar: Bar) -> None:
        """Buy a fixed dollar amount at current market price."""
        current_price = float(bar.close)
        if current_price <= 0:
            return

        raw_quantity = float(self.config.buy_amount) / current_price
        try:
            quantity = self.instrument.make_qty(Decimal(str(raw_quantity)))
        except ValueError:
            self.log.warning(
                f"Computed quantity {raw_quantity} rounds to zero for instrument",
            )
            return

        if quantity <= 0:
            self.log.warning(
                f"Computed quantity {raw_quantity} too small for instrument min size",
            )
            return

        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=OrderSide.BUY,
            quantity=quantity,
            time_in_force=TimeInForce.IOC,  # Binance Spot: market orders must use IOC/FOK, not GTC
        )
        self.submit_order(order)

        # Update average entry price tracking (use rounded quantity to match venue fills)
        buy_qty = Decimal(str(quantity))
        new_total_qty = self._total_quantity + buy_qty
        if new_total_qty > 0:
            self._avg_entry_price = (
                float(self._total_quantity) * self._avg_entry_price + float(buy_qty) * current_price
            ) / float(new_total_qty)
        self._total_quantity = new_total_qty
        self._total_invested += self.config.buy_amount

        self.log.info(
            f"DCA BUY: {quantity} @ {current_price:.2f} | "
            f"Avg entry: {self._avg_entry_price:.2f} | "
            f"Total invested: {self._total_invested}",
        )

    def _check_exit_conditions(self, bar: Bar) -> None:
        """Check take-profit and stop-loss conditions against average entry."""
        if self.portfolio.is_flat(self.config.instrument_id):
            return

        if self._avg_entry_price <= 0:
            return

        current_price = float(bar.close)

        # Take profit
        if self.config.take_profit_pct > 0:
            tp_price = self._avg_entry_price * (1.0 + self.config.take_profit_pct)
            if current_price >= tp_price:
                self.log.info(
                    f"TAKE PROFIT triggered: price {current_price:.2f} >= "
                    f"target {tp_price:.2f} ({self.config.take_profit_pct:.0%} above avg entry)",
                )
                self.close_all_positions(self.config.instrument_id)
                self._reset_tracking()
                return

        # RSI-based exit: partial sell when extremely overbought (cooldown prevents cascade)
        if (
            self.config.use_rsi_exit
            and self.rsi.value > self.config.rsi_exit_threshold
            and self._total_quantity > 0
            and (self._bar_count - self._last_rsi_exit_bar) >= self.config.buy_interval_bars
        ):
            raw_sell = self._total_quantity * Decimal(str(self.config.partial_exit_pct))
            try:
                sell_qty = self.instrument.make_qty(raw_sell)
            except ValueError:
                # Quantity too small for instrument precision -- skip partial exit
                sell_qty = None
            if sell_qty is not None and sell_qty > 0:
                self.log.info(
                    f"RSI EXIT: RSI {self.rsi.value:.2f} > {self.config.rsi_exit_threshold:.2f} | "
                    f"Selling {self.config.partial_exit_pct:.0%} of position ({sell_qty})",
                )
                order = self.order_factory.market(
                    instrument_id=self.config.instrument_id,
                    order_side=OrderSide.SELL,
                    quantity=sell_qty,
                    time_in_force=TimeInForce.IOC,  # Binance Spot: market orders must use IOC/FOK, not GTC
                )
                self.submit_order(order)
                self._total_quantity -= Decimal(str(sell_qty))
                self._last_rsi_exit_bar = self._bar_count
                return

        # Stop loss
        if self.config.stop_loss_pct > 0:
            sl_price = self._avg_entry_price * (1.0 - self.config.stop_loss_pct)
            if current_price <= sl_price:
                self.log.info(
                    f"STOP LOSS triggered: price {current_price:.2f} <= "
                    f"target {sl_price:.2f} ({self.config.stop_loss_pct:.0%} below avg entry)",
                )
                self.close_all_positions(self.config.instrument_id)
                self._reset_tracking()
                return

    def _reset_tracking(self) -> None:
        """Reset position tracking after a full exit."""
        self._avg_entry_price = 0.0
        self._total_quantity = Decimal("0")
        self._total_invested = Decimal("0")

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        self.close_all_positions(self.config.instrument_id)  # required by Round 11 contract
        self.unsubscribe_bars(self.config.bar_type)

    def on_reset(self) -> None:
        self.rsi.reset()
        self._bar_count = 0
        self._last_rsi_exit_bar = -999
        self._reset_tracking()
