"""Market data, symbol trading rules and order execution (live or simulated)."""

from __future__ import annotations

import itertools
import logging
import time
import uuid
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any, Protocol

import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceRequestException

from bvt.credentials import sync_time

log = logging.getLogger(__name__)

BUY = "BUY"
SELL = "SELL"

# Errors worth retrying on the next tick rather than treating as bugs.
NETWORK_ERRORS: tuple[type[Exception], ...] = (requests.RequestException, BinanceRequestException)


class ExchangeError(Exception):
    """An order could not be placed or completed."""


class DustError(ExchangeError):
    """A position cannot be sold: less than the minimum order size is left on the account."""


class BelowMinimumError(ExchangeError):
    """A sale is currently worth less than the minimum order value; it may succeed later."""


class OrderStatusUnknown(ExchangeError):
    """An order was sent but Binance could not confirm whether it executed."""


# Binance: 5xx replies, -1006 and -1007 mean the order's execution status is unknown.
_STATUS_UNKNOWN_CODES = (-1006, -1007)


def round_step(quantity: Decimal, step: Decimal) -> Decimal:
    """Round ``quantity`` down to a multiple of the exchange ``step`` size."""
    if step <= 0:
        return quantity
    return (quantity // step) * step


def format_decimal(value: Decimal) -> str:
    """Plain decimal string without exponent or trailing zeros, as Binance expects."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    base_asset: str
    quote_asset: str
    status: str = "TRADING"
    spot_allowed: bool = True
    step_size: Decimal = Decimal("0")
    min_qty: Decimal = Decimal("0")
    min_notional: Decimal = Decimal("0")
    quote_precision: int = 8
    quote_order_qty_allowed: bool = True

    @property
    def tradable(self) -> bool:
        return self.status == "TRADING" and self.spot_allowed

    @classmethod
    def from_info(cls, info: dict[str, Any]) -> SymbolRules:
        """Parse one entry of ``exchangeInfo['symbols']``. Filters are looked up by type, not position."""
        filters = {f.get("filterType"): f for f in info.get("filters", [])}
        lot = filters.get("LOT_SIZE", {})
        market_lot = filters.get("MARKET_LOT_SIZE", {})
        # Market orders must satisfy both lot filters; the coarser step satisfies both.
        step = max(Decimal(lot.get("stepSize", "0")), Decimal(market_lot.get("stepSize", "0")))
        min_qty = max(Decimal(lot.get("minQty", "0")), Decimal(market_lot.get("minQty", "0")))

        min_notional = Decimal("0")
        if "NOTIONAL" in filters:
            notional = filters["NOTIONAL"]
            if notional.get("applyMinToMarket", True):
                min_notional = Decimal(notional.get("minNotional", "0"))
        elif "MIN_NOTIONAL" in filters:
            notional = filters["MIN_NOTIONAL"]
            if notional.get("applyToMarket", True):
                min_notional = Decimal(notional.get("minNotional", "0"))

        return cls(
            symbol=info["symbol"],
            base_asset=info.get("baseAsset", ""),
            quote_asset=info.get("quoteAsset", ""),
            status=info.get("status", "TRADING"),
            spot_allowed=info.get("isSpotTradingAllowed", True),
            step_size=step,
            min_qty=min_qty,
            min_notional=min_notional,
            quote_precision=int(info.get("quoteAssetPrecision", info.get("quotePrecision", 8))),
            quote_order_qty_allowed=info.get("quoteOrderQtyMarketAllowed", True),
        )


@dataclass(frozen=True)
class Quote:
    """Best bid and ask from the order book."""

    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        """Bid/ask spread as a percentage of the mid price."""
        return (self.ask - self.bid) / self.mid * 100


@dataclass(frozen=True)
class Fill:
    """Result of an executed market order."""

    symbol: str
    side: str
    order_id: int | str
    price: float  # average fill price
    quantity: Decimal  # executed quantity
    net_quantity: Decimal  # quantity actually received (commission paid in the coin deducted)
    timestamp: int  # milliseconds since epoch
    requested: Decimal | None = None  # quantity asked for, when known (a market order can fill partially)

    @property
    def unfilled(self) -> Decimal:
        return max((self.requested or self.quantity) - self.quantity, Decimal("0"))


class Exchange:
    """Public market data plus a cached copy of the trading rules for every symbol."""

    RULES_TTL = 3600.0
    VOLUME_TTL = 600.0

    def __init__(self, client: Client, clock=time.monotonic) -> None:
        self.client = client
        self._clock = clock
        self._rules: dict[str, SymbolRules] = {}
        self._rules_loaded_at: float | None = None
        self._volumes: dict[str, float] = {}
        self._volumes_loaded_at: float | None = None

    def quotes(self) -> dict[str, Quote]:
        """Best bid/ask for every symbol that currently has orders on both sides."""
        quotes = {}
        for ticker in self.client.get_orderbook_tickers():
            try:
                bid, ask = float(ticker["bidPrice"]), float(ticker["askPrice"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 < bid <= ask:
                quotes[ticker["symbol"]] = Quote(bid, ask)
        return quotes

    def volumes_24h(self) -> dict[str, float]:
        """24h traded volume of every symbol in its quote asset, refreshed every 10 minutes."""
        stale = self._volumes_loaded_at is None or self._clock() - self._volumes_loaded_at > self.VOLUME_TTL
        if stale:
            try:
                tickers = self.client.get_ticker()
            except (BinanceAPIException, *NETWORK_ERRORS) as exc:
                if not self._volumes:
                    raise
                log.warning("Could not refresh 24h volumes, using cached values: %s", exc)
            else:
                volumes = {}
                for ticker in tickers:
                    try:
                        volumes[ticker["symbol"]] = float(ticker["quoteVolume"])
                    except (KeyError, TypeError, ValueError):
                        continue
                self._volumes = volumes
            self._volumes_loaded_at = self._clock()
        return self._volumes

    def rules(self) -> dict[str, SymbolRules]:
        """Trading rules for every symbol, refreshed hourly (the last good copy is kept on errors)."""
        stale = self._rules_loaded_at is None or self._clock() - self._rules_loaded_at > self.RULES_TTL
        if stale:
            try:
                info = self.client.get_exchange_info()
            except (BinanceAPIException, *NETWORK_ERRORS) as exc:
                if not self._rules:
                    raise
                log.warning("Could not refresh exchange info, using cached rules: %s", exc)
            else:
                self._rules = {s["symbol"]: SymbolRules.from_info(s) for s in info.get("symbols", [])}
            self._rules_loaded_at = self._clock()
        return self._rules

    def rule(self, symbol: str) -> SymbolRules | None:
        return self.rules().get(symbol)


class Broker(Protocol):
    def buy(self, symbol: str, quote_amount: float, last_price: float) -> Fill: ...

    def sell(self, symbol: str, quantity: Decimal, last_price: float) -> Fill: ...


def _require_rule(exchange: Exchange, symbol: str) -> SymbolRules:
    rule = exchange.rule(symbol)
    if rule is None or not rule.tradable:
        raise ExchangeError(f"{symbol} is not available for spot trading")
    return rule


def sellable(rule: SymbolRules, quantity: Decimal, last_price: float) -> bool:
    """Whether ``quantity`` can currently be sold with a market order."""
    qty = round_step(quantity, rule.step_size)
    return qty > 0 and qty >= rule.min_qty and qty * Decimal(str(last_price)) >= rule.min_notional


def _check_sell_quantity(rule: SymbolRules, quantity: Decimal, last_price: float) -> Decimal:
    qty = round_step(quantity, rule.step_size)
    if qty <= 0 or qty < rule.min_qty:
        raise DustError(f"{format_decimal(qty)} {rule.base_asset} is below the minimum order size")
    if qty * Decimal(str(last_price)) < rule.min_notional:
        # Binance checks the notional against a 5 minute average price, so a sudden wick can make
        # this check fail while the exchange would still accept the order later: keep retrying.
        raise BelowMinimumError(
            f"{format_decimal(qty)} {rule.base_asset} is worth less than the minimum order value "
            f"of {format_decimal(rule.min_notional)} {rule.quote_asset} at the current price"
        )
    return qty


class PaperBroker:
    """Simulates fills at the latest price. Used in TEST_MODE: no orders reach Binance."""

    def __init__(self, exchange: Exchange) -> None:
        self.exchange = exchange
        self._ids = itertools.count(1)

    def buy(self, symbol: str, quote_amount: float, last_price: float) -> Fill:
        rule = _require_rule(self.exchange, symbol)
        if last_price <= 0:
            raise ExchangeError(f"no valid price for {symbol}")
        if Decimal(str(quote_amount)) < rule.min_notional:
            raise ExchangeError(
                f"QUANTITY {quote_amount} is below the minimum order value of "
                f"{format_decimal(rule.min_notional)} {rule.quote_asset} for {symbol}"
            )
        qty = round_step(Decimal(str(quote_amount)) / Decimal(str(last_price)), rule.step_size)
        if qty <= 0 or qty < rule.min_qty:
            raise ExchangeError(f"QUANTITY {quote_amount} buys less than the minimum order size of {symbol}")
        return self._fill(symbol, BUY, qty, last_price)

    def sell(self, symbol: str, quantity: Decimal, last_price: float) -> Fill:
        rule = self.exchange.rule(symbol)
        qty = _check_sell_quantity(rule, quantity, last_price) if rule else quantity
        return self._fill(symbol, SELL, qty, last_price)

    def _fill(self, symbol: str, side: str, qty: Decimal, price: float) -> Fill:
        return Fill(symbol, side, f"paper-{next(self._ids)}", price, qty, qty, int(time.time() * 1000))


class LiveBroker:
    """Places real market orders (on Binance or the Spot Testnet, depending on the client)."""

    def __init__(self, exchange: Exchange) -> None:
        self.exchange = exchange
        self.client = exchange.client

    def buy(self, symbol: str, quote_amount: float, last_price: float) -> Fill:
        rule = _require_rule(self.exchange, symbol)
        amount = Decimal(str(quote_amount)).quantize(Decimal(1).scaleb(-rule.quote_precision), ROUND_DOWN)
        if amount < rule.min_notional:
            raise ExchangeError(
                f"QUANTITY {quote_amount} is below the minimum order value of "
                f"{format_decimal(rule.min_notional)} {rule.quote_asset} for {symbol}"
            )
        params: dict[str, Any] = {"symbol": symbol, "side": BUY, "type": "MARKET"}
        if rule.quote_order_qty_allowed:
            # spend exactly QUANTITY of the quote asset; Binance works out the coin amount
            params["quoteOrderQty"] = format_decimal(amount)
        else:
            qty = round_step(amount / Decimal(str(last_price)), rule.step_size)
            if qty <= 0 or qty < rule.min_qty:
                raise ExchangeError(f"QUANTITY {quote_amount} buys less than the minimum order size of {symbol}")
            params["quantity"] = format_decimal(qty)
        return self._place(params, rule, last_price)

    def sell(self, symbol: str, quantity: Decimal, last_price: float) -> Fill:
        rule = _require_rule(self.exchange, symbol)
        try:
            balance = self._signed(self.client.get_asset_balance, asset=rule.base_asset)
        except (BinanceAPIException, *NETWORK_ERRORS) as exc:
            raise ExchangeError(f"could not read the {rule.base_asset} balance: {exc}") from None
        free = Decimal(str((balance or {}).get("free") or "0"))
        # Never try to sell more than we own: fees paid in the coin, or manual trades, reduce the balance.
        qty = _check_sell_quantity(rule, min(quantity, free), last_price)
        params = {"symbol": symbol, "side": SELL, "type": "MARKET", "quantity": format_decimal(qty)}
        return self._place(params, rule, last_price)

    def _signed(self, method, **params):
        """Call a signed endpoint; if the clock drifted (-1021), resync it and retry once."""
        try:
            return method(**params)
        except BinanceAPIException as exc:
            if exc.code != -1021:
                raise
            log.warning("Binance rejected the request time (%s); resyncing the clock and retrying", exc.message)
            sync_time(self.client)
            return method(**params)

    def _place(self, params: dict[str, Any], rule: SymbolRules, last_price: float) -> Fill:
        client_order_id = f"bvt-{uuid.uuid4().hex[:24]}"
        params = {**params, "newClientOrderId": client_order_id, "newOrderRespType": "FULL"}
        side = params["side"]
        try:
            order = self._signed(self.client.create_order, **params)
        except BinanceAPIException as exc:
            if (exc.status_code or 0) < 500 and exc.code not in _STATUS_UNKNOWN_CODES:
                raise ExchangeError(f"{side} {rule.symbol} rejected: {exc}") from None
            # The order may still have executed; ask Binance before giving up.
            log.warning("Execution status of %s %s unknown (%s); checking order status", side, rule.symbol, exc)
            order = self._lookup(rule.symbol, client_order_id)
        except NETWORK_ERRORS as exc:
            log.warning("No response while placing %s %s (%s); checking order status", side, rule.symbol, exc)
            order = self._lookup(rule.symbol, client_order_id)
        return parse_order(order, rule, side, last_price)

    def _lookup(self, symbol: str, client_order_id: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(5):
            time.sleep(1 + attempt)
            try:
                return self._signed(self.client.get_order, symbol=symbol, origClientOrderId=client_order_id)
            except BinanceAPIException as exc:
                last_error = exc  # -2013 "order does not exist" may mean it is still in flight: ask again
            except NETWORK_ERRORS as exc:
                last_error = exc
        if isinstance(last_error, BinanceAPIException) and last_error.code == -2013:
            raise ExchangeError(f"order for {symbol} was not placed")
        raise OrderStatusUnknown(
            f"could not confirm order {client_order_id} for {symbol} ({last_error}). "
            "Check your Binance order history; if it was filled, sell or record the coin yourself."
        )


def parse_order(order: dict[str, Any], rule: SymbolRules, side: str, fallback_price: float) -> Fill:
    executed = Decimal(str(order.get("executedQty") or "0"))
    if executed <= 0:
        raise ExchangeError(
            f"{side} order {order.get('orderId')} for {rule.symbol} was not filled (status {order.get('status')})"
        )
    quote = Decimal(str(order.get("cummulativeQuoteQty") or "0"))
    price = float(quote / executed) if quote > 0 else fallback_price
    net = executed
    if side == BUY:
        net -= sum(
            (
                Decimal(str(f.get("commission", "0")))
                for f in order.get("fills", [])
                if f.get("commissionAsset") == rule.base_asset
            ),
            Decimal("0"),
        )
    timestamp = order.get("transactTime") or order.get("updateTime") or order.get("time") or int(time.time() * 1000)
    requested = Decimal(str(order["origQty"])) if order.get("origQty") else None
    return Fill(rule.symbol, side, order.get("orderId", ""), price, executed, net, int(timestamp), requested)
