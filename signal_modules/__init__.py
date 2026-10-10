"""Signal modules bundled with the bot.

Enable a module by adding its name to ``SIGNALLING_MODULES`` in config.yml. Each
module runs in its own process; its settings are the UPPERCASE constants at the
top of its file.

Buy signals
    VolScan                         coins with the biggest dip / volatility over a scanning period
    custsignalmod                   TradingView oscillators + moving averages agree on BUY
    signalsamplemod                 TradingView summary BUY count on two timeframes
    the_snail                       "buy the dips": coins far below their recent high, MACD turning up
    rsi_mod                         RSI / Stochastic momentum (also emits sell signals)
    rsi_stoch_signalmod_djcommie    rising RSI with Stochastic confirmation
    ta_indicator_signalmod_firewatch  TradingView BUY on 1m/5m/15m, not overbought (also emits sells)

Pause signals (stop buying in a falling market)
    pausebotmod                     BTC moving averages on TradingView lean SELL
    os_pausebot_MAv2                BTC or ETH moving averages lean SELL
    amin_btc_pause                  BTC recommendation on 3 timeframes, then BTC moving-average trend

Writing your own: see README.md ("Signal modules"). Modules in this package or
in the bot's folder can be used.
"""
