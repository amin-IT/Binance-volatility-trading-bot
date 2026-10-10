import json
import logging
import os
import time

import pytest
import requests
import yaml

from bvt.bot import Bot
from bvt.config import load_config
from bvt.portfolio import Portfolio


@pytest.fixture
def make_bot(write_config, client, clock, workdir):
    def make(script=None, **trading):
        path = write_config(script=script, trading=trading)
        return Bot(load_config(path), client, clock=clock, signals_dir=workdir / "signals")

    return make


def tick(bot, client, clock, **prices):
    for symbol, price in prices.items():
        client.set_price(symbol, price)
    bot.tick()
    clock.advance(bot.config.tick_seconds)


def trades(workdir):
    path = workdir / "trades.txt"
    return path.read_text().splitlines() if path.exists() else []


def test_buys_a_coin_that_rises_and_sells_it_with_the_trailing_stop(make_bot, client, clock, workdir):
    bot = make_bot()
    tick(bot, client, clock, BTCUSDT=100)
    tick(bot, client, clock, BTCUSDT=100)
    tick(bot, client, clock, BTCUSDT=104)  # +4% > CHANGE_IN_PRICE 3
    position = bot.portfolio.get("BTCUSDT")
    assert position is not None
    assert position.bought_at == pytest.approx(104 * 1.0001)  # filled at the ask
    saved = json.loads((workdir / "test_coins_bought.json").read_text())
    assert saved["BTCUSDT"]["volume"] == position.volume

    tick(bot, client, clock, BTCUSDT=107)  # +2.9% > TP 2: trailing armed, not sold
    assert bot.portfolio.get("BTCUSDT").trailing
    tick(bot, client, clock, BTCUSDT=108)
    tick(bot, client, clock, BTCUSDT=107.2)  # back below the trailing stop
    assert "BTCUSDT" not in bot.portfolio
    assert bot.stats.wins == 1 and bot.stats.realized_profit > 0
    log = trades(workdir)
    assert log[0].endswith("Buy : 0.14421 BTCUSDT - 104.0104")
    assert "Sell: 0.14421 BTCUSDT" in log[1] and "(trailing stop loss)" in log[1]


def test_does_not_buy_a_spike_that_already_reversed(make_bot, client, clock):
    bot = make_bot()
    tick(bot, client, clock, BTCUSDT=100)
    client.set_price("BTCUSDT", 104)  # spike between two checks the bot never sees as current
    tick(bot, client, clock, BTCUSDT=100.5)
    assert len(bot.portfolio) == 0


def test_stop_loss(make_bot, client, clock, workdir):
    bot = make_bot()
    tick(bot, client, clock, ETHUSDT=2000)
    tick(bot, client, clock, ETHUSDT=2080)
    assert "ETHUSDT" in bot.portfolio
    tick(bot, client, clock, ETHUSDT=2010)  # -3.4%
    assert "ETHUSDT" not in bot.portfolio
    assert bot.stats.losses == 1 and bot.stats.realized_profit < 0
    assert "(stop loss)" in trades(workdir)[-1]


def test_respects_max_coins_and_excluded_or_unlisted_coins(make_bot, client, clock):
    bot = make_bot(MAX_COINS=2)
    tick(bot, client, clock, BTCUSDT=100, ETHUSDT=100, BNBUSDT=100, XRPUSDT=1, USDCUSDT=1, DOGEUSDT=1, OLDUSDT=1)
    tick(bot, client, clock, BTCUSDT=106, ETHUSDT=105, BNBUSDT=104, USDCUSDT=1.1, DOGEUSDT=1.2, OLDUSDT=1.2)
    assert set(bot.portfolio.positions) == {"BTCUSDT", "ETHUSDT"}  # strongest two
    # USDC is excluded, DOGE is not in tickers.txt and OLD is not trading


