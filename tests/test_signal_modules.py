"""Signal module logic, plus one full do_work() iteration of every module against fakes."""

import importlib
import pkgutil
import time
from types import SimpleNamespace

import pytest
from fakes import FakeClient

import signal_modules
from bvt import modkit
from bvt.config import load_config
from bvt.modkit import Candle

MODULES = sorted(m.name for m in pkgutil.iter_modules(signal_modules.__path__))


def analysis(recommendation="BUY", buys=20, ma_sell=2, ma_buy=12, compute="BUY", **indicators):
    values = {
        "RSI": 55.0,
        "RSI[1]": 50.0,
        "Stoch.K": 60.0,
        "Stoch.D": 40.0,
        "Stoch.K[1]": 50.0,
        "Stoch.RSI.K": 40.0,
        "EMA10": 3.0,
        "EMA20": 2.0,
        "EMA100": 1.0,
    }
    values.update(indicators)
    return SimpleNamespace(
        summary={"RECOMMENDATION": recommendation, "BUY": buys, "SELL": 26 - buys, "NEUTRAL": 0},
        oscillators={"COMPUTE": {k: compute for k in ("MACD", "Stoch.RSI", "Mom", "RSI", "STOCH.K", "CCI")}},
        moving_averages={
            "COMPUTE": {k: compute for k in ("EMA10", "EMA20", "SMA10", "SMA20")},
            "SELL": ma_sell,
            "BUY": ma_buy,
            "NEUTRAL": 15 - ma_sell - ma_buy,
        },
        indicators=values,
    )


def test_every_module_imports_without_network_and_has_do_work():
    assert len(MODULES) == 10
    for name in MODULES:
        module = importlib.import_module(f"signal_modules.{name}")
        assert callable(module.do_work), name


# ---------------------------------------------------------------- VolScan


def test_volscan_ranks_dips_that_started_to_rebound(monkeypatch):
    from signal_modules import VolScan

    history = {
        "AUSDT": [100, 95, 90, 92],  # fell 8%, bouncing
        "BUSDT": [100, 97, 96, 95],  # still making new lows
        "CUSDT": [100, 101, 103, 104],  # rising
        "DUSDT": [100, 99, 98, 98.5],  # small dip, bouncing
    }
    assert [c.symbol for c in VolScan.rank_coins(history)] == ["AUSDT", "DUSDT"]
    monkeypatch.setattr(VolScan, "REQUIRE_REBOUND", False)
    assert [c.symbol for c in VolScan.rank_coins(history)] == ["AUSDT", "BUSDT", "DUSDT"]
    monkeypatch.setattr(VolScan, "CREATE_LIST_BY_ONLY_COV", True)
    assert VolScan.rank_coins(history)[0].symbol == "AUSDT"  # most volatile
    monkeypatch.setattr(VolScan, "NUMBER_COINS_IN_LIST", 2)
    assert len(VolScan.rank_coins(history)) == 2


# ------------------------------------------------------------------ snail


def candles(highs_lows, close):
    return [Candle(0, close, h, low, close, 1.0) for h, low in highs_lows]


def test_snail_selects_deep_dips_with_enough_movement():
    from signal_modules import the_snail

    history = candles([(100, 90), (98, 80), (90, 75), (85, 74)], close=78)
    candidate = the_snail.evaluate("AUSDT", history, 78, take_profit=1.0)
    assert candidate is not None
    assert candidate.potential == pytest.approx(22.0)  # drop from the 100 high
    assert candidate.movement == pytest.approx(26 / 74 * 100)
    assert the_snail.evaluate("AUSDT", history, 95, take_profit=1.0) is None  # not far enough below the high
    assert the_snail.evaluate("AUSDT", history[:2], 78, take_profit=1.0) is None  # not enough history


def test_snail_movement_rule_rejects_coins_that_barely_move(monkeypatch):
    from signal_modules import the_snail

    monkeypatch.setattr(the_snail, "MOVEMENT", "MOVEMENT")
    monkeypatch.setattr(the_snail, "PROFIT_MIN", 0.1)
    flat = candles([(100.4, 100), (100.3, 100.1), (100.2, 100), (100.4, 100)], close=100.0)
    # range is 0.4% of the low: a 1% take profit is out of reach (v2.x scored this 250 and accepted it)
    assert the_snail.evaluate("AUSDT", flat, 100.0, take_profit=1.0) is None


def test_snail_macd_filter():
    from signal_modules import the_snail

    rising = [Candle(i, 0, 0, 0, 100 + 0.05 * i * i, 0) for i in range(36)]  # accelerating uptrend
    closes = [100 + i for i in range(30)] + [130 - 3 * i for i in range(6)]
    falling = [Candle(i, 0, 0, 0, close, 0) for i, close in enumerate(closes)]
    assert the_snail.macd_turning_up(rising)
    assert not the_snail.macd_turning_up(falling)
    assert not the_snail.macd_turning_up(rising[:20])


