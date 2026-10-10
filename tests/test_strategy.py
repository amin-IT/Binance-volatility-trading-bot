from datetime import datetime, timedelta

import pytest

from bvt.config import Config
from bvt.portfolio import Position
from bvt.strategy import HOLD, SELL, TRAIL, PriceWindow, evaluate_exit, net_change, trade_profit

T0 = datetime(2026, 1, 1)


def window_with(*snapshots, size=6):
    window = PriceWindow(size)
    for i, prices in enumerate(snapshots):
        window.add(T0 + timedelta(seconds=10 * i), prices)
    return window


def test_momentum_is_measured_from_the_low_to_the_current_price():
    window = window_with({"A": 100}, {"A": 98}, {"A": 102.9})
    assert window.momentum()["A"] == pytest.approx(5.0)


def test_spike_that_already_reversed_has_no_momentum():
    # the old low-to-high measure reported +5% here and bought the top of a reversed spike
    window = window_with({"A": 100}, {"A": 105}, {"A": 100})
    assert window.momentum()["A"] == pytest.approx(0.0)


def test_momentum_needs_two_samples_and_skips_missing_coins():
    window = window_with({"A": 100, "B": 50}, {"A": 101})
    assert set(window.momentum()) == {"A"}
    assert window_with({"A": 100}).momentum() == {}


def test_window_keeps_only_the_last_snapshots():
    window = window_with({"A": 50}, {"A": 100}, {"A": 100}, {"A": 103}, size=3)
    assert window.momentum()["A"] == pytest.approx(3.0)
    window.resize(2)
    assert len(window) == 2


def test_market_breadth():
    window = window_with({"A": 1, "B": 1, "C": 1}, {"A": 2, "B": 0.5, "C": 1})
    assert window.market_breadth() == (1, 1, 1)


def position(**overrides):
    values = dict(symbol="AUSDT", orderid=1, timestamp=0, bought_at=100.0, volume=1.0, take_profit=2.0, stop_loss=-3.0)
    values.update(overrides)
    return Position(**values)


TRAILING = Config(take_profit=2.0, stop_loss=-3.0, trailing_stop_loss=0.5, trailing_take_profit=0.1)
FIXED = Config(take_profit=2.0, stop_loss=-3.0, use_trailing_stop_loss=False)


def test_fixed_take_profit_and_stop_loss():
    assert evaluate_exit(position(), 102.5, FIXED).action == SELL
    assert evaluate_exit(position(), 102.5, FIXED).reason == "take profit"
    assert evaluate_exit(position(), 96.5, FIXED).reason == "stop loss"
    assert evaluate_exit(position(), 101, FIXED).action == HOLD


def test_trailing_stop_arms_at_take_profit_and_only_moves_up():
    pos = position()
    decision = evaluate_exit(pos, 103, TRAILING)
    assert decision.action == TRAIL
    assert decision.take_profit == pytest.approx(3.1)
    assert decision.stop_loss == pytest.approx(2.6)

    pos.trailing, pos.take_profit, pos.stop_loss = True, decision.take_profit, decision.stop_loss
    assert evaluate_exit(pos, 102.8, TRAILING).action == HOLD
    sell = evaluate_exit(pos, 102.5, TRAILING)
    assert sell.action == SELL and sell.reason == "trailing stop loss"

    # a later higher take profit never lowers the stop
    pos.take_profit, pos.stop_loss = 1.0, 2.6
    decision = evaluate_exit(pos, 102.7, TRAILING)
    assert decision.action == TRAIL
    assert decision.stop_loss == pytest.approx(2.6)


def test_untrailed_positions_follow_config_edits():
    pos = position(take_profit=10.0, stop_loss=-10.0)
    decision = evaluate_exit(pos, 96.5, TRAILING)  # config stop loss is -3
    assert decision.action == SELL
    assert decision.reason == "stop loss"


def test_fees_and_profit():
    assert net_change(1.0, 0.075) == pytest.approx(0.85)
    # buy 1 at 100, sell at 110, 0.1% fee on both legs
    assert trade_profit(100, 110, 1, 0.1) == pytest.approx(10 - 0.21)
