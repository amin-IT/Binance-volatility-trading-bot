"""Buy signals when TradingView's summary shows enough BUY indicators on two timeframes."""

from __future__ import annotations

import logging

from tradingview_ta import Interval

from bvt import modkit
from bvt.signals import write_signals

NAME = "signalsample"

FIRST_INTERVAL = Interval.INTERVAL_1_MINUTE
SECOND_INTERVAL = Interval.INTERVAL_5_MINUTES
# How many of TradingView's 26 summary indicators must say BUY on both timeframes.
# (The previous default of 26 required every indicator to agree and practically never fired.)
TA_BUY_THRESHOLD = 15
TIME_TO_WAIT = 1  # minutes between analyses
FULL_LOG = False  # log the result for every coin

log = logging.getLogger(NAME)


def do_work() -> None:
    config = modkit.bot_config()
    while True:
        pairs = modkit.watchlist(config)
        first = modkit.tradingview_analysis(pairs, FIRST_INTERVAL)
        second = modkit.tradingview_analysis(pairs, SECOND_INTERVAL)
        signals = []
        best = ("none", 0)
        for symbol in pairs:
            if symbol not in first or symbol not in second:
                continue
            first_buys, second_buys = first[symbol].summary["BUY"], second[symbol].summary["BUY"]
            if FULL_LOG:
                log.info("%s first %d second %d", symbol, first_buys, second_buys)
            if first_buys > best[1]:
                best = (symbol, first_buys)
            if first_buys >= TA_BUY_THRESHOLD and second_buys >= TA_BUY_THRESHOLD:
                signals.append(symbol)
                log.info("Signal on %s (%d and %d BUY indicators)", symbol, first_buys, second_buys)
        write_signals(NAME, signals)
        log.info(
            "%d coins at %d+ BUY indicators on both timeframes (strongest: %s with %d); next analysis in %s minute(s)",
            len(signals),
            TA_BUY_THRESHOLD,
            best[0],
            best[1],
            TIME_TO_WAIT,
        )
        modkit.sleep_minutes(TIME_TO_WAIT)
