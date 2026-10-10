import json

import pytest

from bvt.portfolio import Portfolio, PortfolioError, Position, held_symbols
from bvt.signals import clear_signals, is_paused, pause_sources, read_signals, set_paused, write_signals


def test_portfolio_round_trip(tmp_path):
    path = tmp_path / "coins.json"
    portfolio = Portfolio.load(path)
    assert len(portfolio) == 0
    portfolio.add(Position("BTCUSDT", 7, 1_700_000_000_000, 100.0, 0.15, 1.0, -3.0))
    loaded = Portfolio.load(path)
    assert "BTCUSDT" in loaded
    assert loaded.get("BTCUSDT").volume == 0.15
    assert not list(tmp_path.glob(".coins.json.*")), "temporary files must be cleaned up"
    loaded.remove("BTCUSDT")
    assert json.loads(path.read_text()) == {}


def test_legacy_portfolio_format(tmp_path):
    path = tmp_path / "coins_bought.json"
    path.write_text(
        json.dumps(
            {
                "BTCUSDT": {
                    "symbol": "BTCUSDT",
                    "orderid": 0,
                    "timestamp": 1625000000.5,  # old test mode stored seconds
                    "bought_at": "35000.10",  # and prices as strings
                    "volume": 0.0004,
                    "take_profit": 1.0,
                    "stop_loss": -3,
                },
                "ETHUSDT": {
                    "symbol": "ETHUSDT",
                    "orderid": 5,
                    "timestamp": 1625000000000,
                    "bought_at": "2000",
                    "volume": 0.01,
                    "take_profit": 2.1,
                    "stop_loss": 1.9,  # positive stop loss = trailing armed
                },
                "XRPUSDT": {"bought_at": 0.5, "volume": 30},  # added by hand: only the essentials
            }
        )
    )
    portfolio = Portfolio.load(path)
    btc, eth, xrp = portfolio.get("BTCUSDT"), portfolio.get("ETHUSDT"), portfolio.get("XRPUSDT")
    assert btc.bought_at == 35000.10 and btc.timestamp == 1625000000500 and not btc.trailing
    assert eth.trailing
    assert xrp.symbol == "XRPUSDT" and not xrp.trailing
    assert held_symbols(path) == {"BTCUSDT", "ETHUSDT", "XRPUSDT"}
    assert held_symbols(tmp_path / "missing.json") == set()


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "is empty"),
        ("[]", "JSON object"),
        ("null", "JSON object"),
        ("{not json", "Could not read"),
        ('{"BTCUSDT": {"symbol": "BTCUSDT"}}', "fix or remove them"),
        ('{"BTCUSDT": {"bought_at": 0, "volume": 1}}', "greater than 0"),
    ],
)
def test_unreadable_portfolio_is_refused_not_silently_emptied(tmp_path, content, message):
    path = tmp_path / "coins_bought.json"
    path.write_text(content)
    with pytest.raises(PortfolioError, match=message):
        Portfolio.load(path)
    assert path.read_text() == content, "the file must be left untouched"


def test_buy_and_sell_signals_are_read_in_order_and_consumed(tmp_path):
    (tmp_path / "a.exs").write_text("ethusdt\nBTCUSDT\n\n# comment\n")
    (tmp_path / "b.buy").write_text("BTCUSDT\r\nXRPUSDT\r\n")
    (tmp_path / "c.sell").write_text("DOGEUSDT\n")
    assert read_signals("buy", tmp_path) == ["ETHUSDT", "BTCUSDT", "XRPUSDT"]
    assert read_signals("buy", tmp_path) == []
    assert read_signals("sell", tmp_path, consume=False) == ["DOGEUSDT"]
    assert read_signals("sell", tmp_path) == ["DOGEUSDT"]
    assert list(tmp_path.iterdir()) == []
    assert read_signals("buy", tmp_path / "missing") == []


def test_write_signals_replaces_and_empty_removes(tmp_path):
    path = write_signals("mod", ["btcusdt", "BTCUSDT", "ETHUSDT"], directory=tmp_path)
    assert path.read_text() == "BTCUSDT\nETHUSDT\n"
    write_signals("mod", ["XRPUSDT"], directory=tmp_path)
    assert path.read_text() == "XRPUSDT\n"
    write_signals("mod", [], directory=tmp_path)
    assert not path.exists()
    write_signals("mod", ["XRPUSDT"], kind="sell", directory=tmp_path)
    assert (tmp_path / "mod.sell").exists()


def test_pause_files_from_both_formats(tmp_path):
    assert not is_paused(tmp_path)
    set_paused("one", True, tmp_path)
    (tmp_path / "paused.exc").write_text("yes")
    assert pause_sources(tmp_path) == ["one.pause", "paused.exc"]
    set_paused("one", False, tmp_path)
    (tmp_path / "paused.exc").unlink()
    assert not is_paused(tmp_path)


def test_clear_signals_keeps_other_files(tmp_path):
    for name in ("a.exs", "b.buy", "c.sell", "d.pause", "paused.exc", "x.exs.reading", "README.md"):
        (tmp_path / name).write_text("x")
    clear_signals(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["README.md"]
