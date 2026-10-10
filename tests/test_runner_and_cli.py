import json
import signal
import sys
import textwrap
import time
from types import SimpleNamespace

import pytest
import yaml
from fakes import APIError, FakeClient

from bvt import bot as bot_module
from bvt import sell_all as sell_all_module
from bvt.credentials import Credentials, CredentialsError, load_credentials
from bvt.runner import SignalModuleError, SignalModuleRunner, find_signal_module, import_signal_module

# ----------------------------------------------------------------- runner


def test_module_lookup():
    assert find_signal_module("VolScan") == "signal_modules.VolScan"
    with pytest.raises(SignalModuleError, match="not found"):
        find_signal_module("does_not_exist")
    with pytest.raises(SignalModuleError, match="no do_work"):
        import_signal_module("json")  # importable, but not a signal module


def test_runner_starts_modules_in_child_processes(workdir, monkeypatch, write_config):
    config_path = write_config()
    (workdir / "bvt_test_module.py").write_text(
        textwrap.dedent(
            """
            import os, time
            from bvt.signals import write_signals

            def do_work():
                write_signals("bvt_test_module", ["BTCUSDT", os.environ["BVT_CONFIG"]])
                while True:
                    time.sleep(1)
            """
        )
    )
    monkeypatch.syspath_prepend(str(workdir))
    # the bot installs its own SIGTERM handler; forked modules must not inherit it, or
    # terminate() is ignored and the bot hangs on exit
    previous = signal.signal(signal.SIGTERM, lambda *_: None)
    runner = SignalModuleRunner(["bvt_test_module", "missing_module"], config_path)
    try:
        runner.start()
        signal_file = workdir / "signals" / "bvt_test_module.exs"
        deadline = time.monotonic() + 30
        while not signal_file.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert signal_file.read_text().split() == ["BTCUSDT", config_path.upper()]
        assert list(runner.processes) == ["bvt_test_module"]
        runner.check()
        process = runner.processes["bvt_test_module"]
        started = time.monotonic()
        runner.stop()
        assert time.monotonic() - started < 4, "terminate() must stop the module promptly"
        assert not process.is_alive()
    finally:
        runner.stop()
        signal.signal(signal.SIGTERM, previous)
    assert runner.processes == {}


# ------------------------------------------------------------ credentials


def test_credentials_from_env_and_file(workdir, monkeypatch):
    creds = workdir / "creds.yml"
    creds.write_text(
        yaml.safe_dump(
            {
                "prod": {"access_key": "prodkey", "secret_key": "prodsecret"},
                "testnet": {"access_key": "replace_me", "secret_key": "replace_me"},
            }
        )
    )
    assert load_credentials(creds) == Credentials("prodkey", "prodsecret")
    assert load_credentials(creds, testnet=True) is None  # placeholders
    assert load_credentials(workdir / "missing.yml") is None
    assert "prodsecret" not in repr(load_credentials(creds))
    monkeypatch.setenv("BINANCE_API_KEY", "envkey")
    monkeypatch.setenv("BINANCE_API_SECRET", "envsecret")
    assert load_credentials(creds) == Credentials("envkey", "envsecret")
    creds.write_text("prod: access_key:nospace")
    monkeypatch.delenv("BINANCE_API_KEY")
    with pytest.raises(CredentialsError, match="formatted incorrectly"):
        load_credentials(creds)


def test_live_mode_without_keys_stops_with_a_clear_message(write_config):
    path = write_config(script={"TEST_MODE": False})
    with pytest.raises(SystemExit) as exit_info:
        bot_module.main(["-c", path])
    assert exit_info.value.code == 1


def test_bad_api_key_is_explained(monkeypatch):
    from bvt.credentials import check_api_key

    client = FakeClient()

    def reject(**params):
        raise APIError(-2015, "Invalid API-key, IP, or permissions for action.", 401)

    client.get_account = reject
    with pytest.raises(CredentialsError, match="AMERICAN_USER"):
        check_api_key(client)


