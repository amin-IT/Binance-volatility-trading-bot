from tradingview_ta import TA_Handler, Interval, Exchange
import os
import time
import threading
import ccxt
import pandas as pd
import pandas_ta as ta
from datetime import datetime

INTERVAL = Interval.INTERVAL_1_MINUTE #Timeframe for analysis

EXCHANGE = 'BINANCE'
SCREENER = 'CRYPTO'
SYMBOL = 'BTCUSDT'
TIME_TO_WAIT = 1
SIGNAL_NAME = 'btc_pauseTV'
SIGNAL_FILE = 'signals/pausebot.pause'

def analyze():
    first_handler = TA_Handler(
        symbol=SYMBOL,
        exchange=EXCHANGE,
        screener=SCREENER,
        interval=Interval.INTERVAL_1_MINUTE,
        timeout=60
    )
    second_handler = TA_Handler(
        symbol=SYMBOL,
        exchange=EXCHANGE,
        screener=SCREENER,
        interval=Interval.INTERVAL_30_MINUTES,
        timeout=60
    )
    third_handler = TA_Handler(
        symbol=SYMBOL,
        exchange=EXCHANGE,
        screener=SCREENER,
        interval=Interval.INTERVAL_4_HOURS,
        timeout=60
    )


    try:
        first_analysis = first_handler.get_analysis().summary['RECOMMENDATION']
    except:
        pass
    try:
        second_analysis = second_handler.get_analysis().summary['RECOMMENDATION']
    except:
        pass
    try:
        third_analysis = third_handler.get_analysis().summary['RECOMMENDATION']
    except:
        pass

    if (first_analysis == "SELL" or first_analysis == "STRONG_SELL") and \
            (second_analysis == "SELL" or second_analysis == "STRONG_SELL") and \
            (third_analysis == "SELL" or third_analysis == "STRONG_SELL"):
        paused = True
        # print(f'pausebotmod: {SYMBOL} Market not looking too good, bot paused from buying. SELL indicators: {ma_analysis_sell}. BUY/NEUTRAL indicators: {ma_analysis_buy + ma_analysis_neutral}. P: {price} | LP: {lastprice} | Waiting {TIME_TO_WAIT} minutes for next market checkup')
        print(f'{SIGNAL_NAME}: {SYMBOL} Market not looking too good, bot paused from buying. Indicators: {first_analysis} {second_analysis} {third_analysis}. Waiting {TIME_TO_WAIT} minutes for next market checkup')
    elif (first_analysis == "BUY" or first_analysis == "STRONG_BUY") and \
            (second_analysis == "BUY" or second_analysis == "STRONG_BUY") and \
            (third_analysis == "BUY" or third_analysis == "STRONG_BUY"):
        #print(f'pausebotmod: {SYMBOL} Market looks ok, bot is running. SELL indicators: {ma_analysis_sell}. BUY/NEUTRAL indicators: {ma_analysis_buy + ma_analysis_neutral}. P: {price} | LP: {lastprice} | Waiting {TIME_TO_WAIT} minutes for next market checkup')
        print(f'{SIGNAL_NAME}: {SYMBOL} Market looks ok, bot is running. Indicators: {first_analysis} {second_analysis} {third_analysis}. Waiting {TIME_TO_WAIT} minutes for next market checkup')
        paused = False
    else:
        print(f'{first_analysis} {second_analysis} {third_analysis}')
        exchange = ccxt.binance()
        try:
            # btc = exchange.fetch_ohlcv("BTCUSDT", timeframe='1m', limit=25)
            btc = exchange.fetch_ohlcv("BTCUSDT", timeframe='1m', limit=220)
            btc = pd.DataFrame(btc, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
            btc['VWAP'] = ((((btc.high + btc.low + btc.close) / 3) * btc.volume) / btc.volume)
        # print(btc)
        except Exception as e:
            print('CCXT Error')
            print(e.status_code)
            print(e.message)
            print(e.code)
            pass

        btc2 = btc.ta.sma(length=2)
        btc3 = btc.ta.sma(length=3)
        btc4 = btc.ta.sma(length=4)
        btc5 = btc.ta.sma(length=5)
        btc10 = btc.ta.sma(length=10)
        btc20 = btc.ta.sma(length=20)
        btc50 = btc.ta.sma(length=50)
        btc200 = btc.ta.sma(length=200)

        btc2 = btc2.iloc[-1]
        btc3 = btc3.iloc[-1]
        btc4 = btc4.iloc[-1]
        btc5 = btc5.iloc[-1]
        btc10 = btc10.iloc[-1]
        btc20 = btc20.iloc[-1]
        btc50 = btc50.iloc[-1]
        btc200 = btc200.iloc[-1]
        print(f"{btc2:.2f} {btc3:.2f} {btc4:.2f} {btc5:.2f} {btc10:.2f} {btc20:.2f} {btc50:.2f} {btc200:.2f}")

        paused = False
        if btc2 > btc3 > btc4 > btc5 > btc10 > btc20 > btc50 > btc200:
            paused = False
            print(f'{SIGNAL_NAME}: Market looks OK')

        else:
            print(f'{SIGNAL_NAME}: Market not looking good')
            paused = True


    return paused
#if __name__ == '__main__':
def do_work():

    while True:
        try:
            if not threading.main_thread().is_alive(): exit()
            # print(f'pausebotmod: Fetching market state')
            paused = analyze()
            if paused:
                with open(SIGNAL_FILE,'a+') as f:
                    f.write('yes')
            else:
                if os.path.isfile(SIGNAL_FILE):
                    os.remove(SIGNAL_FILE)

            # print(f'pausebotmod: Waiting {TIME_TO_WAIT} minutes for next market checkup')
            time.sleep((TIME_TO_WAIT*60))
        except Exception as e:
            print(f'{SIGNAL_NAME}: Exception do_work() 1: {e}')
            continue
        except KeyboardInterrupt as ki:
            continue



