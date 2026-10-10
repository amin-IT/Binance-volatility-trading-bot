"""The trading loop: watch prices, buy volatile or signalled coins, sell on TP/SL."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from binance.client import Client
from binance.exceptions import BinanceAPIException

from bvt import __version__
from bvt.config import (
    DEFAULT_CONFIG_FILE,
    DEFAULT_CREDS_FILE,
    Config,
    ConfigError,
    build_arg_parser,
    load_config,
    load_tickers,
    restart_only_changes,
)
from bvt.console import Colors, profit_color, set_debug, setup_logging
from bvt.credentials import CredentialsError, check_api_key, create_client, load_credentials, sync_time
from bvt.exchange import NETWORK_ERRORS, Broker, DustError, Exchange, ExchangeError, LiveBroker, PaperBroker, Quote
from bvt.portfolio import Portfolio, Position
from bvt.runner import SignalModuleRunner
from bvt.signals import SIGNALS_DIR, clear_signals, pause_sources, read_signals
from bvt.strategy import HOLD, SELL, TRAIL, PriceWindow, evaluate_exit, net_change, price_change, trade_profit

log = logging.getLogger("bvt")

MAINNET_WAIT_SECONDS = 10


@dataclass
class SessionStats:
    started: datetime
    wins: int = 0
    losses: int = 0
    realized_profit: float = 0.0  # in the quote currency
    realized_percent: float = 0.0  # sum of the net % of every trade
    last_sell: datetime | None = None

    @property
    def trades(self) -> int:
        return self.wins + self.losses

    @property
    def win_ratio(self) -> float:
        return self.wins / self.trades * 100 if self.trades else 0.0

    def record(self, net_percent: float, profit: float, at: datetime) -> None:
        if net_percent > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.realized_percent += net_percent
        self.realized_profit += profit
        self.last_sell = at


class Bot:
    def __init__(
        self,
        config: Config,
        client: Client,
        *,
        broker: Broker | None = None,
        signals_dir: str | os.PathLike = SIGNALS_DIR,
        clock: Callable[[], datetime] = datetime.now,
        force_debug: bool = False,
    ) -> None:
        self.config = config
        self.client = client
        self.exchange = Exchange(client)
        self.broker: Broker = broker or (PaperBroker(self.exchange) if config.test_mode else LiveBroker(self.exchange))
        self.portfolio = Portfolio.load(config.portfolio_file)
        self.window = PriceWindow(config.recheck_interval)
        self.stats = SessionStats(started=clock())
        self.signals_dir = Path(signals_dir)
        self.runner: SignalModuleRunner | None = None
        self.stop_event = threading.Event()
        self.interrupted = False
        self.finished = False
        self._clock = clock
        self._force_debug = force_debug
        self._tickers = frozenset(load_tickers(config.tickers_list)) if config.custom_list else frozenset()
        self._detected: dict[str, datetime] = {}  # last time a coin crossed CHANGE_IN_PRICE
        self._blocked: dict[str, datetime] = {}  # recently sold / failed buys: no buying for TIME_DIFFERENCE
        self._paused_since: datetime | None = None
        self._ticks = 0
        self._config_mtime = _mtime(config.path)

    # ----------------------------------------------------------------- universe

    def is_tradable(self, symbol: str, use_tickers_list: bool = True) -> bool:
        """Whether the bot may buy ``symbol`` (right quote asset, trading, not excluded, in the list)."""
        config = self.config
        rule = self.exchange.rule(symbol)
        if rule is None or not rule.tradable or rule.quote_asset != config.pair_with:
            return False
        if symbol in config.excluded or rule.base_asset in config.excluded:
            return False
        if use_tickers_list and config.custom_list and rule.base_asset not in self._tickers:
            return False
        return True

    def warn_unknown_tickers(self) -> None:
        if not self.config.custom_list:
            return
        rules = self.exchange.rules()
        missing = sorted(t for t in self._tickers if not self.is_tradable(t + self.config.pair_with))
        if missing:
            shown = ", ".join(missing[:20]) + (" …" if len(missing) > 20 else "")
            log.warning(
                "%d of %d coins in %s have no tradable %s pair on Binance (delisted or excluded) and are ignored: %s",
                len(missing),
                len(self._tickers),
                self.config.tickers_list,
                self.config.pair_with,
                shown,
            )
        if rules and len(missing) == len(self._tickers):
            log.error(
                "None of the coins in %s can be traded; check TICKERS_LIST and PAIR_WITH", self.config.tickers_list
            )

    # --------------------------------------------------------------------- loop

    def run(self) -> None:
        log.info("Press Ctrl-C to stop the bot")
        next_tick = time.monotonic()
        while not self.stop_event.is_set():
            self.reload_config()
            try:
                self.tick()
            except BinanceAPIException as exc:
                if exc.code == -1021:
                    sync_time(self.client)
                log.warning("Binance API error: %s (retrying next check)", exc)
            except NETWORK_ERRORS as exc:
                log.warning("Network error talking to Binance: %s (retrying next check)", exc)
            except Exception:
                log.exception("Unexpected error (retrying next check)")
            if self.runner:
                self.runner.check()
            if self.finished:
                break
            next_tick += self.config.tick_seconds
            delay = next_tick - time.monotonic()
            if delay < 0:
                next_tick = time.monotonic()
            # wait in short slices so Ctrl-C is handled promptly on every platform
            while delay > 0 and not self.stop_event.is_set():
                self.stop_event.wait(min(delay, 1.0))
                delay = next_tick - time.monotonic()

    def tick(self) -> None:
        """One price check: update the window, manage open positions, then look for buys."""
        now = self._clock()
        quotes = self.exchange.quotes()
        self.window.add(now, {s: q.mid for s, q in quotes.items() if self.is_tradable(s)})
        self._manage_positions(quotes, now)
        self._look_for_buys(quotes, now)

        if self._ticks % self.config.recheck_interval == 0:
            self.report(quotes)
        self._ticks += 1

        if self.config.max_coins == -1 and not self.portfolio:
            log.warning("MAX_COINS is -1 and all coins are sold: stopping the bot")
            self.finished = True

    # ---------------------------------------------------------------- selling

    def _manage_positions(self, quotes: dict[str, Quote], now: datetime) -> None:
        sell_signals = set(read_signals("sell", self.signals_dir))
        changed = False
        for position in self.portfolio:
            quote = quotes.get(position.symbol)
            if quote is None:
                log.warning("No price for %s, skipping it this check", position.symbol)
                continue
            price = quote.bid  # what a market sell would get
            decision = evaluate_exit(position, price, self.config)
            if position.symbol in sell_signals and decision.action != SELL:
                decision = dataclasses.replace(decision, action=SELL, reason="sell signal")

            if decision.action == SELL:
                self._close(position, price, decision.reason, now)
                continue
            if decision.action == TRAIL:
                if not position.trailing:
                    log.info(
                        "%s reached take profit (%+.2f%%): trailing stop armed at %+.2f%%",
                        position.symbol,
                        decision.change,
                        decision.stop_loss,
                    )
                else:
                    log.debug(
                        "%s trailing: TP %.2f%%, SL %.2f%%", position.symbol, decision.take_profit, decision.stop_loss
                    )
                position.trailing = True
            if decision.action in (TRAIL, HOLD) and (position.take_profit, position.stop_loss) != (
                decision.take_profit,
                decision.stop_loss,
            ):
                position.take_profit, position.stop_loss = decision.take_profit, decision.stop_loss
                changed = True
        if changed:
            self.portfolio.save()

    def _close(self, position: Position, price: float, reason: str, now: datetime) -> bool:
        config = self.config
        try:
            fill = self.broker.sell(position.symbol, Decimal(str(position.volume)), price)
        except DustError as exc:
            log.warning("Removing %s from the portfolio: %s", position.symbol, exc)
            self.portfolio.remove(position.symbol)
            return False
        except ExchangeError as exc:
            log.error("Could not sell %s: %s", position.symbol, exc)
            return False

        quantity = float(fill.quantity)
        change = price_change(position.bought_at, fill.price)
        net = net_change(change, config.trading_fee)
        profit = trade_profit(position.bought_at, fill.price, quantity, config.trading_fee)
        self.stats.record(net, profit, now)
        self._blocked[position.symbol] = now
        self.portfolio.remove(position.symbol)
        log.info(
            "%sSold %s %s (%s): %s -> %s, %+.2f%% / %+.2f %s after fees%s",
            profit_color(net),
            _fmt(quantity),
            position.symbol,
            reason,
            _fmt(position.bought_at),
            _fmt(fill.price),
            net,
            profit,
            config.pair_with,
            Colors.DEFAULT,
        )
        self._trade_log(
            f"Sell: {_fmt(quantity)} {position.symbol} - {_fmt(position.bought_at)} - {_fmt(fill.price)} "
            f"Profit: {profit:.2f} {net:.2f}% ({reason})"
        )
        return True

    def sell_all(self, reason: str = "sell all") -> bool:
        """Sell every held coin. Returns True when nothing is left in the portfolio."""
        if not self.portfolio:
            log.info("Not holding any coins")
            return True
        quotes = self.exchange.quotes()
        now = self._clock()
        for position in self.portfolio:
            quote = quotes.get(position.symbol)
            if quote is None:
                log.error("No price for %s, cannot sell it", position.symbol)
                continue
            self._close(position, quote.bid, reason, now)
        if self.portfolio:
            log.error("Still holding: %s", ", ".join(p.symbol for p in self.portfolio))
            return False
        log.info(
            "Session result: %+.2f %s over %d trade(s)",
            self.stats.realized_profit,
            self.config.pair_with,
            self.stats.trades,
        )
        return True

    # ----------------------------------------------------------------- buying

    def _look_for_buys(self, quotes: dict[str, Quote], now: datetime) -> None:
        config = self.config
        buy_signals = read_signals("buy", self.signals_dir)
        sources = pause_sources(self.signals_dir)
        if sources and self._paused_since is None:
            self._paused_since = now
            log.warning("Buying paused by %s; stop loss and take profit keep working", ", ".join(sources))
        elif not sources and self._paused_since is not None:
            log.warning("Buying resumed after a pause of %s", _duration(now - self._paused_since))
            self._paused_since = None
        if sources:
            if buy_signals:
                log.debug("Ignoring %d buy signal(s) while paused", len(buy_signals))
            return
        if config.max_coins <= 0:
            return

        cooloff = timedelta(minutes=config.time_difference)
        candidates: list[tuple[str, str, bool]] = []
        for symbol, change in sorted(self.window.momentum().items(), key=lambda item: item[1], reverse=True):
            if change <= config.change_in_price:
                break
            last_seen = self._detected.get(symbol)
            if last_seen is not None and now - last_seen < cooloff:
                continue
            self._detected[symbol] = now
            reason = f"up {change:.3f}% from its low of the last {config.time_difference:g} minute(s)"
            candidates.append((symbol, reason, False))
        candidates.extend((symbol, "external signal", True) for symbol in buy_signals)

        for symbol, reason, from_signal in candidates:
            # signals are rewritten every few seconds by some modules, so keep their skips quiet
            skip = log.debug if from_signal else log.info
            if symbol in self.portfolio:
                skip("%s: %s, but it is already held", symbol, reason)
                continue
            if len(self.portfolio) >= config.max_coins:
                skip("%s: %s, but already holding the maximum of %d coins", symbol, reason, config.max_coins)
                continue
            if not self.is_tradable(symbol, use_tickers_list=not from_signal):
                log.debug("%s: %s, but it is not a tradable %s pair or is excluded", symbol, reason, config.pair_with)
                continue
            blocked_at = self._blocked.get(symbol)
            if blocked_at is not None and now - blocked_at < cooloff:
                log.debug(
                    "%s: %s, but it was traded less than %g minute(s) ago", symbol, reason, config.time_difference
                )
                continue
            quote = quotes.get(symbol)
            if quote is None:
                continue
            problem = self._liquidity_problem(symbol, quote)
            if problem:
                skip("%s: %s, but %s", symbol, reason, problem)
                continue
            self._open(symbol, quote.ask, reason, now)

    def _liquidity_problem(self, symbol: str, quote: Quote) -> str | None:
        """Why ``symbol`` is too illiquid to buy, or None. Thin markets fake moves and fill badly."""
        config = self.config
        if config.max_spread and quote.spread > config.max_spread:
            return f"its bid/ask spread of {quote.spread:.2f}% is above MAX_SPREAD ({config.max_spread:g}%)"
        if config.min_24h_volume:
            volume = self.exchange.volumes_24h().get(symbol, 0.0)
            if volume < config.min_24h_volume:
                return (
                    f"its 24h volume of {volume:,.0f} {config.pair_with} is below MIN_24H_VOLUME "
                    f"({config.min_24h_volume:,.0f})"
                )
        return None

    def _open(self, symbol: str, price: float, reason: str, now: datetime) -> None:
        config = self.config
        log.info(
            "%sBuying %s for %g %s: %s%s", Colors.BUY, symbol, config.quantity, config.pair_with, reason, Colors.DEFAULT
        )
        try:
            fill = self.broker.buy(symbol, config.quantity, price)
        except ExchangeError as exc:
            log.error("Could not buy %s: %s", symbol, exc)
            self._blocked[symbol] = now
            return
        position = Position(
            symbol=symbol,
            orderid=fill.order_id,
            timestamp=fill.timestamp,
            bought_at=fill.price,
            volume=float(fill.net_quantity),
            take_profit=config.take_profit,
            stop_loss=config.stop_loss,
        )
        self.portfolio.add(position)
        log.info(
            "%sBought %s %s at %s (order %s)%s",
            Colors.BUY,
            _fmt(position.volume),
            symbol,
            _fmt(fill.price),
            fill.order_id,
            Colors.DEFAULT,
        )
        self._trade_log(f"Buy : {_fmt(position.volume)} {symbol} - {_fmt(fill.price)}")

    # --------------------------------------------------------------- reporting

    def report(self, quotes: dict[str, Quote]) -> None:
        config, stats = self.config, self.stats
        now = self._clock()
        pair = config.pair_with

        unrealized_percent = unrealized_profit = 0.0
        position_lines = []
        for position in self.portfolio:
            quote = quotes.get(position.symbol)
            if quote is None:
                continue
            price = quote.bid
            net = net_change(price_change(position.bought_at, price), config.trading_fee)
            profit = trade_profit(position.bought_at, price, position.volume, config.trading_fee)
            unrealized_percent += net
            unrealized_profit += profit
            position_lines.append(
                f"  {position.symbol:<14} {_fmt(position.bought_at)} -> {_fmt(price)}  "
                f"{profit_color(net)}{net:+.2f}% ({profit:+.2f} {pair}){Colors.DEFAULT}  "
                f"SL {position.stop_loss:+.2f}% TP {position.take_profit:+.2f}%"
                + ("  [trailing]" if position.trailing else "")
            )

        if config.test_mode:
            mode = f"Test mode, {Colors.SELL_PROFIT}no real money is used{Colors.DEFAULT}"
        elif config.use_testnet:
            mode = f"{Colors.WARNING}Binance Spot Testnet (real orders, test funds){Colors.DEFAULT}"
        else:
            mode = f"{Colors.WARNING}LIVE - you are using REAL money!{Colors.DEFAULT}"
        per_trade = stats.realized_percent / stats.trades if stats.trades else 0.0
        up, down, unchanged = self.window.market_breadth()
        trailing = (
            f"yes (stop {config.trailing_stop_loss}%, step {config.trailing_take_profit}%)"
            if config.use_trailing_stop_loss
            else "no"
        )
        slots = f"{len(self.portfolio)}/{max(config.max_coins, 0)}"
        lines = [
            "--------",
            f"Mode            : {mode}",
            f"Session profit  : {profit_color(stats.realized_profit)}{stats.realized_profit:+.2f} {pair} "
            f"({stats.realized_percent:+.2f}%, ~{per_trade:+.2f}% per trade){Colors.DEFAULT}",
            f"Unrealised      : {profit_color(unrealized_profit)}{unrealized_profit:+.2f} {pair} "
            f"({unrealized_percent:+.2f}%){Colors.DEFAULT}",
            f"Trades          : {stats.trades} (wins {stats.wins}, losses {stats.losses}, "
            f"win ratio {stats.win_ratio:.1f}%)",
            f"Last sell       : {stats.last_sell.strftime('%Y-%m-%d %H:%M:%S') if stats.last_sell else 'never'}",
            f"Running for     : {_duration(now - stats.started)} (since {stats.started.strftime('%Y-%m-%d %H:%M:%S')})",
            f"Holding         : {slots} coins ({len(self.portfolio) * config.quantity:g}/"
            f"{max(config.max_coins, 0) * config.quantity:g} {pair})",
            f"Market          : {Colors.SELL_PROFIT}up {up}{Colors.DEFAULT}, {Colors.SELL_LOSS}down {down}"
            f"{Colors.DEFAULT}, unchanged {unchanged} (last {config.time_difference:g} min)",
            f"Strategy        : buy on +{config.change_in_price}% in {config.time_difference:g} min, "
            f"TP {config.take_profit}%, SL {config.stop_loss}%, trailing {trailing}",
            f"Liquidity       : max spread {f'{config.max_spread:g}%' if config.max_spread else 'any'}, "
            f"min 24h volume {f'{config.min_24h_volume:,.0f} {pair}' if config.min_24h_volume else 'any'}",
            f"Signal modules  : {', '.join(config.signalling_modules) or 'none'}",
        ]
        if self._paused_since is not None:
            lines.append(f"{Colors.WARNING}Buying paused for {_duration(now - self._paused_since)}{Colors.DEFAULT}")
        if config.max_coins == 0:
            lines.append(f"{Colors.WARNING}MAX_COINS is 0: not buying, still selling at TP/SL{Colors.DEFAULT}")
        elif config.max_coins == -1:
            lines.append(f"{Colors.WARNING}MAX_COINS is -1: the bot stops once every coin is sold{Colors.DEFAULT}")
        if position_lines:
            lines.append("Positions       :")
            lines.extend(position_lines)
        lines.append("--------")
        for line in lines:
            log.info("%s", line)

    def _trade_log(self, line: str) -> None:
        if not self.config.log_trades or not self.config.log_file:
            return
        timestamp = self._clock().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(self.config.log_file, "a", encoding="utf-8") as handle:
                handle.write(f"{timestamp} {line}\n")
        except OSError as exc:
            log.error("Could not write to %s: %s", self.config.log_file, exc)

    # ------------------------------------------------------------ config reload

    def reload_config(self) -> None:
        """Pick up edits to config.yml while running (restart-only settings excepted)."""
        mtime = _mtime(self.config.path)
        if mtime is None or mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            new = restart_only_changes(self.config, load_config(self.config.path))
        except ConfigError as exc:
            log.error("Keeping the current settings, %s could not be loaded: %s", self.config.path, exc)
            return
        if new == self.config:
            return
        if new.custom_list:
            try:
                self._tickers = frozenset(load_tickers(new.tickers_list))
            except ConfigError as exc:
                log.error("Keeping the current tickers list: %s", exc)
                new = dataclasses.replace(new, tickers_list=self.config.tickers_list)
        self.config = new
        self.window.resize(new.recheck_interval)
        set_debug(new.debug or self._force_debug)
        log.info("Reloaded settings from %s", new.path)


def _mtime(path: str) -> float | None:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _fmt(value: float) -> str:
    """Compact number formatting for prices and quantities."""
    return f"{value:.8f}".rstrip("0").rstrip(".") if value else "0"


def _duration(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{days}d {text}" if days else text


# ------------------------------------------------------------------- startup


def connect(config: Config, creds_path: str) -> Client:
    """Create the Binance client; live and testnet trading require valid API keys."""
    credentials = None
    if not config.test_mode:
        credentials = load_credentials(creds_path, testnet=config.use_testnet)
        if credentials is None:
            section, env = ("testnet", "BINANCE_TESTNET_API") if config.use_testnet else ("prod", "BINANCE_API")
            raise CredentialsError(
                f"No API keys found. Copy creds.example.yml to {creds_path} and fill in the '{section}' section, "
                f"or set the {env}_KEY and {env}_SECRET environment variables."
            )
    client = create_client(config, credentials)
    if credentials:
        sync_time(client)
        check_api_key(client)
    return client


def _install_signal_handlers(bot: Bot) -> None:
    def on_interrupt(signum, frame):  # noqa: ARG001
        if bot.stop_event.is_set():
            raise KeyboardInterrupt  # second Ctrl-C: stop right away
        bot.interrupted = True
        bot.stop_event.set()
        log.warning("Stopping after the current check (press Ctrl-C again to force)...")

    signal.signal(signal.SIGINT, on_interrupt)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda _signum, _frame: bot.stop_event.set())


def ask_yes_no(question: str) -> bool:
    try:
        return input(question).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser("Binance volatility trading bot")
    parser.add_argument("--notimeout", action="store_true", help="skip the safety delay before live trading starts")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    setup_logging(args.debug)

    try:
        config = load_config(args.config or DEFAULT_CONFIG_FILE)
        set_debug(config.debug or args.debug)
        client = connect(config, args.creds or DEFAULT_CREDS_FILE)
        bot = Bot(config, client, force_debug=args.debug)
        bot.warn_unknown_tickers()
    except (ConfigError, CredentialsError) as exc:
        log.error("%s", exc)
        sys.exit(1)
    except (BinanceAPIException, *NETWORK_ERRORS) as exc:
        log.error("Could not connect to Binance: %s", exc)
        sys.exit(1)
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Could not read the portfolio file %s: %s", config.portfolio_file, exc)
        sys.exit(1)

    log.info("Binance Volatility Trading Bot %s", __version__)
    if config.test_mode:
        log.info("%sTest mode: trades are simulated, no real money is used%s", Colors.SELL_PROFIT, Colors.DEFAULT)
    elif config.use_testnet:
        log.warning("Trading on the Binance Spot Testnet with test funds")
    else:
        log.warning(
            "LIVE mode: the bot trades with REAL money on %s", "Binance.US" if config.american_user else "Binance"
        )
        if not args.notimeout:
            log.warning("Starting in %d seconds, press Ctrl-C to abort", MAINNET_WAIT_SECONDS)
            try:
                time.sleep(MAINNET_WAIT_SECONDS)
            except KeyboardInterrupt:
                log.info("Aborted")
                return
    if bot.portfolio:
        log.info("Loaded %d held coin(s) from %s", len(bot.portfolio), config.portfolio_file)

    clear_signals(bot.signals_dir)
    runner = SignalModuleRunner(config.signalling_modules, str(Path(config.path).resolve()), config.debug or args.debug)
    bot.runner = runner
    try:
        runner.start()
        _install_signal_handlers(bot)
        bot.run()
    except KeyboardInterrupt:
        bot.interrupted = True
    finally:
        signal.signal(signal.SIGINT, signal.default_int_handler)
        runner.stop()

    if bot.interrupted and bot.portfolio and sys.stdin is not None and sys.stdin.isatty():
        question = (
            f"{Colors.WARNING}Bot stopped. Sell all {len(bot.portfolio)} held coin(s) now? (y/N) {Colors.DEFAULT}"
        )
        if ask_yes_no(question):
            try:
                bot.sell_all()
            except (BinanceAPIException, *NETWORK_ERRORS) as exc:
                log.error("Could not reach Binance (%s); run sell-remaining-coins.py to try again", exc)
    log.info("Bot stopped")


if __name__ == "__main__":
    main()
