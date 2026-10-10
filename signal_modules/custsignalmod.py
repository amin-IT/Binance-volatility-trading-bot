"""Buy signals when selected TradingView oscillators and moving averages all say BUY.

Available indicators:
https://python-tradingview-ta.readthedocs.io/en/latest/usage.html#retrieving-the-analysis
"""

from __future__ import annotations

import logging

from tradingview_ta import Interval

from bvt import modkit
from bvt.signals import write_signals

NAME = "custsignalmod"

OSC_INDICATORS = ["MACD", "Stoch.RSI", "Mom"]  # oscillators to check
OSC_THRESHOLD = 2  # how many of OSC_INDICATORS must say BUY (at most len(OSC_INDICATORS))
MA_INDICATORS = ["EMA10", "EMA20"]  # moving averages to check
MA_THRESHOLD = 2  # how many of MA_INDICATORS must say BUY (at most len(MA_INDICATORS))
INTERVAL = Interval.INTERVAL_5_MINUTES  # timeframe of the analysis
TIME_TO_WAIT = 1  # minutes between analyses
FULL_LOG = False  # log the result for every coin

log = logging.getLogger(NAME)


def is_buy(analysis) -> tuple[bool, int, int]:
    osc = sum(analysis.oscillators["COMPUTE"].get(i) == "BUY" for i in OSC_INDICATORS)
    ma = sum(analysis.moving_averages["COMPUTE"].get(i) == "BUY" for i in MA_INDICATORS)
    return osc >= OSC_THRESHOLD and ma >= MA_THRESHOLD, osc, ma


def do_work() -> None:
    config = modkit.bot_config()
    while True:
        pairs = modkit.watchlist(config)
        signals = []
        for symbol, analysis in modkit.tradingview_analysis(pairs, INTERVAL).items():
            buy, osc, ma = is_buy(analysis)
            if FULL_LOG:
                log.info(
                    "%s oscillators %d/%d, moving averages %d/%d",
                    symbol,
                    osc,
                    len(OSC_INDICATORS),
                    ma,
                    len(MA_INDICATORS),
                )
            if buy:
                signals.append(symbol)
                log.info(
                    "Signal on %s (%d/%d oscillators, %d/%d moving averages)",
                    symbol,
                    osc,
                    len(OSC_INDICATORS),
                    ma,
                    len(MA_INDICATORS),
                )
        write_signals(NAME, signals)
        log.info("%d of %d coins passed; next analysis in %s minute(s)", len(signals), len(pairs), TIME_TO_WAIT)
        modkit.sleep_minutes(TIME_TO_WAIT)
