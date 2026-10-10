"""An in-memory stand-in for ``binance.client.Client``."""

from __future__ import annotations

import itertools
import time
from decimal import Decimal

from binance.exceptions import BinanceAPIException


def symbol_info(symbol, base, quote="USDT", step="0.001", min_qty="0.001", min_notional="5", status="TRADING"):
    return {
        "symbol": symbol,
        "status": status,
        "baseAsset": base,
        "quoteAsset": quote,
        "quoteAssetPrecision": 8,
        "isSpotTradingAllowed": True,
        "quoteOrderQtyMarketAllowed": True,
        "orderTypes": ["LIMIT", "MARKET"],
        # deliberately not in the order older versions of the bot assumed
        "filters": [
            {"filterType": "PRICE_FILTER", "minPrice": "0.01", "maxPrice": "1000000", "tickSize": "0.01"},
            {"filterType": "PERCENT_PRICE_BY_SIDE"},
            {"filterType": "LOT_SIZE", "minQty": min_qty, "maxQty": "9000000", "stepSize": step},
            {"filterType": "NOTIONAL", "minNotional": min_notional, "applyMinToMarket": True},
            {"filterType": "MARKET_LOT_SIZE", "minQty": "0", "maxQty": "9000000", "stepSize": "0"},
        ],
    }


class APIError(BinanceAPIException):
    def __init__(self, code, message, status_code=400):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.response = None
        self.request = None


class FakeClient:
    """Prices are set with :meth:`set_price`; market orders fill at the ask (buy) or bid (sell)."""

    def __init__(self, symbols=None, spread=0.0002, commission_in_base=False):
        self.symbols = symbols or [
            symbol_info("BTCUSDT", "BTC", step="0.00001", min_qty="0.00001"),
            symbol_info("ETHUSDT", "ETH", step="0.0001", min_qty="0.0001"),
            symbol_info("BNBUSDT", "BNB"),
            symbol_info("XRPUSDT", "XRP", step="1", min_qty="1"),
            symbol_info("DOGEUSDT", "DOGE", step="1", min_qty="1"),
            symbol_info("ETHBTC", "ETH", quote="BTC"),
            symbol_info("USDCUSDT", "USDC", step="1", min_qty="1"),
            symbol_info("OLDUSDT", "OLD", status="BREAK"),
        ]
        self.spread = spread
        self.commission_in_base = commission_in_base
        self.prices = {s["symbol"]: 1.0 for s in self.symbols}
        self.volumes = {s["symbol"]: 50_000_000.0 for s in self.symbols}
        self.balances: dict[str, Decimal] = {"USDT": Decimal("1000")}
        self.orders: list[dict] = []
        self.timestamp_offset = 0
        self.fail_next_order: Exception | None = None
        self.klines: dict[tuple[str, str], list[list]] = {}
        self._ids = itertools.count(1)

    # --- market data
    def set_price(self, symbol, price):
        self.prices[symbol] = float(price)

    def ping(self):
        return {}

    def get_server_time(self):
        return {"serverTime": int(time.time() * 1000)}

    def get_exchange_info(self):
        return {"symbols": self.symbols}

    def _quote(self, symbol):
        price = self.prices[symbol]
        return price * (1 - self.spread / 2), price * (1 + self.spread / 2)

    def get_orderbook_tickers(self, **params):
        rows = []
        for symbol in self.prices:
            bid, ask = self._quote(symbol)
            rows.append(
                {"symbol": symbol, "bidPrice": f"{bid:.8f}", "askPrice": f"{ask:.8f}", "bidQty": "1", "askQty": "1"}
            )
        return rows

    def get_all_tickers(self):
        return [{"symbol": s, "price": f"{p:.8f}"} for s, p in self.prices.items()]

    def get_ticker(self, **params):
        return [{"symbol": s, "quoteVolume": str(v)} for s, v in self.volumes.items()]

    def get_klines(self, symbol, interval, limit=500, **params):
        return self.klines.get((symbol, interval), [])[-limit:]

    # --- account
    def get_account(self, **params):
        return {"balances": [{"asset": a, "free": str(b), "locked": "0"} for a, b in self.balances.items()]}

    def get_asset_balance(self, asset=None, **params):
        if asset not in self.balances:
            return None
        return {"asset": asset, "free": str(self.balances[asset]), "locked": "0"}

    def _info(self, symbol):
        return next(s for s in self.symbols if s["symbol"] == symbol)

    def create_order(self, **params):
        if self.fail_next_order is not None:
            error, self.fail_next_order = self.fail_next_order, None
            raise error
        info = self._info(params["symbol"])
        base, quote = info["baseAsset"], info["quoteAsset"]
        step = Decimal(next(f for f in info["filters"] if f["filterType"] == "LOT_SIZE")["stepSize"])
        bid, ask = self._quote(params["symbol"])
        if params["side"] == "BUY":
            price = Decimal(str(ask))
            if "quoteOrderQty" in params:
                qty = (Decimal(params["quoteOrderQty"]) / price // step) * step
            else:
                qty = Decimal(params["quantity"])
        else:
            price = Decimal(str(bid))
            qty = Decimal(params["quantity"])
            if qty > self.balances.get(base, Decimal("0")):
                raise APIError(-2010, "Account has insufficient balance for requested action.")
        commission = (qty * Decimal("0.001")) if self.commission_in_base and params["side"] == "BUY" else Decimal("0")
        if params["side"] == "BUY":
            self.balances[base] = self.balances.get(base, Decimal("0")) + qty - commission
            self.balances[quote] = self.balances.get(quote, Decimal("0")) - qty * price
        else:
            self.balances[base] -= qty
            self.balances[quote] = self.balances.get(quote, Decimal("0")) + qty * price
        order = {
            "symbol": params["symbol"],
            "orderId": next(self._ids),
            "clientOrderId": params.get("newClientOrderId"),
            "transactTime": int(time.time() * 1000),
            "status": "FILLED",
            "side": params["side"],
            "executedQty": str(qty),
            "cummulativeQuoteQty": str(qty * price),
            "fills": [
                {
                    "price": str(price),
                    "qty": str(qty),
                    "commission": str(commission) if commission else "0.0001",
                    "commissionAsset": base if commission else "BNB",
                }
            ],
            "params": params,
        }
        self.orders.append(order)
        return order

    def get_order(self, symbol, origClientOrderId=None, **params):
        for order in self.orders:
            if order["clientOrderId"] == origClientOrderId:
                return {k: v for k, v in order.items() if k != "fills"}
        raise APIError(-2013, "Order does not exist.")
