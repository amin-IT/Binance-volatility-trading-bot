from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeClient  # noqa: E402

BASE_CONFIG = {
    "script_options": {"TEST_MODE": True, "LOG_TRADES": True, "LOG_FILE": "trades.txt", "DEBUG": False},
    "trading_options": {
        "PAIR_WITH": "USDT",
        "QUANTITY": 15,
        "MAX_COINS": 2,
        "FIATS": [],
        "EX_PAIRS": ["USDC"],
        "TICKERS_LIST": "tickers.txt",
        "CUSTOM_LIST": True,
        "TIME_DIFFERENCE": 1,
        "RECHECK_INTERVAL": 4,
        "CHANGE_IN_PRICE": 3,
        "STOP_LOSS": -3,
        "TAKE_PROFIT": 2,
        "USE_TRAILING_STOP_LOSS": True,
        "TRAILING_STOP_LOSS": 0.5,
        "TRAILING_TAKE_PROFIT": 0.1,
        "TRADING_FEE": 0.075,
        "SIGNALLING_MODULES": [],
    },
}


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, 0)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def restore_logging():
    """bvt.console.setup_logging replaces the root handlers; put pytest's back afterwards."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for var in ("BINANCE_API_KEY", "BINANCE_API_SECRET", "BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "tickers.txt").write_text("BTC\r\nETH\r\nBNB\r\nXRP\r\nUSDC\r\nOLD\r\n", encoding="utf-8")
    (tmp_path / "signals").mkdir()
    return tmp_path


@pytest.fixture
def write_config(workdir):
    def write(script=None, trading=None, name="config.yml"):
        data = {
            "script_options": {**BASE_CONFIG["script_options"], **(script or {})},
            "trading_options": {**BASE_CONFIG["trading_options"], **(trading or {})},
        }
        path = workdir / name
        path.write_text(yaml.safe_dump(data), encoding="utf-8")
        return str(path)

    return write


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def clock():
    return Clock()
