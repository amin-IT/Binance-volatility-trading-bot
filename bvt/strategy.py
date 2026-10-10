"""Pure trading logic: volatility detection and take profit / stop loss decisions."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from bvt.config import Config
from bvt.portfolio import Position


class PriceWindow:
    """Rolling window of the last ``size`` price snapshots (one per tick).

    With RECHECK_INTERVAL ticks per TIME_DIFFERENCE minutes the window spans
    TIME_DIFFERENCE minutes.
    """

    def __init__(self, size: int) -> None:
        self._snapshots: deque[tuple[datetime, dict[str, float]]] = deque(maxlen=max(1, size))

    def resize(self, size: int) -> None:
        if size != self._snapshots.maxlen:
            self._snapshots = deque(self._snapshots, maxlen=max(1, size))

    def add(self, at: datetime, prices: dict[str, float]) -> None:
        self._snapshots.append((at, prices))

    def __len__(self) -> int:
        return len(self._snapshots)

    @property
    def latest(self) -> dict[str, float]:
        return self._snapshots[-1][1] if self._snapshots else {}

    def momentum(self) -> dict[str, float]:
        """Percentage rise of each coin from its lowest price in the window to its current price.

        Earlier versions measured the window's low-to-high move, which also flagged
        coins whose spike had already reversed; measuring up to the current price
        only flags coins that are still up when the bot would buy them.
        """
        result: dict[str, float] = {}
        for symbol, current in self.latest.items():
            prices = [p for _, snapshot in self._snapshots if (p := snapshot.get(symbol)) is not None and p > 0]
            if len(prices) < 2 or current <= 0:
                continue
            low = min(prices)
            result[symbol] = (current - low) / low * 100
        return result

    def market_breadth(self) -> tuple[int, int, int]:
        """Number of coins (up, down, unchanged) between the oldest and latest snapshot."""
        if not self._snapshots:
            return 0, 0, 0
        first, latest = self._snapshots[0][1], self.latest
        up = down = unchanged = 0
        for symbol, price in latest.items():
            start = first.get(symbol)
            if start is None or price == start:
                unchanged += 1
            elif price > start:
                up += 1
            else:
                down += 1
        return up, down, unchanged


HOLD = "hold"
SELL = "sell"
TRAIL = "trail"


@dataclass(frozen=True)
class ExitDecision:
    action: str
    change: float  # % price change since the buy, before fees
    take_profit: float
    stop_loss: float
    reason: str = ""


def price_change(bought_at: float, price: float) -> float:
    return (price - bought_at) / bought_at * 100


def evaluate_exit(position: Position, price: float, config: Config) -> ExitDecision:
    """Decide whether to hold, sell, or move the trailing stop for a position.

    Until the trailing stop is armed a position follows the current TAKE_PROFIT and
    STOP_LOSS from the config (so editing config.yml affects open positions). Once
    armed, the stop only ever moves up.
    """
    change = price_change(position.bought_at, price)
    if position.trailing:
        take_profit, stop_loss = position.take_profit, position.stop_loss
    else:
        take_profit, stop_loss = config.take_profit, config.stop_loss

    if config.use_trailing_stop_loss:
        if change > take_profit:
            new_take_profit = change + config.trailing_take_profit
            new_stop_loss = max(stop_loss, new_take_profit - config.trailing_stop_loss)
            return ExitDecision(TRAIL, change, new_take_profit, new_stop_loss)
        if change < stop_loss:
            reason = "trailing stop loss" if position.trailing else "stop loss"
            return ExitDecision(SELL, change, take_profit, stop_loss, reason)
        return ExitDecision(HOLD, change, take_profit, stop_loss)

    if change > take_profit:
        return ExitDecision(SELL, change, take_profit, stop_loss, "take profit")
    if change < stop_loss:
        return ExitDecision(SELL, change, take_profit, stop_loss, "stop loss")
    return ExitDecision(HOLD, change, take_profit, stop_loss)


def net_change(change: float, trading_fee: float) -> float:
    """Price change after paying the trading fee on both the buy and the sell."""
    return change - 2 * trading_fee


def trade_profit(bought_at: float, sold_at: float, quantity: float, trading_fee: float) -> float:
    """Profit in the quote currency after fees on both legs."""
    gross = (sold_at - bought_at) * quantity
    fees = (bought_at + sold_at) * quantity * trading_fee / 100
    return gross - fees