def test_buy_signals(make_bot, client, clock, workdir):
    bot = make_bot(MAX_COINS=5)
    tick(bot, client, clock, DOGEUSDT=0.1, XRPUSDT=0.5)
    (workdir / "signals" / "mod.exs").write_text("DOGEUSDT\nETHBTC\nUSDCUSDT\nNOPEUSDT\n")
    (workdir / "signals" / "other.buy").write_text("XRPUSDT\n")
    tick(bot, client, clock)
    # signals may name coins outside tickers.txt, but must be tradable PAIR_WITH pairs
    assert set(bot.portfolio.positions) == {"DOGEUSDT", "XRPUSDT"}
    assert not list((workdir / "signals").glob("*.exs")), "signal files are consumed"


def test_sell_signal_sells_a_held_coin(make_bot, client, clock, workdir):
    bot = make_bot()
    tick(bot, client, clock, BTCUSDT=100)
    tick(bot, client, clock, BTCUSDT=104)
    (workdir / "signals" / "mod.sell").write_text("BTCUSDT\nETHUSDT\n")
    tick(bot, client, clock)
    assert "BTCUSDT" not in bot.portfolio
    assert "(sell signal)" in trades(workdir)[-1]


def test_pause_stops_buying_but_not_selling(make_bot, client, clock, workdir, caplog):
    bot = make_bot(MAX_COINS=5)
    tick(bot, client, clock, BTCUSDT=100, ETHUSDT=100)
    tick(bot, client, clock, BTCUSDT=104)
    (workdir / "signals" / "pausebotmod.pause").write_text("paused")
    (workdir / "signals" / "mod.exs").write_text("XRPUSDT\n")
    with caplog.at_level(logging.WARNING):
        tick(bot, client, clock, ETHUSDT=104.5, BTCUSDT=100)  # BTC hits stop loss while paused
    assert len(bot.portfolio) == 0
    assert "Buying paused by pausebotmod.pause" in caplog.text
    (workdir / "signals" / "pausebotmod.pause").unlink()
    with caplog.at_level(logging.WARNING):
        tick(bot, client, clock)
    assert "Buying resumed" in caplog.text


def test_liquidity_filters(make_bot, client, clock, caplog):
    bot = make_bot(MAX_COINS=5, MAX_SPREAD=0.5, MIN_24H_VOLUME=1_000_000)
    client.volumes["ETHUSDT"] = 10_000
    tick(bot, client, clock, BTCUSDT=100, ETHUSDT=100, BNBUSDT=100)
    client.spread = 0.01  # 1% spread on everything
    with caplog.at_level(logging.INFO):
        tick(bot, client, clock, BTCUSDT=105)
    assert "spread of 1.00% is above MAX_SPREAD" in caplog.text
    client.spread = 0.0002
    clock.advance(120)  # past the detection cool-off
    tick(bot, client, clock, BTCUSDT=105, ETHUSDT=100, BNBUSDT=100)
    tick(bot, client, clock, BTCUSDT=110, ETHUSDT=105, BNBUSDT=105)
    assert set(bot.portfolio.positions) == {"BTCUSDT", "BNBUSDT"}  # ETH volume too low


def test_live_mode_places_real_orders_and_sells_what_is_on_the_account(write_config, clock, workdir):
    from fakes import FakeClient

    client = FakeClient(commission_in_base=True)
    bot = Bot(
        load_config(write_config(script={"TEST_MODE": False})), client, clock=clock, signals_dir=workdir / "signals"
    )
    assert bot.config.portfolio_file == "coins_bought.json"
    tick(bot, client, clock, XRPUSDT=0.5)
    tick(bot, client, clock, XRPUSDT=0.52)
    position = bot.portfolio.get("XRPUSDT")
    assert position.volume == pytest.approx(27.972)  # 15 USDT buys 28 XRP, minus 0.1% commission paid in XRP
    tick(bot, client, clock, XRPUSDT=0.49)
    assert "XRPUSDT" not in bot.portfolio
    assert [o["side"] for o in client.orders] == ["BUY", "SELL"]
    assert client.orders[0]["params"]["quoteOrderQty"] == "15"
    assert client.orders[1]["params"]["quantity"] == "27"  # never more than the account holds


