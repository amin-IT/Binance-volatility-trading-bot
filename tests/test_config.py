import dataclasses
import logging
from pathlib import Path

import pytest

from bvt.config import (
    Config,
    ConfigError,
    config_path_from_env_or_argv,
    load_config,
    load_tickers,
    parse_config,
    restart_only_changes,
)

ROOT = Path(__file__).resolve().parent.parent


def test_shipped_config_is_valid_and_safe():
    config = load_config(ROOT / "config.yml")
    assert config.test_mode is True, "the committed config must never trade real money by default"
    assert config.signalling_modules == ("pausebotmod", "VolScan")
    assert config.portfolio_file == "test_coins_bought.json"
    assert config.tick_seconds == pytest.approx(10)


def test_shipped_tickers_list_loads():
    tickers = load_tickers(ROOT / "tickers.txt")
    assert "BTC" in tickers and "BNB" in tickers
    assert all(t == t.strip().upper() for t in tickers)


def test_legacy_config_without_new_keys_loads(caplog):
    data = {
        "script_options": {"TEST_MODE": False, "LOG_TRADES": True, "LOG_FILE": "trades.txt", "DEBUG": False},
        "trading_options": {
            "PAIR_WITH": "usdt",
            "QUANTITY": 15,
            "MAX_COINS": 10,
            "FIATS": ["EURUSDT", "DOWN"],
            "TICKERS_LIST": "tickers.txt",
            "TIME_DIFFERENCE": 1,
            "RECHECK_INTERVAL": 6,
            "CHANGE_IN_PRICE": 3,
            "STOP_LOSS": -3,
            "TAKE_PROFIT": 1.0,
            "CUSTOM_LIST": True,
            "USE_TRAILING_STOP_LOSS": True,
            "TRAILING_STOP_LOSS": 0.2,
            "TRAILING_TAKE_PROFIT": 0.1,
            "TRADING_FEE": 0.075,
            "SIGNALLING_MODULES": ["pausebotmod.py"],
            "SOME_OTHER_FORK_SETTING": 1,
        },
    }
    with caplog.at_level(logging.WARNING):
        config = parse_config(data)
    assert config.pair_with == "USDT"
    assert config.portfolio_file == "coins_bought.json"
    assert config.max_spread == 0 and config.min_24h_volume == 0  # new filters default to off
    assert config.signalling_modules == ("pausebotmod",)
    assert "SOME_OTHER_FORK_SETTING" in caplog.text


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("STOP_LOSS", 3, "STOP_LOSS must be a negative"),
        ("TAKE_PROFIT", 0, "TAKE_PROFIT must be greater"),
        ("QUANTITY", -1, "QUANTITY"),
        ("RECHECK_INTERVAL", 0, "RECHECK_INTERVAL"),
        ("MAX_COINS", -2, "MAX_COINS"),
        ("MAX_SPREAD", -1, "MAX_SPREAD"),
        ("QUANTITY", "lots", "QUANTITY must be a number"),
        ("MAX_COINS", 2.5, "MAX_COINS must be a whole number"),
        ("CUSTOM_LIST", "maybe", "CUSTOM_LIST must be True or False"),
    ],
)
def test_invalid_values_are_rejected(key, value, message):
    with pytest.raises(ConfigError, match=message):
        parse_config({"script_options": {}, "trading_options": {key: value}})


def test_fee_and_trailing_sanity_warnings(caplog):
    with caplog.at_level(logging.WARNING):
        parse_config(
            {
                "trading_options": {
                    "TAKE_PROFIT": 0.1,
                    "TRADING_FEE": 0.1,
                    "TRAILING_STOP_LOSS": 0.1,
                    "TRAILING_TAKE_PROFIT": 0.2,
                }
            }
        )
    assert "does not cover the trading fees" in caplog.text
    assert "TRAILING_STOP_LOSS" in caplog.text


def test_missing_and_broken_files(tmp_path):
    with pytest.raises(ConfigError, match="Could not find"):
        load_config(tmp_path / "nope.yml")
    broken = tmp_path / "broken.yml"
    broken.write_text("trading_options: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="Could not parse"):
        load_config(broken)
    with pytest.raises(ConfigError):
        parse_config(["not", "a", "mapping"])


def test_exclusions_match_base_asset_or_pair():
    config = Config(fiats=("EURUSDT",), ex_pairs=("USDC",))
    assert config.is_excluded("EURUSDT")
    assert config.is_excluded("USDCUSDT")
    assert not config.is_excluded("JUPUSDT")  # substring matching used to exclude coins containing UP


def test_portfolio_file_per_mode():
    assert Config(test_mode=True).portfolio_file == "test_coins_bought.json"
    assert Config(test_mode=False).portfolio_file == "coins_bought.json"
    assert Config(test_mode=False, use_testnet=True).portfolio_file == "testnet_coins_bought.json"
    assert Config(test_mode=False, use_testnet=True).live_testnet


def test_restart_only_settings_are_kept(caplog):
    old = Config(test_mode=True, take_profit=1.0)
    new = dataclasses.replace(old, test_mode=False, take_profit=2.0)
    with caplog.at_level(logging.WARNING):
        merged = restart_only_changes(old, new)
    assert merged.test_mode is True
    assert merged.take_profit == 2.0
    assert "TEST_MODE changed" in caplog.text


def test_tickers_list_handles_crlf_comments_and_duplicates(tmp_path):
    path = tmp_path / "t.txt"
    path.write_text("btc\r\n# comment\r\nETH # second\r\n\r\nBTC\r\n", encoding="utf-8")
    assert load_tickers(path) == ["BTC", "ETH"]


def test_config_path_for_child_processes(monkeypatch):
    monkeypatch.delenv("BVT_CONFIG", raising=False)
    assert config_path_from_env_or_argv(["--debug"]) == "config.yml"
    assert config_path_from_env_or_argv(["-c", "other.yml", "--notimeout"]) == "other.yml"
    monkeypatch.setenv("BVT_CONFIG", "/abs/config.yml")
    assert config_path_from_env_or_argv(["-c", "other.yml"]) == "/abs/config.yml"
