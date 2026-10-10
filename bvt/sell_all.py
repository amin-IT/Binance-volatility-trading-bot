"""Sell every coin held by the bot (``python sell-remaining-coins.py``)."""

from __future__ import annotations

import logging
import sys

from binance.exceptions import BinanceAPIException

from bvt.bot import Bot, ask_yes_no, connect
from bvt.config import DEFAULT_CONFIG_FILE, DEFAULT_CREDS_FILE, ConfigError, build_arg_parser, load_config
from bvt.console import Colors, setup_logging
from bvt.credentials import CredentialsError
from bvt.exchange import NETWORK_ERRORS
from bvt.portfolio import PortfolioError

log = logging.getLogger("bvt")


def main(argv: list[str] | None = None) -> None:
    parser = build_arg_parser("Sell all coins held by the Binance volatility trading bot")
    parser.add_argument("--yes", "-y", action="store_true", help="do not ask for confirmation")
    args = parser.parse_args(argv)
    setup_logging(args.debug)

    try:
        config = load_config(args.config or DEFAULT_CONFIG_FILE)
        client = connect(config, args.creds or DEFAULT_CREDS_FILE)
        bot = Bot(config, client)
    except (ConfigError, CredentialsError, PortfolioError) as exc:
        log.error("%s", exc)
        sys.exit(1)
    except (BinanceAPIException, *NETWORK_ERRORS) as exc:
        log.error("Could not connect to Binance: %s", exc)
        sys.exit(1)

    if not bot.portfolio:
        log.info("%s is empty, nothing to sell", config.portfolio_file)
        return
    symbols = ", ".join(position.symbol for position in bot.portfolio)
    mode = "simulated (test mode)" if config.test_mode else config.mode_name()
    log.info("Holding %d coin(s) in %s: %s", len(bot.portfolio), config.portfolio_file, symbols)
    question = f"{Colors.WARNING}Sell all of them now ({mode})? (y/N) {Colors.DEFAULT}"
    if not args.yes and not ask_yes_no(question):
        log.info("Nothing sold")
        return
    try:
        sold_everything = bot.sell_all()
    except (BinanceAPIException, *NETWORK_ERRORS) as exc:
        log.error("Could not reach Binance: %s", exc)
        sys.exit(1)
    sys.exit(0 if sold_everything else 1)


if __name__ == "__main__":
    main()
