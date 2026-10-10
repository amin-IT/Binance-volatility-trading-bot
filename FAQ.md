# Frequently Asked Questions

## Errors and solutions

| Error | Solution |
|-------|----------|
| `APIError(code=-1021): Timestamp for this request was 1000ms ahead of the server's time` | The bot compensates for clock drift automatically, but a badly wrong clock still causes this. Sync your computer/server with an NTP server such as `pool.ntp.org`. |
| `APIError(code=-2015): Invalid API-key, IP, or permissions for action` | Check the keys in `creds.yml`, that the key has *Enable Spot & Margin Trading*, that your IP is allowed if the key is IP restricted, and set `AMERICAN_USER: True` if you use Binance.US. |
| `APIError(code=-1022): Signature for this request is not valid` | Your `secret_key` is wrong. |
| `Binance does not serve your location` / HTTP 451 | Binance blocks some countries (including the USA) from binance.com. US residents need Binance.US and `AMERICAN_USER: True`. |
| `No API keys found` | Live trading needs keys: copy `creds.example.yml` to `creds.yml` and fill it in (keep a space after each colon), or set `BINANCE_API_KEY` / `BINANCE_API_SECRET`. Test mode needs no keys. |
| `Account has insufficient balance` | Keep at least `MAX_COINS × QUANTITY` of `PAIR_WITH` free (not tied up in open orders), plus BNB for fees if you pay fees with BNB. |
| `QUANTITY ... is below the minimum order value` | Binance's minimum order is around 5-10 USDT depending on the coin. Use a `QUANTITY` of 15 or more. |
| `Removing X from the portfolio: ... below the minimum order size/value` | The coin is worth less than Binance's minimum order (or is no longer in your account), so it cannot be sold by the bot. Sell or convert the dust in the Binance app. |
| `N of M coins in tickers.txt have no tradable USDT pair` | Those coins were delisted or are excluded. Remove them from your tickers list. |
| `Signal module 'x' not found` | The name in `SIGNALLING_MODULES` must match a file in `signal_modules/` (or the bot folder) without `.py`. |
| Other Binance API errors | See the [Binance API error codes](https://developers.binance.com/docs/binance-spot-api-docs/errors). |

## Questions and answers

| Question | Answer |
|----------|--------|
| What funds do I need for live trading? | At least `QUANTITY × MAX_COINS` of `PAIR_WITH` (e.g. USDT). If you pay fees with BNB (enable it on Binance), keep a few dollars of BNB too and set `TRADING_FEE: 0.075`. |
| How do I test without risking money? | Keep `TEST_MODE: True` (simulated trades with live prices), or use the Binance Spot Testnet with `TEST_MODE: False` and `USE_TESTNET: True`. |
| Can I change settings while the bot runs? | Yes, save `config.yml`. TEST_MODE, USE_TESTNET, AMERICAN_USER, PAIR_WITH and SIGNALLING_MODULES need a restart. |
| How do I stop buying but keep selling? | Set `MAX_COINS: 0`. With `MAX_COINS: -1` the bot also stops once everything is sold. |
| How do I sell everything? | Stop the bot with Ctrl-C and answer `y`, or run `python sell-remaining-coins.py`. |
| Does it keep my coins after a restart? | Yes, held coins are stored in `coins_bought.json` (`test_coins_bought.json` in test mode). |
| Why didn't the bot buy a coin that spiked? | It only buys coins that are still up when it checks, that pass `MAX_SPREAD` and `MIN_24H_VOLUME`, and only while no pause signal is active. Run with `--debug` to see why candidates are skipped. |
