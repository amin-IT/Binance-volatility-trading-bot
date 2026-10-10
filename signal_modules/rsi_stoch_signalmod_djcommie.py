"""Buy signals on rising RSI confirmed by Stochastic (by @DJcommie).

A coin is signalled when, on INTERVAL, its RSI rose by at least RSI_RISE since the
previous check, RSI and Stoch.RSI %K are inside their ranges, %K leads %D by
STOCH_KD_GAP, and the selected TradingView oscillators / moving averages agree.
"""

from __future__ import annotations

import logging

from tradingview_ta import Interval

from bvt import modkit
from bvt.signals import write_signals

NAME = "djcommie_custsignalmod"

OSC_INDICATORS = ["RSI", "Stoch.RSI"]  # oscillators that must not say SELL
OSC_THRESHOLD = 2
MA_INDICATORS = ["EMA10", "EMA20"]  # moving averages that must say BUY
MA_THRESHOLD = 2
INTERVAL = Interval.INTERVAL_5_MINUTES
RSI_RISE = 2.5  # minimum RSI increase since the previous check
RSI_RANGE = (49, 67)
STOCH_RSI_K_RANGE = (25, 58)
STOCH_KD_GAP = 4.5  # minimum Stoch %K - %D
# The original code meant to require EMA10 > EMA20 > EMA100 but the check was
# accidentally always true; it is off by default to keep the original behaviour.
REQUIRE_EMA_TREND = False
TIME_TO_WAIT = 1  # minutes between analyses

log = logging.getLogger(NAME)


def is_buy(analysis, last_rsi: float | None) -> bool:
    ind = analysis.indicators
    try:
        rsi, stoch_rsi_k = float(ind["RSI"]), float(ind["Stoch.RSI.K"])
        stoch_k, stoch_d = float(ind["Stoch.K"]), float(ind["Stoch.D"])
        ema10, ema20, ema100 = float(ind["EMA10"]), float(ind["EMA20"]), float(ind["EMA100"])
    except (KeyError, TypeError, ValueError):
        return False
    if last_rsi is None or rsi - last_rsi < RSI_RISE:
        return False
    if not (RSI_RANGE[0] <= rsi <= RSI_RANGE[1] and STOCH_RSI_K_RANGE[0] <= stoch_rsi_k <= STOCH_RSI_K_RANGE[1]):
        return False
    if stoch_k - stoch_d < STOCH_KD_GAP:
        return False
    if REQUIRE_EMA_TREND and not (ema10 > ema20 > ema100):
        return False
    osc = sum(analysis.oscillators["COMPUTE"].get(i, "SELL") != "SELL" for i in OSC_INDICATORS)
    ma = sum(analysis.moving_averages["COMPUTE"].get(i) == "BUY" for i in MA_INDICATORS)
    return osc >= OSC_THRESHOLD and ma >= MA_THRESHOLD


def do_work() -> None:
    config = modkit.bot_config()
    last_rsi: dict[str, float] = {}
    while True:
        signals = []
        for symbol, analysis in modkit.tradingview_analysis(modkit.watchlist(config), INTERVAL).items():
            if is_buy(analysis, last_rsi.get(symbol)):
                signals.append(symbol)
                log.info("Signal on %s", symbol)
            rsi = analysis.indicators.get("RSI")
            if rsi is not None:
                last_rsi[symbol] = float(rsi)
        write_signals(NAME, signals)
        modkit.sleep_minutes(TIME_TO_WAIT)
