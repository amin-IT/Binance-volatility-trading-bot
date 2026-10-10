# Binance Volatility Trading Bot

A Binance spot trading bot that watches price changes across coins, buys the ones
moving up fastest (or the ones flagged by signal modules) and sells them at your
take profit or stop loss.

> Everything below is configurable in `config.yml`. Example: the bot checks whether any
> coin has risen more than 3% in the last minute, buys 15 USDT of it, and sells at a 6%
> profit or a 3% loss.

## Read before use

1. **`TEST_MODE: True` (the default) simulates trades** with live prices. With
   `TEST_MODE: False` the bot trades **real money**. Always check this setting.
2. This is a framework to adapt to your own strategy, not a turn-key money maker.
   Depending on the market the default settings may do very little, or lose money.
3. Run it in test mode (or on the Binance Spot Testnet) until you understand how your
   settings behave.

## Quick start

Requires Python 3.10 or newer.

```bash
git clone https://github.com/amin-IT/Binance-volatility-trading-bot.git
cd Binance-volatility-trading-bot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python Binance_Detect_Moonings.py  # test mode: no API keys needed
```

Run the bot from its folder: `config.yml`, the tickers list and `signals/` are looked
up in the current directory. Stop it with **Ctrl-C**; it then offers to sell the coins it
holds. To sell everything later: `python sell-remaining-coins.py`.

Command line options (both scripts): `--config/-c FILE`, `--creds/-u FILE`, `--debug/-d`;
the bot also has `--notimeout` (skip the 10 second delay before live trading) and
`--version`; `sell-remaining-coins.py` has `--yes` (no confirmation).

### Live trading

1. Create an API key on Binance with **Enable Spot & Margin Trading** (never enable
   withdrawals) and preferably restrict it to your IP address.
2. `cp creds.example.yml creds.yml` and fill in the `prod` section, or set the
   `BINANCE_API_KEY` and `BINANCE_API_SECRET` environment variables.
3. Set `TEST_MODE: False` in `config.yml`. US residents with a Binance.US account also set
   `AMERICAN_USER: True`.
4. Keep `MAX_COINS × QUANTITY` of `PAIR_WITH` (e.g. USDT) in your spot wallet, plus some
   BNB if you pay fees with BNB.

To test the real order flow without real money, create keys on the
[Binance Spot Testnet](https://testnet.binance.vision), put them in the `testnet` section
of `creds.yml` and set `TEST_MODE: False` and `USE_TESTNET: True`.

## How it trades

Every `TIME_DIFFERENCE / RECHECK_INTERVAL` minutes (10 seconds by default) the bot:

1. **Reads the order book prices** (best bid / ask) of every coin.
2. **Manages held coins**, valuing them at the bid (the price a sale would get):
   * below `STOP_LOSS` → sell;
   * above `TAKE_PROFIT` → sell, or with `USE_TRAILING_STOP_LOSS` arm a trailing stop
     `TRAILING_STOP_LOSS` % below the price that follows the price up (it never moves down)
     and sell when the price falls back to it;
   * named in a sell signal from a module → sell.
3. **Looks for buys** (unless a pause signal is active or `MAX_COINS` are held):
   * coins whose price is `CHANGE_IN_PRICE` % above their lowest price of the last
     `TIME_DIFFERENCE` minutes, strongest first;
   * coins named in buy signals from the signal modules.

   Each buy must pass the liquidity filters `MAX_SPREAD` (bid/ask spread) and
   `MIN_24H_VOLUME`; illiquid coins can jump several % on a single trade and fill at bad
   prices. A coin is not bought again within `TIME_DIFFERENCE` minutes of selling it.
4. Prints a status report once per `TIME_DIFFERENCE`.

Each buy spends exactly `QUANTITY` of `PAIR_WITH`. Held coins are saved to
`coins_bought.json` (`test_coins_bought.json` in test mode) and picked up again after a
restart; trades are logged to `trades.txt`. Most settings can be changed while the bot
runs: save `config.yml` and the change is applied at the next check.

## Signal modules

Signal modules are small programs that run next to the bot, in their own processes, and
suggest coins to buy, coins to sell, or pause buying. Enable them in `config.yml`:

```yaml
SIGNALLING_MODULES:
  - pausebotmod
  - VolScan
```

| Module | What it does |
|---|---|
| `VolScan` | buys the coins with the biggest dips (or highest volatility) over a scanning period, once they start to rebound |
| `the_snail` | "buy the dips": coins far below their recent high whose MACD turns up |
| `custsignalmod` | TradingView oscillators and moving averages agree on BUY |
| `signalsamplemod` | enough TradingView BUY indicators on two timeframes |
| `rsi_mod` | rising RSI / Stochastic; also sells on bearish momentum |
| `rsi_stoch_signalmod_djcommie` | rising RSI with Stochastic confirmation |
| `ta_indicator_signalmod_firewatch` | TradingView BUY on 1m/5m/15m and not overbought; also sells |
| `pausebotmod` | pauses buying while BTC's moving averages lean SELL |
| `os_pausebot_MAv2` | pauses buying while BTC's or ETH's moving averages lean SELL |
| `amin_btc_pause` | pauses buying unless BTC looks healthy (TradingView, then moving-average trend) |

Each module's settings are the UPPERCASE constants at the top of its file in
`signal_modules/`.

### Writing your own

Put a Python file in `signal_modules/` (or in the bot's folder) with a `do_work()`
function that loops forever, and add its name to `SIGNALLING_MODULES`:

```python
from bvt import modkit
from bvt.signals import write_signals, set_paused

def do_work():
    config = modkit.bot_config()                 # the bot's config.yml
    while True:
        pairs = modkit.watchlist(config)         # e.g. ['BTCUSDT', 'ETHUSDT', ...]
        picked = [...]                           # your logic
        write_signals("mymodule", picked)        # buy signals -> signals/mymodule.exs
        write_signals("mymodule", [...], kind="sell")
        set_paused("mymodule", False)            # True pauses buying
        modkit.sleep_minutes(1)
```

`bvt.modkit` also provides `public_client()` (Binance market data), `klines()`,
`tradingview_analysis()` (batched TradingView requests) and `held_symbols()`;
`bvt.indicators` has SMA, EMA, MACD and RSI.

The file protocol is plain text, so modules written for the original bot (`*.exs`,
`signals/paused.exc`) and for the OlorinSledge fork (`*.buy`, `*.sell`, `*.pause`) work
too; see [signals/README.md](signals/README.md).

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest        # runs offline against a fake exchange
ruff check . && ruff format --check .
```

The code lives in `bvt/`: `bot.py` (main loop), `strategy.py` (entry / exit rules),
`exchange.py` (prices, trading rules, orders), `portfolio.py`, `signals.py`, `runner.py`
(signal module processes) and `config.py`.

## Troubleshooting

Read the [FAQ](FAQ.md). Discord: https://discord.gg/7h2NRUayRw (`#troubleshooting`).
Please be kind: this is an open source project, not a full time job.

Further reading: [Binance volatility bot guide](https://www.cryptomaton.org/2021/05/08/how-to-code-a-binance-trading-bot-that-detects-the-most-volatile-coins-on-binance/),
[step by step](https://amin-it-consulting.com/binance-trading-bot-all-coins).

## Disclaimer

All investment strategies and investments involve risk of loss.
**Nothing contained in this program, scripts, code or repository should be construed as investment advice.**
Any reference to an investment's past or potential performance is not, and should not be
construed as, a recommendation or as a guarantee of any specific outcome or profit.
By using this program you accept all liabilities, and that no claims can be made against
the developers or others connected with the program.