# -------------------------------------------------------- TradingView based


def test_pause_modules():
    from signal_modules import amin_btc_pause, os_pausebot_MAv2, pausebotmod

    assert pausebotmod.should_pause(analysis(ma_sell=14))
    assert not pausebotmod.should_pause(analysis(ma_sell=13))
    assert os_pausebot_MAv2.bearish(analysis(ma_sell=7, ma_buy=7))
    assert not os_pausebot_MAv2.bearish(analysis(ma_sell=3, ma_buy=7))
    assert amin_btc_pause.trend_is_up([100 + i * 0.1 for i in range(250)])
    assert not amin_btc_pause.trend_is_up([200 - i * 0.1 for i in range(250)])
    assert amin_btc_pause.trend_is_up([1.0] * 50) is None


def test_tradingview_buy_rules():
    from signal_modules import custsignalmod, rsi_mod, rsi_stoch_signalmod_djcommie, ta_indicator_signalmod_firewatch

    assert custsignalmod.is_buy(analysis())[0]
    assert not custsignalmod.is_buy(analysis(compute="SELL"))[0]

    reading = rsi_mod.read(analysis(), analysis())
    assert rsi_mod.is_buy(reading)
    falling = rsi_mod.read(
        analysis(buys=3, **{"RSI": 40, "Stoch.K": 20, "Stoch.D": 40, "Stoch.K[1]": 30}), analysis(buys=3)
    )
    assert rsi_mod.is_sell(falling) and not rsi_mod.is_buy(falling)
    assert rsi_mod.read(SimpleNamespace(indicators={}), analysis()) is None

    assert rsi_stoch_signalmod_djcommie.is_buy(analysis(), last_rsi=52.0)
    assert not rsi_stoch_signalmod_djcommie.is_buy(analysis(), last_rsi=None)  # needs a previous reading
    assert not rsi_stoch_signalmod_djcommie.is_buy(analysis(), last_rsi=54.0)  # RSI rose < 2.5

    fw = ta_indicator_signalmod_firewatch
    assert fw.classify([analysis()] * 3) == "buy"
    assert fw.classify([analysis(RSI=80)] * 3) is None  # overbought
    assert fw.classify([analysis("STRONG_SELL")] * 3) == "sell"
    assert fw.classify([analysis(), analysis("SELL"), analysis()]) is None


# ------------------------------------------------- one iteration of do_work


class _Stop(Exception):
    pass


def _stop(*args, **kwargs):
    raise _Stop


@pytest.mark.parametrize("name", MODULES)
def test_do_work_runs_one_iteration(name, write_config, workdir, monkeypatch):
    config = load_config(write_config())
    client = FakeClient()
    # 1m candles for the BTC trend check and MACD filters, daily candles for the snail
    rising = [[i, "1", str(101 + i * 0.01), str(99 + i * 0.01), str(100 + i * 0.01), "1"] for i in range(250)]
    for symbol in ("BTCUSDT", "ETHUSDT", "XRPUSDT"):
        for interval in ("1m", "5m", "15m", "1h", "4h"):
            client.klines[(symbol, interval)] = rising
        client.klines[(symbol, "1d")] = [[i, "1", "2.0", "0.5", "1.0", "1"] for i in range(4)]
    client.set_price("XRPUSDT", 0.7)

    monkeypatch.setattr(modkit, "bot_config", lambda: config)
    monkeypatch.setattr(modkit, "public_client", lambda config=None: client)
    monkeypatch.setattr(
        modkit, "tradingview_analysis", lambda symbols, interval, timeout=20: {s: analysis() for s in symbols}
    )
    monkeypatch.setattr(modkit, "sleep_minutes", _stop)
    module = importlib.import_module(f"signal_modules.{name}")
    if name == "VolScan":
        monkeypatch.setattr(module, "SCANNING_PERIOD", 0.05)  # 3 quick scans
        monkeypatch.setattr(module, "TIME_SLEEP", 1)
        monkeypatch.setattr(time, "sleep", lambda s: None if s < 1 else _stop())
        monkeypatch.setattr(module.time, "sleep", lambda s: None if s < 1 else _stop())

    with pytest.raises(_Stop):
        module.do_work()
    signals = workdir / "signals"
    written = sorted(p.name for p in signals.iterdir())
    if name in ("pausebotmod", "os_pausebot_MAv2", "amin_btc_pause"):
        assert all(n.endswith(".pause") for n in written)
    elif name in ("custsignalmod", "signalsamplemod", "ta_indicator_signalmod_firewatch", "rsi_mod"):
        assert written, f"{name} should signal the fake all-BUY analyses"
        content = (signals / written[0]).read_text().split()
        # the watchlist comes from tickers.txt minus EX_PAIRS (USDC); the bot itself skips non-trading pairs
        assert set(content) <= {"BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "OLDUSDT"}
