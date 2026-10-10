from decimal import Decimal

import pytest
import requests
from fakes import APIError, FakeClient, symbol_info

from bvt.exchange import (
    BelowMinimumError,
    DustError,
    Exchange,
    ExchangeError,
    LiveBroker,
    OrderStatusUnknown,
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
    with pytest.raises(BelowMinimumError):
        broker.sell("XRPUSDT", Decimal("2"), 0.7)  # 1.4 USDT is below the 5 USDT minimum: retry later
    with pytest.raises(DustError):
        broker.sell("XRPUSDT", Decimal("0.5"), 0.7)  # below the 1 XRP step: can never be sold


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
    with pytest.raises(ExchangeError, match="was not placed") as error:
        LiveBroker(Exchange(client)).buy("BTCUSDT", 15, 1)
    assert not isinstance(error.value, OrderStatusUnknown)


@pytest.mark.parametrize(
    "error", [APIError(-1, "Internal error", 503), APIError(-1007, "Timeout waiting for response")]
)
def test_server_error_after_execution_is_looked_up(monkeypatch, error):
    monkeypatch.setattr("bvt.exchange.time.sleep", lambda s: None)
    client = FakeClient(spread=0)
    client.set_price("BTCUSDT", 100)
    real_create = client.create_order

    def execute_then_fail(**params):
        real_create(**params)
        raise error

    client.create_order = execute_then_fail
    fill = LiveBroker(Exchange(client)).buy("BTCUSDT", 15, 100)
    assert fill.quantity == Decimal("0.15")


def test_lookup_retries_orders_still_in_flight(monkeypatch):
    monkeypatch.setattr("bvt.exchange.time.sleep", lambda s: None)
    client = FakeClient(spread=0)
    client.set_price("BTCUSDT", 100)
    real_create, real_get = client.create_order, client.get_order
    pending = []

    def lost_response(**params):
        pending.append(params)
        raise requests.ReadTimeout("no response")

    def get_order(**params):
        if pending:  # the first lookup does not see it yet, then it executes
            real_create(**pending.pop())
            raise APIError(-2013, "Order does not exist.")
        return real_get(**params)

    client.create_order, client.get_order = lost_response, get_order
    assert LiveBroker(Exchange(client)).buy("BTCUSDT", 15, 100).quantity == Decimal("0.15")


def test_unconfirmable_order_is_reported_as_unknown(monkeypatch):
    monkeypatch.setattr("bvt.exchange.time.sleep", lambda s: None)
    client = FakeClient()

    def down(**params):
        raise requests.ConnectionError("down")

    client.create_order = client.get_order = down
    with pytest.raises(OrderStatusUnknown, match="Check your Binance order history"):
        LiveBroker(Exchange(client)).buy("BTCUSDT", 15, 1)


def test_clock_drift_is_resynced_and_the_request_retried(monkeypatch):
    client = FakeClient(spread=0)
    client.set_price("BTCUSDT", 100)
    client.balances["BTC"] = Decimal("1")
    synced = []
    monkeypatch.setattr("bvt.exchange.sync_time", lambda c: synced.append(c))
    client.fail_next_order = APIError(-1021, "Timestamp for this request is outside of the recvWindow.")
    fill = LiveBroker(Exchange(client)).sell("BTCUSDT", Decimal("0.5"), 100)
    assert synced == [client]
    assert fill.quantity == Decimal("0.5")


def test_network_error_reading_the_balance_is_an_exchange_error():
    client = FakeClient()

    def down(**params):
        raise requests.ConnectionError("down")

    client.get_asset_balance = down
    with pytest.raises(ExchangeError, match="balance"):
        LiveBroker(Exchange(client)).sell("BTCUSDT", Decimal("1"), 100)


def test_partial_fill_reports_the_unfilled_quantity():
    client = FakeClient(spread=0)
    client.set_price("ETHUSDT", 2000)
    client.balances["ETH"] = Decimal("1")
    client.sell_fill_ratio = Decimal("0.25")
    fill = LiveBroker(Exchange(client)).sell("ETHUSDT", Decimal("1"), 2000)
    assert fill.quantity == Decimal("0.25")
    assert fill.unfilled == Decimal("0.75")


def test_unfilled_order_raises():
    rules = SymbolRules("AUSDT", "A", "USDT")
    with pytest.raises(ExchangeError, match="not filled"):
        parse_order({"orderId": 1, "status": "EXPIRED", "executedQty": "0"}, rules, "BUY", 1.0)
