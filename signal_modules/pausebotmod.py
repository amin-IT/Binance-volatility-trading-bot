"""Pause buying while TradingView's moving averages for BTC are bearish.

Stop loss and take profit keep working while the bot is paused.
"""

from __future__ import annotations

import logging

from tradingview_ta import Interval

from bvt import modkit
from bvt.signals import set_paused

NAME = "pausebotmod"

SYMBOL = "BTCUSDT"
INTERVAL = Interval.INTERVAL_1_MINUTE  # timeframe of the analysis
THRESHOLD = 14  # pause when at least this many of the 15 moving averages say SELL
TIME_TO_WAIT = 1  # minutes between checks

log = logging.getLogger(NAME)


def should_pause(analysis) -> bool:
    return analysis.moving_averages["SELL"] >= THRESHOLD


def do_work() -> None:
    while True:
        analysis = modkit.tradingview_analysis([SYMBOL], INTERVAL).get(SYMBOL)
        if analysis is None:
            log.warning("No TradingView analysis for %s, keeping the current state", SYMBOL)
        else:
            sells = analysis.moving_averages["SELL"]
            paused = should_pause(analysis)
            set_paused(NAME, paused)
            state = "market not looking good, buying paused" if paused else "market looks OK"
            log.info("%s %s (%d/%d MAs say SELL)", SYMBOL, state, sells, THRESHOLD)
        modkit.sleep_minutes(TIME_TO_WAIT)
