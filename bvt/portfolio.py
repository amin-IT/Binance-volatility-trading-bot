"""The coins currently held by the bot, persisted to a JSON file.

The file format is the same as earlier versions of the bot (``coins_bought.json``),
so existing portfolios keep working and signal modules can read the held coins.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class PortfolioError(Exception):
    """The portfolio file cannot be read; the bot refuses to start rather than forget coins."""


@dataclass
class Position:
    symbol: str
    orderid: int | str
    timestamp: int  # milliseconds since epoch
    bought_at: float
    volume: float
    take_profit: float
    stop_loss: float
    trailing: bool = False  # True once the trailing stop loss has been armed

    @classmethod
    def from_dict(cls, symbol: str, data: dict[str, Any]) -> Position:
        """Parse one entry. Only ``bought_at`` and ``volume`` are required, so entries can be added by hand."""
        if not isinstance(data, dict):
            raise ValueError("entry must be a mapping")
        bought_at, volume = float(data["bought_at"]), float(data["volume"])
        if bought_at <= 0 or volume <= 0:
            raise ValueError("bought_at and volume must be greater than 0")
        timestamp = data.get("timestamp") or 0
        if isinstance(timestamp, float) and timestamp < 1e11:  # old test-mode files stored seconds
            timestamp *= 1000
        # Until the trailing stop is armed, take profit and stop loss come from config.yml.
        stop_loss = float(data.get("stop_loss", 0.0))
        if "trailing" in data:
            trailing = bool(data["trailing"])
        else:
            # older versions marked an armed trailing stop by moving the stop loss above zero
            trailing = "stop_loss" in data and stop_loss >= 0
        return cls(
            symbol=data.get("symbol", symbol),
            orderid=data.get("orderid", 0),
            timestamp=int(timestamp),
            bought_at=bought_at,
            volume=volume,
            take_profit=float(data.get("take_profit", 0.0)),
            stop_loss=stop_loss,
            trailing=trailing,
        )


class Portfolio:
    def __init__(self, path: str | os.PathLike, positions: dict[str, Position] | None = None) -> None:
        self.path = Path(path)
        self.positions: dict[str, Position] = positions or {}

    @classmethod
    def load(cls, path: str | os.PathLike) -> Portfolio:
        path = Path(path)
        if not path.exists():
            return cls(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise PortfolioError(f"Could not read {path}: {exc}") from None
        if not text.strip():
            raise PortfolioError(f"{path} is empty. Delete it if the bot holds no coins, or restore it from a backup.")
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise PortfolioError(f"Could not read {path}: {exc}") from None
        if not isinstance(raw, dict):
            raise PortfolioError(f"{path} must contain a JSON object of held coins")
        positions, problems = {}, []
        for symbol, data in raw.items():
            try:
                positions[symbol] = Position.from_dict(symbol, data)
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"{symbol}: {exc!r}")
        if problems:
            raise PortfolioError(f"Could not read these entries of {path}; fix or remove them: " + "; ".join(problems))
        return cls(path, positions)

    def save(self) -> None:
        """Write atomically so a crash mid-write never corrupts the portfolio."""
        data = {symbol: asdict(position) for symbol, position in self.positions.items()}
        directory = self.path.parent if str(self.path.parent) else Path(".")
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=4)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def add(self, position: Position) -> None:
        self.positions[position.symbol] = position
        self.save()

    def remove(self, symbol: str) -> Position | None:
        position = self.positions.pop(symbol, None)
        self.save()
        return position

    def get(self, symbol: str) -> Position | None:
        return self.positions.get(symbol)

    def __contains__(self, symbol: object) -> bool:
        return symbol in self.positions

    def __len__(self) -> int:
        return len(self.positions)

    def __iter__(self) -> Iterator[Position]:
        return iter(list(self.positions.values()))


def held_symbols(path: str | os.PathLike) -> set[str]:
    """Symbols in a portfolio file; an unreadable or missing file counts as empty."""
    try:
        return set(Portfolio.load(path).positions)
    except PortfolioError:
        return set()