# -------------------------------------------------------------------- main


@pytest.fixture
def fake_connect(monkeypatch):
    client = FakeClient()
    client.set_price("BTCUSDT", 100)
    monkeypatch.setattr(bot_module, "connect", lambda config, creds: client)
    monkeypatch.setattr(sell_all_module, "connect", lambda config, creds: client)
    return client


def test_main_runs_in_test_mode(write_config, fake_connect, monkeypatch, workdir, capsys):
    path = write_config()
    (workdir / "signals" / "stale.exs").write_text("BTCUSDT\n")
    ticks = []

    def one_tick(self):
        ticks.append(self.tick())

    monkeypatch.setattr(bot_module.Bot, "run", one_tick)
    bot_module.main(["-c", path])
    assert len(ticks) == 1
    assert not (workdir / "signals" / "stale.exs").exists(), "stale signals are cleared at startup"
    assert "Test mode" in capsys.readouterr().out


def test_main_reports_a_corrupt_portfolio_file(write_config, fake_connect, workdir):
    path = write_config()
    (workdir / "test_coins_bought.json").write_text("{not json")
    with pytest.raises(SystemExit) as exit_info:
        bot_module.main(["-c", path])
    assert exit_info.value.code == 1


def test_main_rejects_invalid_config(write_config):
    path = write_config(trading={"STOP_LOSS": 3})
    with pytest.raises(SystemExit) as exit_info:
        bot_module.main(["-c", path])
    assert exit_info.value.code == 1


def test_interrupted_bot_offers_to_sell(write_config, fake_connect, monkeypatch, workdir):
    path = write_config()
    (workdir / "test_coins_bought.json").write_text(
        json.dumps(
            {
                "BTCUSDT": {
                    "symbol": "BTCUSDT",
                    "orderid": 1,
                    "timestamp": 0,
                    "bought_at": 90,
                    "volume": 0.1,
                    "take_profit": 50,
                    "stop_loss": -50,
                }
            }
        )
    )

    def interrupted(self):
        raise KeyboardInterrupt

    monkeypatch.setattr(bot_module.Bot, "run", interrupted)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda question: "y")
    bot_module.main(["-c", path])
    assert json.loads((workdir / "test_coins_bought.json").read_text()) == {}
    assert "Sell: 0.1 BTCUSDT" in (workdir / "trades.txt").read_text()


def test_sell_all_script(write_config, fake_connect, workdir, capsys):
    path = write_config()
    sell_all_module.main(["-c", path, "--yes"])
    assert "nothing to sell" in capsys.readouterr().out

    (workdir / "test_coins_bought.json").write_text(
        json.dumps(
            {
                "BTCUSDT": {
                    "symbol": "BTCUSDT",
                    "orderid": 1,
                    "timestamp": 0,
                    "bought_at": 90,
                    "volume": 0.1,
                    "take_profit": 1,
                    "stop_loss": -3,
                }
            }
        )
    )
    with pytest.raises(SystemExit) as exit_info:
        sell_all_module.main(["-c", path, "--yes"])
    assert exit_info.value.code == 0
    assert json.loads((workdir / "test_coins_bought.json").read_text()) == {}


# ------------------------------------------------------------------ legacy


def test_legacy_helpers_for_third_party_modules(workdir, write_config, monkeypatch):
    from helpers.handle_creds import load_correct_creds, test_api_key
    from helpers.parameters import load_config, parse_args

    path = write_config()
    monkeypatch.setenv("BVT_CONFIG", path)
    args = parse_args()
    assert args.config == path
    assert load_config(args.config)["trading_options"]["PAIR_WITH"] == "USDT"
    assert load_correct_creds({"prod": {"access_key": "k", "secret_key": "s"}}) == ("k", "s")
    assert test_api_key(FakeClient()) == (True, "API key validated successfully")
    with pytest.raises(SystemExit):
        load_config(workdir / "missing.yml")
