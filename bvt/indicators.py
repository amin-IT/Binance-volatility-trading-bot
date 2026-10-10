"""Small, dependency-free technical indicators used by the signal modules.

Series are plain lists of floats (oldest first). Indicator outputs have the same
length as their input, with ``None`` where there is not enough data yet.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

Series = list[float | None]


def sma(values: Sequence[float], length: int) -> Series:
    out: Series = [None] * len(values)
    for i in range(length - 1, len(values)):
        out[i] = sum(values[i - length + 1 : i + 1]) / length
    return out


def ema(values: Sequence[float], length: int) -> Series:
    """Exponential moving average seeded with the SMA of the first ``length`` values."""
    out: Series = [None] * len(values)
    if len(values) < length:
        return out
    alpha = 2 / (length + 1)
    current = sum(values[:length]) / length
    out[length - 1] = current
    for i in range(length, len(values)):
        current = alpha * values[i] + (1 - alpha) * current
        out[i] = current
    return out


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    """MACD line, signal line and histogram."""
    fast_ema, slow_ema = ema(values, fast), ema(values, slow)
    line: Series = [f - s if f is not None and s is not None else None for f, s in zip(fast_ema, slow_ema)]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    signal_part = ema([v for v in line[start:] if v is not None], signal)
    signal_line: Series = [None] * start + signal_part
    histogram: Series = [m - s if m is not None and s is not None else None for m, s in zip(line, signal_line)]
    return line, signal_line, histogram


def rsi(values: Sequence[float], length: int = 14) -> Series:
    """Relative Strength Index using Wilder's smoothing (as TradingView does)."""
    out: Series = [None] * len(values)
    if len(values) <= length:
        return out
    gains = [max(values[i] - values[i - 1], 0.0) for i in range(1, len(values))]
    losses = [max(values[i - 1] - values[i], 0.0) for i in range(1, len(values))]
    avg_gain = sum(gains[:length]) / length
    avg_loss = sum(losses[:length]) / length

    def value(gain: float, loss: float) -> float:
        return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)

    out[length] = value(avg_gain, avg_loss)
    for i in range(length, len(gains)):
        avg_gain = (avg_gain * (length - 1) + gains[i]) / length
        avg_loss = (avg_loss * (length - 1) + losses[i]) / length
        out[i + 1] = value(avg_gain, avg_loss)
    return out


def coefficient_of_variation(values: Sequence[float]) -> float:
    """Population standard deviation as a percentage of the mean."""
    if not values:
        return 0.0
    mean = statistics.fmean(values)
    return statistics.pstdev(values) / mean * 100 if mean else 0.0


def cumulative_change(values: Sequence[float]) -> float:
    """Sum of the percentage changes between consecutive values."""
    return sum((b - a) / a * 100 for a, b in zip(values, values[1:]) if a)


def last(series: Series) -> float | None:
    return series[-1] if series else None
