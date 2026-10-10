"""Buy / sell signals from TradingView recommendations on three timeframes (by @Firewatch).

Buy when the recommendation is BUY or STRONG_BUY on all of INTERVALS and RSI is at
most RSI_MAX on each; sell held coins when it is SELL or STRONG_SELL on all of them.
"""

from __future__ import annotations

import logging

from tradingview_ta import Interval

from bvt import modkit
from bvt.signals import write_signals

NAME = "firewatch_signalsample"

INTERVALS = [Interval.INTERVAL_1_MINUTE, Interval.INTERVAL_5_MINUTES, Interval.INTERVAL_15_MINUTES]
RSI_MAX = 67  # skip overbought coins
SELL_COINS = True  # emit sell signals
TIME_TO_WAIT = 1  # minutes between analyses
FULL_LOG = False  # log the recommendations of every coin

log = logging.getLogger(NAME)


def classify(analyses: list) -> str | None:
    """'buy', 'sell' or None for one coin's analyses on all INTERVALS."""
    recommendations = [a.summary["RECOMMENDATION"] for a in analyses]
    if all(r in ("BUY", "STRONG_BUY") for r in recommendations):
        try:
            if all(float(a.indicators["RSI"]) <= RSI_MAX for a in analyses):
                return "buy"
        except (KeyError, TypeError, ValueError):
            return None
    if all(r in ("SELL", "STRONG_SELL") for r in recommendations):
        return "sell"
    return None


def do_work() -> None:
    config = modkit.bot_config()
    while True:
        pairs = modkit.watchlist(config)
        per_interval = [modkit.tradingview_analysis(pairs, interval) for interval in INTERVALS]
        buys, sells = [], []
        for symbol in pairs:
            analyses = [result.get(symbol) for result in per_interval]
            if any(a is None for a in analyses):
                continue
            if FULL_LOG:
                log.info("%s %s", symbol, " ".join(a.summary["RECOMMENDATION"] for a in analyses))
            verdict = classify(analyses)
            if verdict == "buy":
                buys.append(symbol)
                log.info("Buy signal on %s", symbol)
            elif verdict == "sell" and SELL_COINS:
                sells.append(symbol)
        write_signals(NAME, buys)
        write_signals(NAME, sells, kind="sell")
        log.info("%d buy and %d sell signals; next analysis in %s minute(s)", len(buys), len(sells), TIME_TO_WAIT)
        modkit.sleep_minutes(TIME_TO_WAIT)
