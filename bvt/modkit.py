"""Helpers for writing signal modules.

Signal modules run in their own process, so everything here is safe to call from
``do_work()``. Nothing touches the network at import time.

Example::

    from bvt import modkit
    from bvt.signals import write_signals

    def do_work():
        config = modkit.bot_config()
        while True:
            pairs = modkit.watchlist(config)
            ...
            write_signals("mymodule", picked)
            modkit.sleep_minutes(1)
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from binance.client import Client

from bvt.config import Config, ConfigError, config_path_from_env_or_argv, load_config, load_tickers
from bvt.portfolio import held_symbols as _held_symbols

log = logging.getLogger(__name__)

TRADINGVIEW_EXCHANGE = "BINANCE"
TRADINGVIEW_SCREENER = "crypto"
TRADINGVIEW_BATCH = 200


def bot_config() -> Config:
    """The configuration the bot was started with."""
    return load_config(config_path_from_env_or_argv())


def watchlist(config: Config | None = None) -> list[str]:
    """Trading pairs from the bot's TICKERS_LIST (e.g. ``['BTCUSDT', ...]``), minus excluded coins."""
    config = config or bot_config()
    try:
        tickers = load_tickers(config.tickers_list)
    except ConfigError as exc:
        log.error("%s", exc)
        return []
    pairs = [ticker + config.pair_with for ticker in tickers]
    return [pair for pair in pairs if not config.is_excluded(pair)]


def held_symbols(config: Config | None = None) -> set[str]:
    """Symbols the bot currently holds."""
    config = config or bot_config()
    return _held_symbols(config.portfolio_file)


def public_client(config: Config | None = None) -> Client:
    """Unauthenticated Binance client for market data (binance.com or binance.us per config)."""
    config = config or bot_config()
    return Client(tld="us" if config.american_user else "com", ping=False)


def sleep_minutes(minutes: float) -> None:
    time.sleep(minutes * 60)


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def klines(client: Client, symbol: str, interval: str, limit: int) -> list[Candle]:
    """Recent candles for ``symbol``; ``interval`` is a Binance interval such as ``'1m'`` or ``'4h'``."""
    rows = client.get_klines(symbol=symbol, interval=interval, limit=limit)
    return [Candle(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]


def klines_many(
    client: Client, symbols: Iterable[str], interval: str, limit: int, workers: int = 8
) -> dict[str, list[Candle]]:
    """Fetch candles for many symbols in parallel; symbols that fail are left out."""

    def fetch(symbol: str) -> tuple[str, list[Candle] | None]:
        try:
            return symbol, klines(client, symbol, interval, limit)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the scan
            log.debug("Could not fetch %s %s candles: %s", symbol, interval, exc)
            return symbol, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return {symbol: candles for symbol, candles in pool.map(fetch, list(symbols)) if candles}


def tradingview_analysis(symbols: Iterable[str], interval: str, timeout: float = 20) -> dict[str, Any]:
    """TradingView technical analysis for many Binance symbols, fetched in batches.

    Returns ``{symbol: tradingview_ta.Analysis}``; symbols TradingView does not know
    are left out. Batching avoids the HTTP 429 errors that one request per coin causes.
    """
    from tradingview_ta import get_multiple_analysis

    symbols = list(dict.fromkeys(symbols))
    result: dict[str, Any] = {}
    for start in range(0, len(symbols), TRADINGVIEW_BATCH):
        batch = [f"{TRADINGVIEW_EXCHANGE}:{symbol}" for symbol in symbols[start : start + TRADINGVIEW_BATCH]]
        try:
            analyses = get_multiple_analysis(TRADINGVIEW_SCREENER, interval, batch, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - network/JSON errors from TradingView
            log.warning("TradingView request failed: %s", exc)
            continue
        for key, analysis in analyses.items():
            if analysis is not None:
                result[key.split(":", 1)[-1]] = analysis
    return result
