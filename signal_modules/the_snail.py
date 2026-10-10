"""The Snail: "buy the dips! ... then wait".

Merges The Snail v2.2 (by scoobie) and v2.3 (RSI ranking and TradingView filter by
@ashwinprasad.me); thanks to @vyacheslav, @Kevin.Butters and @OlorinSledge.

Strategy
1. Select coins trading at least PROFIT_MIN % below their high of the last LIMIT days,
   and below ``high - range * PERCENT_BELOW``.
2. Optionally require enough price movement to reach TAKE_PROFIT:
   * "MOVEMENT": the LIMIT-day range as % of the low, compared with TAKE_PROFIT
   * "ATR_MOVEMENT": the Average True Range as % of price (ATRP), compared with TAKE_PROFIT
3. Skip coins the bot already holds.
4. Require MACD histograms >= 0 on MACD_TIMEFRAMES (coin turning up) and on BTC 1m.
5. Optionally drop coins TradingView rates SELL on any of TRADINGVIEW_INTERVALS.
6. Signal the remaining coins, best first (by potential, or RSI-weighted with RSI_RANKING).

Recommended config.yml: CHANGE_IN_PRICE high (e.g. 100) so the bot only buys snail
signals, and no pause module, since the snail buys dips on purpose.
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass

from tradingview_ta import Interval

from bvt import modkit
from bvt.indicators import macd, rsi
from bvt.signals import write_signals

NAME = "snail_scan"

# --- strategy
LIMIT = 4  # candles of history (days with INTERVAL = '1d')
INTERVAL = "1d"
PROFIT_MIN = 15  # minimum potential profit / drop in %
PROFIT_MAX = 100  # maximum potential profit / drop in %
PERCENT_BELOW = 0.6  # risk: 0.7 = buy below 70% of the range under the high, 0.5 = 50%
MOVEMENT = "ATR_MOVEMENT"  # "MOVEMENT", "ATR_MOVEMENT" or "" to disable the movement check
DROP_CALCULATION = True  # potential = drop from the high (True) or rise needed back to the high (False)
MACD_TIMEFRAMES = ["1m", "5m", "15m", "4h"]  # v2.2 used ["1m", "5m", "15m", "1d"]
MACD_BTC_FILTER = True  # also require BTC's 1m MACD histogram >= 0
RSI_RANKING = True  # rank by a mix of 1h RSI (75%) and potential (25%) instead of potential alone
TRADINGVIEW_FILTER = True  # skip coins TradingView rates SELL / STRONG_SELL
TRADINGVIEW_INTERVALS = [
    Interval.INTERVAL_1_MINUTE,
    Interval.INTERVAL_5_MINUTES,
    Interval.INTERVAL_15_MINUTES,
    Interval.INTERVAL_4_HOURS,
]

# --- other
TIME_TO_WAIT = 3  # minutes between scans
ALL_INFO = False  # log details for every coin

MACD_CANDLES = 36
log = logging.getLogger("the_snail")


@dataclass
class Candidate:
    symbol: str
    last_price: float
    high: float
    low: float
    potential: float
    movement: float
    atr_percentage: float
    rank: float = 0.0


def evaluate(symbol: str, candles: list[modkit.Candle], last_price: float, take_profit: float) -> Candidate | None:
    """Apply the dip and movement rules (steps 1 and 2) to one coin."""
    if len(candles) < LIMIT or last_price <= 0:
        return None
    high = max(c.high for c in candles)
    low = min(c.low for c in candles)
    price_range = high - low
    if price_range <= 0 or low <= 0:
        return None
    buy_below = high - price_range * PERCENT_BELOW
    current_potential = high / last_price * 100 - 100
    current_drop = (high - last_price) / high * 100
    # How far the coin swings over the period. (v2.x computed low / range here, which
    # grows as the movement shrinks - the opposite of what the setting describes.)
    movement = price_range / low * 100
    atr_percentage = statistics.fmean(c.high - c.low for c in candles) / candles[-1].close * 100
    potential = current_drop if DROP_CALCULATION else current_potential

    if ALL_INFO:
        log.info(
            "%s potential %.2f%% drop %.2f%% movement %.2f%% ATRP %.2f%%",
            symbol,
            current_potential,
            current_drop,
            movement,
            atr_percentage,
        )
    if not (PROFIT_MIN < potential < PROFIT_MAX and last_price < buy_below):
        return None
    if MOVEMENT == "MOVEMENT" and movement < take_profit + 0.2:
        return None
    if MOVEMENT == "ATR_MOVEMENT" and atr_percentage < take_profit:
        return None
    return Candidate(symbol, last_price, high, low, potential, movement, atr_percentage)


def macd_turning_up(candles: list[modkit.Candle]) -> bool:
    if len(candles) < MACD_CANDLES:
        return False
    histogram = macd([c.close for c in candles])[2][-1]
    return histogram is not None and histogram >= 0


def tradingview_rejects(symbols: list[str]) -> set[str]:
    rejected = set()
    for interval in TRADINGVIEW_INTERVALS:
        for symbol, analysis in modkit.tradingview_analysis(symbols, interval).items():
            if analysis.summary["RECOMMENDATION"] in ("SELL", "STRONG_SELL"):
                log.info("%s rated %s on %s by TradingView", symbol, analysis.summary["RECOMMENDATION"], interval)
                rejected.add(symbol)
    return rejected


def scan(client, config) -> list[Candidate]:
    pairs = set(modkit.watchlist(config))
    prices = {t["symbol"]: float(t["price"]) for t in client.get_all_tickers() if t["symbol"] in pairs}
    history = modkit.klines_many(client, prices, INTERVAL, LIMIT)
    held = modkit.held_symbols(config)
    candidates = [
        candidate
        for symbol, candles in history.items()
        if symbol not in held and (candidate := evaluate(symbol, candles, prices[symbol], config.take_profit))
    ]
    if not candidates:
        log.info("Found no coins %s%%+ below their %d %s high", PROFIT_MIN, LIMIT, INTERVAL)
        return []

    if MACD_BTC_FILTER and not macd_turning_up(modkit.klines(client, "BTCUSDT", "1m", MACD_CANDLES)):
        log.info("Found %d coins, but BTC's MACD is negative: no signals", len(candidates))
        return []
    approved = []
    for candidate in candidates:
        if all(
            macd_turning_up(modkit.klines(client, candidate.symbol, timeframe, MACD_CANDLES))
            for timeframe in MACD_TIMEFRAMES
        ):
            approved.append(candidate)
    if approved and TRADINGVIEW_FILTER:
        rejected = tradingview_rejects([c.symbol for c in approved])
        approved = [c for c in approved if c.symbol not in rejected]

    if RSI_RANKING:
        for candidate in approved:
            value = rsi([c.close for c in modkit.klines(client, candidate.symbol, "1h", MACD_CANDLES)])[-1]
            candidate.rank = (100 - candidate.potential) * 0.25 + (value if value is not None else 50) * 0.75
        approved.sort(key=lambda c: c.rank)
    else:
        approved.sort(key=lambda c: c.potential, reverse=True)

    log.info(
        "Found %d coins and MACD approved %d. L: %d %s, min %s%%, risk %.0f%%",
        len(candidates),
        len(approved),
        LIMIT,
        INTERVAL,
        PROFIT_MIN,
        PERCENT_BELOW * 100,
    )
    return approved


def do_work() -> None:
    config = modkit.bot_config()
    client = modkit.public_client(config)
    while True:
        try:
            picked = scan(client, config)
        except Exception as exc:  # noqa: BLE001 - network errors: retry sooner
            log.warning("Scan failed: %s", exc)
            time.sleep(60)
            continue
        write_signals(NAME, [c.symbol for c in picked])
        for candidate in picked:
            log.info(
                "Signal on %s: price %s, high %s, potential %.0f%%",
                candidate.symbol,
                candidate.last_price,
                candidate.high,
                candidate.potential,
            )
        modkit.sleep_minutes(TIME_TO_WAIT)
