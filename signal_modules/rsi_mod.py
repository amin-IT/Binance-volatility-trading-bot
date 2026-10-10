"""RSI / Stochastic momentum signals (NigeC v1.01, credit to @DJcommie and @Firewatch).

Buys coins whose RSI and Stochastic are rising and within the configured ranges, and
(with SELL_COINS) emits sell signals for held coins whose momentum turns down.
Test before using real funds. Default values suggested by the original author are in
brackets.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tradingview_ta import Interval

from bvt import modkit
from bvt.signals import write_signals

NAME = "rsi_mod"

INTERVAL = Interval.INTERVAL_1_DAY  # main timeframe for oscillators and moving averages (15 mins)
INTERVAL2 = Interval.INTERVAL_1_MINUTE  # secondary timeframe to check the entry point (5 mins)

OSC_INDICATORS = ["RSI", "STOCH.K", "CCI", "ADX", "AO", "W%R", "Stoch.RSI", "Mom", "MACD", "UO", "BBP"]
OSC_THRESHOLD = 0  # oscillators that must not say SELL (5)
MA_INDICATORS = [
    "EMA10", "EMA20", "SMA10", "SMA20", "EMA30", "SMA30", "EMA50", "SMA50",
    "EMA100", "SMA100", "EMA200", "SMA200", "Ichimoku", "VWMA", "HullMA",
]  # fmt: skip
MA_THRESHOLD = 0  # moving averages that must say BUY (3)
MA_SUMMARY = 0  # BUY indicators out of 26 needed on INTERVAL (13)
MA_SUMMARY2 = 0  # BUY indicators out of 26 needed on INTERVAL2 (13)

RSI_MIN = 1  # minimum RSI for a buy; under 25 is considered oversold (12)
RSI_MAX = 99  # maximum RSI for a buy; over 80 is considered overbought (55)
STOCH_MIN = 0  # minimum Stoch %K and %D for a buy (12)
STOCH_MAX = 99  # maximum Stoch %K and %D for a buy (99)
RSI_BUY = 0.3  # minimum RSI rise since the previous candle for a buy (-0.3)
STOCH_BUY = 10  # minimum %K - %D difference for a buy (10)

SELL_COINS = True  # emit sell signals on bearish momentum (False)
RSI_SELL = -5  # RSI change since the previous candle below which to sell (-5)
STOCH_SELL = -10  # %K - %D difference below which to sell (-10)
SIGNALS_SELL = 7  # sell only if both timeframes have fewer BUY indicators than this (7)

TIME_TO_WAIT = 4  # minutes between analyses
FULL_LOG = False  # log the values of every coin that looks promising

log = logging.getLogger(NAME)


@dataclass(frozen=True)
class Reading:
    rsi: float
    rsi_diff: float
    stoch_k: float
    stoch_k1: float
    stoch_d: float
    stoch_diff: float
    buys: int
    buys2: int
    osc_ok: int
    ma_ok: int


def read(analysis, analysis2) -> Reading | None:
    try:
        ind = analysis.indicators
        rsi, rsi1 = float(ind["RSI"]), float(ind["RSI[1]"])
        stoch_k, stoch_d, stoch_k1 = float(ind["Stoch.K"]), float(ind["Stoch.D"]), float(ind["Stoch.K[1]"])
    except (KeyError, TypeError, ValueError):
        return None
    osc_ok = sum(analysis.oscillators["COMPUTE"].get(i, "SELL") != "SELL" for i in OSC_INDICATORS)
    ma_ok = sum(analysis.moving_averages["COMPUTE"].get(i) == "BUY" for i in MA_INDICATORS)
    return Reading(
        rsi, rsi - rsi1, stoch_k, stoch_k1, stoch_d, stoch_k - stoch_d,
        analysis.summary["BUY"], analysis2.summary["BUY"], osc_ok, ma_ok,
    )  # fmt: skip


def is_buy(r: Reading) -> bool:
    return (
        RSI_MIN <= r.rsi <= RSI_MAX
        and r.rsi_diff >= RSI_BUY
        and r.stoch_diff >= STOCH_BUY
        and STOCH_MIN <= r.stoch_k <= STOCH_MAX
        and STOCH_MIN <= r.stoch_d <= STOCH_MAX
        and r.buys >= MA_SUMMARY
        and r.buys2 >= MA_SUMMARY2
        and r.stoch_k > r.stoch_k1
        and r.osc_ok >= OSC_THRESHOLD
        and r.ma_ok >= MA_THRESHOLD
    )


def is_sell(r: Reading) -> bool:
    return (
        r.buys < SIGNALS_SELL
        and r.buys2 < SIGNALS_SELL
        and r.stoch_diff < STOCH_SELL
        and r.rsi_diff < RSI_SELL
        and r.stoch_k < r.stoch_k1
    )


def do_work() -> None:
    config = modkit.bot_config()
    while True:
        pairs = modkit.watchlist(config)
        main = modkit.tradingview_analysis(pairs, INTERVAL)
        entry = modkit.tradingview_analysis(pairs, INTERVAL2)
        buys, sells = [], []
        for symbol in pairs:
            if symbol not in main or symbol not in entry:
                continue
            r = read(main[symbol], entry[symbol])
            if r is None:
                continue
            if FULL_LOG and r.rsi < 80 and r.buys >= 10 and r.stoch_diff > 0 and r.rsi_diff > 0:
                log.info(
                    "%s RSI %.2f (%+.2f) Stoch K/D %.2f/%.2f BUYS %d_%d/26",
                    symbol,
                    r.rsi,
                    r.rsi_diff,
                    r.stoch_k,
                    r.stoch_d,
                    r.buys,
                    r.buys2,
                )
            if is_buy(r):
                buys.append(symbol)
                log.info("Buy signal on %s | %d_%d/26", symbol, r.buys, r.buys2)
            if SELL_COINS and is_sell(r):
                sells.append(symbol)
                log.info("Sell signal on %s | %d_%d/26", symbol, r.buys, r.buys2)
        write_signals(NAME, buys)
        write_signals(NAME, sells, kind="sell")
        log.info("%d coins with buy signals; next analysis in %s minutes", len(buys), TIME_TO_WAIT)
        modkit.sleep_minutes(TIME_TO_WAIT)
