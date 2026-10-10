"""Pause buying when the moving averages of BTC or ETH lean SELL on TradingView."""

from __future__ import annotations

import logging

from tradingview_ta import Interval

from bvt import modkit
from bvt.signals import set_paused

NAME = "os_pausebot_MAv2"

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
INTERVAL = Interval.INTERVAL_5_MINUTES  # timeframe of the analysis
TIME_TO_WAIT = 5  # minutes between checks
DEBUG = True  # log the indicator counts of every symbol

log = logging.getLogger(NAME)


def bearish(analysis) -> bool:
    return analysis.moving_averages["SELL"] >= analysis.moving_averages["BUY"]


def do_work() -> None:
    while True:
        analyses = modkit.tradingview_analysis(SYMBOLS, INTERVAL)
        missing = [s for s in SYMBOLS if s not in analyses]
        if missing:
            log.warning("No TradingView analysis for %s, keeping the current state", ", ".join(missing))
        else:
            for symbol, analysis in analyses.items():
                if DEBUG:
                    ma = analysis.moving_averages
                    log.info("%s - SELL: %d | BUY: %d | NEUTRAL: %d", symbol, ma["SELL"], ma["BUY"], ma["NEUTRAL"])
            paused = any(bearish(a) for a in analyses.values())
            set_paused(NAME, paused)
            if paused:
                log.info("Market not looking good, buying paused. Next check in %s minutes", TIME_TO_WAIT)
            else:
                log.info("Market looks OK. Next check in %s minutes", TIME_TO_WAIT)
        modkit.sleep_minutes(TIME_TO_WAIT)
