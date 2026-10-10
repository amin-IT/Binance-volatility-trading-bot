"""Pause buying when Bitcoin looks bearish.

1. If TradingView recommends SELL (or STRONG_SELL) for BTC on all of the 1m, 30m and
   4h timeframes, buying is paused; if it recommends BUY on all three, buying is allowed.
2. Otherwise the BTC trend on 1 minute candles decides: buying is allowed only while
   the moving averages in TREND_SMAS are stacked from fastest to slowest.
"""

from __future__ import annotations

import logging

from tradingview_ta import Interval

from bvt import modkit
from bvt.indicators import sma
from bvt.signals import set_paused

NAME = "btc_pauseTV"

SYMBOL = "BTCUSDT"
INTERVALS = [Interval.INTERVAL_1_MINUTE, Interval.INTERVAL_30_MINUTES, Interval.INTERVAL_4_HOURS]
CANDLE_INTERVAL = "1m"
# Simple moving averages (in candles) that must be in falling order (fastest highest) for an uptrend.
# The original stack (2, 3, 4, 5, 10, 20, 50, 200) required the last five 1-minute closes to rise
# one after another, which is noise rather than trend and kept the bot paused most of the time.
TREND_SMAS = (20, 50, 200)
TIME_TO_WAIT = 1  # minutes between checks

log = logging.getLogger(NAME)

_SELL = {"SELL", "STRONG_SELL"}
_BUY = {"BUY", "STRONG_BUY"}


def trend_is_up(closes: list[float]) -> bool | None:
    """True if the SMAs are stacked fastest-highest, None if there are not enough candles."""
    values = [sma(closes, length)[-1] if len(closes) >= length else None for length in TREND_SMAS]
    if any(v is None for v in values):
        return None
    return all(faster > slower for faster, slower in zip(values, values[1:]))


def analyze(client) -> bool | None:
    """True to pause, False to allow buying, None if no data could be fetched."""
    recommendations = []
    for interval in INTERVALS:
        analysis = modkit.tradingview_analysis([SYMBOL], interval).get(SYMBOL)
        recommendations.append(analysis.summary["RECOMMENDATION"] if analysis else "UNKNOWN")
    if all(r in _SELL for r in recommendations):
        log.info("%s market not looking good, buying paused. Indicators: %s", SYMBOL, " ".join(recommendations))
        return True
    if all(r in _BUY for r in recommendations):
        log.info("%s market looks OK. Indicators: %s", SYMBOL, " ".join(recommendations))
        return False

    try:
        candles = modkit.klines(client, SYMBOL, CANDLE_INTERVAL, max(TREND_SMAS) + 10)
    except Exception as exc:  # noqa: BLE001 - network errors: keep the current state
        log.warning("Could not fetch %s candles: %s", SYMBOL, exc)
        return None
    up = trend_is_up([c.close for c in candles])
    if up is None:
        log.warning("Not enough %s candles for the trend check", SYMBOL)
        return None
    log.info(
        "%s indicators mixed (%s); moving-average trend is %s",
        SYMBOL,
        " ".join(recommendations),
        "up, buying allowed" if up else "not up, buying paused",
    )
    return not up


def do_work() -> None:
    client = modkit.public_client()
    while True:
        paused = analyze(client)
        if paused is not None:
            set_paused(NAME, paused)
        modkit.sleep_minutes(TIME_TO_WAIT)
