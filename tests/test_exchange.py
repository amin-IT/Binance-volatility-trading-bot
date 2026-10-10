from decimal import Decimal

import pytest
import requests
from fakes import APIError, FakeClient, symbol_info

from bvt.exchange import (
    DustError,
    Exchange,
    ExchangeError,
    LiveBroker,
    PaperBroker,
    Quote,
    SymbolRules,
    format_decimal,
    parse_order,
    round_step,
)


def test_round_step_and_format():
    assert round_step(Decimal("1.23456"), Decimal("0.001")) == Decimal("1.234")
    assert round_step(Decimal("7.9"), Decimal("1")) == Decimal("7")
    assert round_step(Decimal("0.5"), Decimal("0")) == Decimal("0.5")
    assert format_decimal(Decimal("1.2300")) == "1.23"
    assert format_decimal(Decimal("1E-7")) == "0.0000001"
    assert format_decimal(Decimal("100")) == "100"


def test_rules_are_read_by_filter_type_not_position():
    rules = SymbolRules.from_info(symbol_info("BTCUSDT", "BTC", step="0.00001", min_qty="0.0001", min_notional="5"))
    assert rules.step_size == Decimal("0.00001")
    assert rules.min_qty == Decimal("0.0001")
    assert rules.min_notional == Decimal("5")
    assert rules.tradable


def test_legacy_min_notional_filter_and_status():
    info = symbol_info("XUSDT", "X", status="BREAK")
    info["filters"] = [{"filterType": "MIN_NOTIONAL", "minNotional": "10", "applyToMarket": True}]
    rules = SymbolRules.from_info(info)
    assert rules.min_notional == Decimal("10")
    assert not rules.tradable


def test_quote_mid_and_spread():
    quote = Quote(99, 101)
    assert quote.mid == 100
    assert quote.spread == pytest.approx(2.0)


def test_quotes_skip_empty_books():
    client = FakeClient()
    exchange = Exchange(client)
    client.get_orderbook_tickers = lambda: [
        {"symbol": "AUSDT", "bidPrice": "0", "askPrice": "0"},
        {"symbol": "BUSDT", "bidPrice": "1.0", "askPrice": "1.1"},
    ]
    assert set(exchange.quotes()) == {"BUSDT"}


def test_rules_and_volumes_are_cached_and_survive_errors():
    client = FakeClient()
    now = [0.0]
    exchange = Exchange(client, clock=lambda: now[0])
    assert "BTCUSDT" in exchange.rules()
    assert exchange.volumes_24h()["BTCUSDT"] == 50_000_000

    def broken():
        raise requests.ConnectionError("down")

    client.get_exchange_info = broken
    client.get_ticker = broken
    now[0] = 10_000
    assert "BTCUSDT" in exchange.rules()  # stale copy kept
    assert "BTCUSDT" in exchange.volumes_24h()


def test_paper_broker_fills_at_given_price_with_step_rounding():
    broker = PaperBroker(Exchange(FakeClient()))
    fill = broker.buy("XRPUSDT", 15, 0.7)
    assert fill.quantity == Decimal("21")  # step size 1
    assert fill.price == 0.7
    with pytest.raises(ExchangeError, match="minimum order value"):
        broker.buy("XRPUSDT", 1, 0.7)
    with pytest.raises(ExchangeError, match="not available"):
        broker.buy("OLDUSDT", 15, 1)
    with pytest.raises(DustError):
        broker.sell("XRPUSDT", Decimal("2"), 0.7)  # 1.4 USDT is below the 5 USDT minimum


def test_live_buy_spends_quote_amount_and_deducts_commission_in_coin():
    client = FakeClient(commission_in_base=True, spread=0)
    client.set_price("XRPUSDT", 0.5)
    fill = LiveBroker(Exchange(client)).buy("XRPUSDT", 15, 0.5)
    params = client.orders[-1]["params"]
    assert params["quoteOrderQty"] == "15"
    assert params["newClientOrderId"].startswith("bvt-")
    assert fill.quantity == Decimal("30")
    assert fill.net_quantity == Decimal("29.97")
    assert fill.price == pytest.approx(0.5)


def test_live_sell_never_exceeds_the_free_balance():
    client = FakeClient(commission_in_base=True, spread=0)
    client.set_price("XRPUSDT", 0.5)
    broker = LiveBroker(Exchange(client))
    broker.buy("XRPUSDT", 15, 0.5)
    # the portfolio might believe we hold the gross 30; only 29.97 is on the account
    fill = broker.sell("XRPUSDT", Decimal("30"), 0.5)
    assert fill.quantity == Decimal("29")  # rounded down to the step size


def test_live_sell_of_a_coin_no_longer_on_the_account_is_dust():
    broker = LiveBroker(Exchange(FakeClient()))
    with pytest.raises(DustError):
        broker.sell("ETHUSDT", Decimal("1"), 2000)


def test_live_order_rejection_is_reported():
    client = FakeClient()
    client.fail_next_order = APIError(-2010, "Account has insufficient balance")
    with pytest.raises(ExchangeError, match="insufficient balance"):
        LiveBroker(Exchange(client)).buy("BTCUSDT", 15, 1)


def test_live_order_timeout_is_resolved_by_client_order_id(monkeypatch):
    monkeypatch.setattr("bvt.exchange.time.sleep", lambda s: None)
    client = FakeClient(spread=0)
    client.set_price("BTCUSDT", 100)
    real_create = client.create_order

    def create_then_time_out(**params):
        real_create(**params)  # the order executes...
        raise requests.ReadTimeout("...but the response is lost")

    client.create_order = create_then_time_out
    fill = LiveBroker(Exchange(client)).buy("BTCUSDT", 15, 100)
    assert fill.quantity == Decimal("0.15")
    assert fill.price == pytest.approx(100)


def test_live_order_that_never_arrived(monkeypatch):
    monkeypatch.setattr("bvt.exchange.time.sleep", lambda s: None)
    client = FakeClient()

    def time_out(**params):
        raise requests.ReadTimeout("lost before reaching Binance")

    client.create_order = time_out
    with pytest.raises(ExchangeError, match="was not placed"):
        LiveBroker(Exchange(client)).buy("BTCUSDT", 15, 1)


def test_unfilled_order_raises():
    rules = SymbolRules("AUSDT", "A", "USDT")
    with pytest.raises(ExchangeError, match="not filled"):
        parse_order({"orderId": 1, "status": "EXPIRED", "executedQty": "0"}, rules, "BUY", 1.0)
