"""Loading and validating ``config.yml``."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "config.yml"
DEFAULT_CREDS_FILE = "creds.yml"

# Signal modules run in their own processes; this tells them which config the bot uses.
CONFIG_ENV_VAR = "BVT_CONFIG"

_SCRIPT = "script_options"
_TRADING = "trading_options"

# Settings that only take effect after a restart; hot reloads keep the running value.
RESTART_ONLY = ("test_mode", "use_testnet", "american_user", "pair_with", "signalling_modules")


class ConfigError(Exception):
    """Raised when the config file is missing or contains invalid values."""


def _opt(section: str, default: Any) -> Any:
    return field(default=default, metadata={"section": section})


@dataclass(frozen=True)
class Config:
    # script_options
    test_mode: bool = _opt(_SCRIPT, True)
    use_testnet: bool = _opt(_SCRIPT, False)
    log_trades: bool = _opt(_SCRIPT, True)
    log_file: str = _opt(_SCRIPT, "trades.txt")
    debug: bool = _opt(_SCRIPT, False)
    american_user: bool = _opt(_SCRIPT, False)

    # trading_options
    pair_with: str = _opt(_TRADING, "USDT")
    quantity: float = _opt(_TRADING, 15.0)
    max_coins: int = _opt(_TRADING, 10)
    fiats: tuple[str, ...] = _opt(_TRADING, ())
    ex_pairs: tuple[str, ...] = _opt(_TRADING, ())
    tickers_list: str = _opt(_TRADING, "tickers.txt")
    custom_list: bool = _opt(_TRADING, True)
    time_difference: float = _opt(_TRADING, 1.0)
    recheck_interval: int = _opt(_TRADING, 6)
    change_in_price: float = _opt(_TRADING, 3.0)
    stop_loss: float = _opt(_TRADING, -3.0)
    take_profit: float = _opt(_TRADING, 6.0)
    use_trailing_stop_loss: bool = _opt(_TRADING, True)
    trailing_stop_loss: float = _opt(_TRADING, 0.2)
    trailing_take_profit: float = _opt(_TRADING, 0.1)
    trading_fee: float = _opt(_TRADING, 0.075)
    # liquidity filters (0 disables them)
    max_spread: float = _opt(_TRADING, 0.0)
    min_24h_volume: float = _opt(_TRADING, 0.0)
    signalling_modules: tuple[str, ...] = _opt(_TRADING, ())

    # where the config was loaded from (not a YAML key)
    path: str = field(default=DEFAULT_CONFIG_FILE, compare=False)

    @property
    def tick_seconds(self) -> float:
        """Seconds between two price checks."""
        return self.time_difference * 60 / self.recheck_interval

    @property
    def live_testnet(self) -> bool:
        """True when real orders are sent to the Binance Spot Testnet."""
        return self.use_testnet and not self.test_mode

    @property
    def portfolio_file(self) -> str:
        """JSON file holding the coins bought by the bot (separate per mode)."""
        if self.test_mode:
            return "test_coins_bought.json"
        if self.use_testnet:
            return "testnet_coins_bought.json"
        return "coins_bought.json"

    @property
    def excluded(self) -> frozenset[str]:
        """Symbols or base assets that are never traded (FIATS + EX_PAIRS)."""
        return frozenset(self.fiats) | frozenset(self.ex_pairs)

    def is_excluded(self, symbol: str) -> bool:
        base = symbol[: -len(self.pair_with)] if symbol.endswith(self.pair_with) else symbol
        return symbol in self.excluded or base in self.excluded

    def mode_name(self) -> str:
        if self.test_mode:
            return "TEST"
        return "TESTNET" if self.use_testnet else "LIVE"


def _fields() -> list[dataclasses.Field]:
    return [f for f in dataclasses.fields(Config) if "section" in f.metadata]


def _coerce(name: str, value: Any, default: Any) -> Any:
    key = name.upper()
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "yes", "on", "1"):
            return True
        if isinstance(value, str) and value.strip().lower() in ("false", "no", "off", "0"):
            return False
        raise ConfigError(f"{key} must be True or False, got {value!r}")
    if isinstance(default, tuple):
        if value is None:
            return ()
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, (list, tuple)):
            raise ConfigError(f"{key} must be a list, got {value!r}")
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(default, int):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ConfigError(f"{key} must be a whole number, got {value!r}")
        try:
            return int(value)
        except ValueError:
            raise ConfigError(f"{key} must be a whole number, got {value!r}") from None
    if isinstance(default, float):
        if isinstance(value, bool):
            raise ConfigError(f"{key} must be a number, got {value!r}")
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key} must be a number, got {value!r}") from None
    if value is None:
        raise ConfigError(f"{key} must not be empty")
    return str(value).strip()


def _validate(config: Config) -> None:
    errors = []
    if not config.pair_with.isalnum():
        errors.append("PAIR_WITH must be an asset name such as USDT")
    if config.quantity <= 0:
        errors.append("QUANTITY must be greater than 0")
    if config.max_coins < -1:
        errors.append("MAX_COINS must be -1, 0 or a positive number")
    if config.time_difference <= 0:
        errors.append("TIME_DIFFERENCE must be greater than 0")
    if config.recheck_interval < 1:
        errors.append("RECHECK_INTERVAL must be at least 1")
    if config.change_in_price <= 0:
        errors.append("CHANGE_IN_PRICE must be greater than 0")
    if config.stop_loss >= 0:
        errors.append("STOP_LOSS must be a negative number, e.g. -3")
    if config.take_profit <= 0:
        errors.append("TAKE_PROFIT must be greater than 0")
    if config.trading_fee < 0:
        errors.append("TRADING_FEE must not be negative")
    if config.max_spread < 0:
        errors.append("MAX_SPREAD must not be negative (0 disables the filter)")
    if config.min_24h_volume < 0:
        errors.append("MIN_24H_VOLUME must not be negative (0 disables the filter)")
    if config.use_trailing_stop_loss and (config.trailing_stop_loss <= 0 or config.trailing_take_profit <= 0):
        errors.append("TRAILING_STOP_LOSS and TRAILING_TAKE_PROFIT must be greater than 0")
    if config.custom_list and not config.tickers_list:
        errors.append("TICKERS_LIST must be set when CUSTOM_LIST is True")
    if errors:
        raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(errors))

    if config.take_profit <= 2 * config.trading_fee:
        log.warning(
            "TAKE_PROFIT (%s%%) does not cover the trading fees of a buy and a sell (2 x %s%%), "
            "so trades closed at take profit lose money",
            config.take_profit,
            config.trading_fee,
        )
    if config.use_trailing_stop_loss and config.trailing_stop_loss <= config.trailing_take_profit:
        log.warning(
            "TRAILING_STOP_LOSS (%s) should be greater than TRAILING_TAKE_PROFIT (%s), "
            "otherwise coins are sold right after the trailing stop is armed",
            config.trailing_stop_loss,
            config.trailing_take_profit,
        )


def parse_config(data: dict[str, Any], path: str = DEFAULT_CONFIG_FILE) -> Config:
    """Build a validated :class:`Config` from the parsed YAML document."""
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain the sections '{_SCRIPT}' and '{_TRADING}'")

    sections: dict[str, dict[str, Any]] = {}
    for section in (_SCRIPT, _TRADING):
        content = data.get(section) or {}
        if not isinstance(content, dict):
            raise ConfigError(f"'{section}' in {path} must be a mapping of settings")
        sections[section] = content

    values: dict[str, Any] = {"path": str(path)}
    known: dict[str, set[str]] = {_SCRIPT: set(), _TRADING: set()}
    for f in _fields():
        section = f.metadata["section"]
        key = f.name.upper()
        known[section].add(key)
        if key in sections[section]:
            values[f.name] = _coerce(f.name, sections[section][key], f.default)

    for section, content in sections.items():
        unknown = sorted(set(map(str, content)) - known[section])
        if unknown:
            log.warning("Ignoring unknown setting(s) in %s/%s: %s", path, section, ", ".join(unknown))

    if "pair_with" in values:
        values["pair_with"] = values["pair_with"].upper()
    for name in ("fiats", "ex_pairs"):
        if name in values:
            values[name] = tuple(item.upper() for item in values[name])
    if "signalling_modules" in values:
        values["signalling_modules"] = tuple(
            name[:-3] if name.endswith(".py") else name for name in values["signalling_modules"]
        )

    config = Config(**values)
    _validate(config)
    return config


def load_yaml(path: str | os.PathLike) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError:
        raise ConfigError(f"Could not find {path}") from None
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}:\n{exc}") from None


def load_config(path: str | os.PathLike = DEFAULT_CONFIG_FILE) -> Config:
    return parse_config(load_yaml(path), str(path))


def load_tickers(path: str | os.PathLike) -> list[str]:
    """Read a tickers list: one base asset per line (e.g. ``BTC``); ``#`` starts a comment."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"Could not find tickers list {path}") from None
    tickers: list[str] = []
    for line in text.splitlines():
        ticker = line.split("#", 1)[0].strip().upper()
        if ticker and ticker not in tickers:
            tickers.append(ticker)
    return tickers


def restart_only_changes(old: Config, new: Config) -> Config:
    """Return ``new`` with restart-only settings reset to ``old`` (warning about each)."""
    kept = {}
    for name in RESTART_ONLY:
        if getattr(old, name) != getattr(new, name):
            log.warning("%s changed in %s; restart the bot for it to take effect", name.upper(), new.path)
            kept[name] = getattr(old, name)
    return dataclasses.replace(new, **kept) if kept else new


def build_arg_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", "-c", default=None, help=f"path to the config file (default {DEFAULT_CONFIG_FILE})")
    parser.add_argument(
        "--creds", "-u", default=None, help=f"path to the credentials file (default {DEFAULT_CREDS_FILE})"
    )
    parser.add_argument("--debug", "-d", action="store_true", help="extra logging")
    return parser


def config_path_from_env_or_argv(argv: list[str] | None = None) -> str:
    """Config path for helper processes: ``$BVT_CONFIG``, then ``--config``, then the default."""
    if os.environ.get(CONFIG_ENV_VAR):
        return os.environ[CONFIG_ENV_VAR]
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", "-c", default=None)
    args, _ = parser.parse_known_args(argv)
    return args.config or DEFAULT_CONFIG_FILE
