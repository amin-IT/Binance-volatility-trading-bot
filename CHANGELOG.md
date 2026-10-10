# Changelog

## 2.0.0

A rewrite of the bot into a tested package (`bvt/`) that runs on current Python (3.10-3.14)
and current versions of its dependencies. You still start it with
`python Binance_Detect_Moonings.py`, and `config.yml` / `creds.yml` keep their format.

### Upgrading

* Check `config.yml`: **`TEST_MODE` now defaults to `True`**. Set it to `False` again to
  trade with real money.
* Reinstall the dependencies: `pip install -r requirements.txt`. pandas, pandas_ta, numpy,
  ccxt and aiohttp are no longer needed.
* Signal modules moved to `signal_modules/`. Their names in `SIGNALLING_MODULES` are
  unchanged, except: `the_snail_v2` / `snail+tradingview` → `the_snail`, `rsi-mod` →
  `rsi_mod`, `signalsample` → `signalsamplemod`. Modules you wrote yourself keep working
  from the bot's folder.
* `TICKERS_LIST` should point to `tickers.txt` (`tickers_all_USDT.txt` and
  `signalsample.txt` were identical copies and were removed).
* `FIATS` and `EX_PAIRS` entries now match a coin or pair exactly instead of anywhere in
  the name, so `UP` no longer excludes coins such as JUP or SUPER.

### Strategy changes

* **Momentum is measured from the window's low to the current price.** Previously the bot
  measured the window's low-to-high move, so it also bought coins whose spike had already
  fully reversed.
* **Liquidity filters** `MAX_SPREAD` and `MIN_24H_VOLUME` skip coins whose order book is
  too thin; they apply to signal module buys too. They are off unless set (the shipped
  config sets 0.5% and 1,000,000).
* **Prices come from the order book**: held coins are valued at the bid, so stop loss and
  take profit trigger on the price a sale would actually get, and test mode fills at the
  ask / bid instead of the last trade price.
* The **trailing stop loss never moves down** (it could drop when the take profit was reset
  every minute), and only untrailed positions follow edits to TAKE_PROFIT / STOP_LOSS.
* A coin is not bought again within TIME_DIFFERENCE minutes of being sold, including via
  signal modules.
* Signal modules: VolScan only signals dips that have started to rebound
  (`REQUIRE_REBOUND`); the snail's movement check now measures the price range as % of the
  low (it computed low / range, which grew as movement shrank); `amin_btc_pause` uses a
  20/50/200 moving-average trend instead of 2/3/4/5-minute averages; `signalsamplemod`'s
  threshold of 26/26 indicators never fired and is now 15; djcommie's EMA condition was
  always true and is now an explicit `REQUIRE_EMA_TREND` option; firewatch only analysed
  coins when `FULL_LOG` was on.

### Fixes

* Order quantities: trading rules are looked up by filter type (Binance reordered the
  filters, breaking `filters[2]`); buys spend exactly `QUANTITY` via `quoteOrderQty`; the
  minimum order value (`NOTIONAL`) is checked.
* Sells never exceed the coin balance, so fees paid in the coin no longer cause
  "insufficient balance" errors. Coins below the minimum order size are dropped from the
  portfolio instead of failing forever; positions only temporarily worth less than the
  minimum order value (e.g. during a price wick) are kept and retried.
* Partially filled sells keep the unsold remainder as a position.
* If an order request times out, or Binance answers with a server error, the bot asks
  Binance whether the order executed instead of losing track of it. A buy that cannot be
  confirmed is reported and that coin is not bought again until the bot restarts.
* Clock drift that develops while the bot runs is corrected automatically (error -1021).
* Real fill prices and commissions are recorded instead of the last ticker price.
* While paused the bot no longer hammers the Binance API in a busy loop.
* The bot no longer crashes when BNB is missing from the tickers list, when a coin is
  listed or delisted mid-window, when a signal names a coin outside the list, or when
  `TIME_DIFFERENCE` is not a whole number.
* `USDTBRL`-style pairs are no longer picked up when `CUSTOM_LIST: False`.
* Session profit is tracked even with `LOG_TRADES: False`; profit and win/loss figures
  include fees correctly.
* Ctrl-C finishes the current check before stopping (a second Ctrl-C forces it, but never
  while an order is being placed and recorded). The portfolio is written atomically and
  flushed to disk, and an unreadable portfolio file stops the bot at startup instead of
  silently forgetting coins. An error in one position no longer stops the others from being
  checked, and a config file saved with the wrong encoding no longer crashes the bot.
* Signal modules are stopped reliably on exit and restarted if they crash; stale signal
  files are cleared at startup; signal files are read atomically.
* Signal modules no longer create API clients or parse arguments at import, batch their
  TradingView requests (avoiding HTTP 429), and survive network errors.
* Removed the `bot/` and `modules/` folders (parts of another fork that could not run
  here), duplicate scripts, outdated ticker lists, `__pycache__` files and stale signal
  files from the repository.

### New

* `USE_TESTNET` for real orders on the Binance Spot Testnet.
* API keys can come from `BINANCE_API_KEY` / `BINANCE_API_SECRET`; test mode needs none.
* Clock drift compensation, clearer startup errors, live reload of `config.yml` with
  validation, Binance.US support for signal modules.
* Signal files from the OlorinSledge fork (`.buy`, `.sell`, `.pause`) are understood.
* Test suite (offline, fake exchange), ruff, and GitHub Actions CI.