def test_failed_sell_is_retried_and_dust_is_dropped(make_bot, client, clock, workdir, caplog):
    bot = make_bot()
    tick(bot, client, clock, BTCUSDT=100)
    tick(bot, client, clock, BTCUSDT=104)
    bot.portfolio.get("BTCUSDT").volume = 0.00001  # worth 0.001 USDT, below the minimum order
    with caplog.at_level(logging.WARNING):
        tick(bot, client, clock, BTCUSDT=90)
    assert "Removing BTCUSDT from the portfolio" in caplog.text
    assert "BTCUSDT" not in Portfolio.load(workdir / "test_coins_bought.json")


def test_portfolio_survives_a_restart(make_bot, client, clock):
    bot = make_bot()
    tick(bot, client, clock, BTCUSDT=100)
    tick(bot, client, clock, BTCUSDT=104)
    restarted = make_bot()
    assert "BTCUSDT" in restarted.portfolio
    tick(restarted, client, clock, BTCUSDT=99)  # stop loss still enforced after restart
    assert "BTCUSDT" not in restarted.portfolio


def test_max_coins_minus_one_finishes_when_everything_is_sold(make_bot, client, clock):
    bot = make_bot(MAX_COINS=-1)
    tick(bot, client, clock, BTCUSDT=100)
    assert bot.finished


def test_network_errors_do_not_stop_the_loop(make_bot, client, clock, caplog):
    bot = make_bot()
    calls = {"n": 0}
    real = client.get_orderbook_tickers

    def flaky(**params):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("connection reset")
        bot.stop_event.set()
        return real()

    client.get_orderbook_tickers = flaky
    bot.config = bot.config.__class__(**{**bot.config.__dict__, "recheck_interval": 600})  # 0.1s ticks
    with caplog.at_level(logging.WARNING):
        bot.run()
    assert "Network error talking to Binance" in caplog.text
    assert calls["n"] == 2


def test_config_is_reloaded_while_running(make_bot, client, clock, workdir, caplog):
    bot = make_bot()
    path = workdir / "config.yml"
    data = yaml.safe_load(path.read_text())
    data["trading_options"]["TAKE_PROFIT"] = 5
    data["script_options"]["TEST_MODE"] = False
    path.write_text(yaml.safe_dump(data))
    os.utime(path, (time.time() + 5, time.time() + 5))
    with caplog.at_level(logging.INFO):
        bot.reload_config()
    assert bot.config.take_profit == 5
    assert bot.config.test_mode is True
    assert "TEST_MODE changed" in caplog.text

    path.write_text("trading_options: {STOP_LOSS: 5}")
    os.utime(path, (time.time() + 10, time.time() + 10))
    with caplog.at_level(logging.ERROR):
        bot.reload_config()
    assert bot.config.take_profit == 5
    assert "Keeping the current settings" in caplog.text


def test_sell_all(make_bot, client, clock, workdir):
    bot = make_bot(MAX_COINS=5)
    tick(bot, client, clock, BTCUSDT=100, ETHUSDT=100)
    tick(bot, client, clock, BTCUSDT=104, ETHUSDT=104)
    assert len(bot.portfolio) == 2
    assert bot.sell_all()
    assert len(bot.portfolio) == 0
    assert json.loads((workdir / "test_coins_bought.json").read_text()) == {}


def test_report_and_unknown_tickers(make_bot, client, clock, caplog):
    bot = make_bot()
    with caplog.at_level(logging.INFO):
        bot.warn_unknown_tickers()
        tick(bot, client, clock, BTCUSDT=100)
        tick(bot, client, clock, BTCUSDT=104)
        bot.report(bot.exchange.quotes())
    assert "have no tradable USDT pair" in caplog.text and "OLD" in caplog.text and "USDC" in caplog.text
    assert "Test mode" in caplog.text
    assert "BTCUSDT" in caplog.text and "Positions" in caplog.text
