import pytest

from bvt.indicators import coefficient_of_variation, cumulative_change, ema, macd, rsi, sma

# closing prices from StockCharts' RSI calculation example (Wilder's method)
WILDER = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826, 45.8931,
    46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521,
    45.7137, 46.4515, 45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314,
]  # fmt: skip


def test_sma():
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


def test_ema_is_seeded_with_the_sma():
    values = ema([1, 2, 3, 4, 5], 3)
    assert values[:2] == [None, None]
    assert values[2] == pytest.approx(2.0)
    assert values[3] == pytest.approx(3.0)  # 0.5 * 4 + 0.5 * 2
    assert values[4] == pytest.approx(4.0)


def test_rsi_matches_wilders_reference_values():
    values = rsi(WILDER, 14)
    assert values[13] is None
    assert values[14] == pytest.approx(70.53, abs=0.01)
    assert values[15] == pytest.approx(66.32, abs=0.01)
    assert values[-1] == pytest.approx(37.77, abs=0.01)


def test_rsi_of_a_rising_series_is_100():
    assert rsi(list(range(1, 20)), 14)[-1] == 100


def test_macd_histogram_needs_slow_plus_signal_candles():
    closes = [100 + i * 0.5 for i in range(36)]
    line, signal, histogram = macd(closes)
    first_line = next(i for i, v in enumerate(line) if v is not None)
    first_hist = next(i for i, v in enumerate(histogram) if v is not None)
    assert first_line == 25
    assert first_hist == 33
    assert histogram[-1] == pytest.approx(line[-1] - signal[-1])
    assert line[-1] > 0  # uptrend


def test_macd_histogram_turns_negative_after_a_reversal():
    closes = [100 + i for i in range(30)] + [130 - 3 * i for i in range(10)]
    assert macd(closes)[2][-1] < 0


def test_coefficient_of_variation_and_cumulative_change():
    assert coefficient_of_variation([10, 10, 10]) == 0
    assert coefficient_of_variation([9, 11]) == pytest.approx(10.0)
    assert coefficient_of_variation([]) == 0
    assert cumulative_change([100, 110, 99]) == pytest.approx(10 - 10)
