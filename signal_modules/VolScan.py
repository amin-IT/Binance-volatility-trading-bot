"""VolScan: buy signals from price dips and volatility.

Every SCANNING_PERIOD minutes VolScan records the price of every coin in the bot's
tickers list every TIME_SLEEP seconds, then ranks the coins either by

* the aggregate price change over the period (default): coins that fell, biggest drop first, or
* the Coefficient of Variation (CoV, standard deviation / mean): the most volatile first
  (https://www.investopedia.com/terms/c/coefficientofvariation.asp)

and writes the top NUMBER_COINS_IN_LIST to ``signals/vol_scan.exs`` for the bot to buy.
VolScan provides all the buy signals the bot needs, so other buy-signal modules can be disabled.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bvt import modkit
from bvt.indicators import coefficient_of_variation, cumulative_change
from bvt.signals import write_signals

NAME = "vol_scan"

SCANNING_PERIOD = 4  # minutes in one scanning period
TIME_SLEEP = 30  # seconds between price scans within a period
NUMBER_COINS_IN_LIST = 15  # maximum number of coins signalled per period

# Only coins with a CoV above this value are listed when one of the CoV modes below is on.
CoV_INDEX = 0.0
# True: list coins that fell AND have CoV >= CoV_INDEX.
CREATE_LIST_BY_COV_AND_PRICE_CHANGE = False
# True: list coins by CoV only (most volatile first), ignoring the price change.
CREATE_LIST_BY_ONLY_COV = False

# Only signal a coin that fell if its latest price is above its lowest price of the
# period, i.e. it has started to bounce. Buying while a coin is still making new lows
# ("catching a falling knife") is the classic failure of dip buying. False restores
# the original behaviour.
REQUIRE_REBOUND = True

log = logging.getLogger("VolScan")


@dataclass(frozen=True)
class CoinScore:
    symbol: str
    change: float  # aggregate % change over the period
    cov: float


def rank_coins(history: dict[str, list[float]]) -> list[CoinScore]:
    """Pick and order the coins to signal from each coin's prices over the period."""
    scores = []
    for symbol, prices in history.items():
        if len(prices) < 2:
            continue
        change = cumulative_change(prices)
        cov = coefficient_of_variation(prices)
        if CREATE_LIST_BY_ONLY_COV:
            selected = cov >= CoV_INDEX
        else:
            selected = change < 0
            if CREATE_LIST_BY_COV_AND_PRICE_CHANGE:
                selected = selected and cov >= CoV_INDEX
            if REQUIRE_REBOUND:
                selected = selected and prices[-1] > min(prices)
        if selected:
            scores.append(CoinScore(symbol, round(change, 4), round(cov, 2)))
    if CREATE_LIST_BY_ONLY_COV:
        scores.sort(key=lambda s: s.cov, reverse=True)
    else:
        scores.sort(key=lambda s: s.change)
    return scores[:NUMBER_COINS_IN_LIST]


def do_work() -> None:
    config = modkit.bot_config()
    client = modkit.public_client(config)
    scans_per_period = max(2, round(SCANNING_PERIOD * 60 / TIME_SLEEP))
    while True:
        symbols = set(modkit.watchlist(config))
        log.info("Scanning %d coins over %s minutes", len(symbols), SCANNING_PERIOD)
        history: dict[str, list[float]] = {}
        for scan in range(1, scans_per_period + 1):
            started = time.monotonic()
            try:
                tickers = client.get_all_tickers()
            except Exception as exc:  # noqa: BLE001 - network problems: try again next scan
                log.warning("Could not fetch prices: %s", exc)
            else:
                for ticker in tickers:
                    if ticker["symbol"] in symbols:
                        history.setdefault(ticker["symbol"], []).append(float(ticker["price"]))
                log.debug("Round %d/%d complete", scan, scans_per_period)
            if scan < scans_per_period:
                time.sleep(max(0.0, TIME_SLEEP - (time.monotonic() - started)))

        picked = rank_coins(history)
        write_signals(NAME, [coin.symbol for coin in picked])
        if picked:
            for coin in picked:
                log.info("Signal on %s (change %+.2f%%, CoV %.2f)", coin.symbol, coin.change, coin.cov)
        else:
            log.info("No coins matched this period")
        time.sleep(TIME_SLEEP)
