"""The coins currently held by the bot, persisted to a JSON file.

The file format is the same as earlier versions of the bot (``coins_bought.json``),
so existing portfolios keep working and signal modules can read the held coins.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


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
        stop_loss = float(data["stop_loss"])
        timestamp = data.get("timestamp") or 0
        if isinstance(timestamp, float) and timestamp < 1e11:  # old test-mode files stored seconds
            timestamp *= 1000
        return cls(
            symbol=data.get("symbol", symbol),
            orderid=data.get("orderid", 0),
            timestamp=int(timestamp),
            bought_at=float(data["bought_at"]),
            volume=float(data["volume"]),
            take_profit=float(data["take_profit"]),
            stop_loss=stop_loss,
            # older versions marked an armed trailing stop by moving the stop loss above zero
            trailing=bool(data.get("trailing", stop_loss >= 0)),
        )


class Portfolio:
    def __init__(self, path: str | os.PathLike, positions: dict[str, Position] | None = None) -> None:
        self.path = Path(path)
        self.positions: dict[str, Position] = positions or {}

    @classmethod
    def load(cls, path: str | os.PathLike) -> Portfolio:
        path = Path(path)
        if not path.is_file() or path.stat().st_size == 0:
            return cls(path)
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        positions = {}
        for symbol, data in raw.items():
            try:
                positions[symbol] = Position.from_dict(symbol, data)
            except (KeyError, TypeError, ValueError) as exc:
                log.error("Skipping unreadable entry %s in %s: %s", symbol, path, exc)
        return cls(path, positions)

    def save(self) -> None:
        """Write atomically so a crash mid-write never corrupts the portfolio."""
        data = {symbol: asdict(position) for symbol, position in self.positions.items()}
        directory = self.path.parent if str(self.path.parent) else Path(".")
        fd, tmp = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=4)
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
    except (OSError, ValueError):
        return set()
