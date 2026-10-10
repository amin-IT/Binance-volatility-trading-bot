"""The signal file protocol shared by the bot and the signal modules.

Signal modules communicate with the bot through files in the ``signals/`` folder:

* ``*.exs`` or ``*.buy`` - one symbol per line (e.g. ``BTCUSDT``) to buy
* ``*.sell``             - one symbol per line to sell if the bot holds it
* ``paused.exc`` or ``*.pause`` - while any exists the bot buys nothing
  (stop loss / take profit keep working)

``.exs``/``paused.exc`` are the original format of this bot; ``.buy``/``.sell``/``.pause``
are used by the OlorinSledge fork, so modules written for either work here.
Buy and sell files are consumed (deleted) when the bot reads them.
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

log = logging.getLogger(__name__)

SIGNALS_DIR = Path("signals")

BUY_SUFFIXES = (".exs", ".buy")
SELL_SUFFIXES = (".sell",)
PAUSE_SUFFIXES = (".pause",)
LEGACY_PAUSE_FILE = "paused.exc"

_KIND_SUFFIXES = {"buy": BUY_SUFFIXES, "sell": SELL_SUFFIXES}
_CLAIM_SUFFIX = ".reading"


def _parse(text: str) -> list[str]:
    symbols = []
    for line in text.splitlines():
        symbol = line.split("#", 1)[0].strip().upper()
        if symbol:
            symbols.append(symbol)
    return symbols


def read_signals(kind: str, directory: str | os.PathLike = SIGNALS_DIR, consume: bool = True) -> list[str]:
    """Symbols from all ``buy`` or ``sell`` signal files, in file then line order, without duplicates."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    symbols: list[str] = []
    for path in sorted(p for p in directory.iterdir() if p.suffix in _KIND_SUFFIXES[kind] and p.is_file()):
        source = path
        if consume:
            # claim the file atomically so a module rewriting it right now is not lost
            source = path.with_name(path.name + _CLAIM_SUFFIX)
            try:
                os.replace(path, source)
            except OSError:
                continue
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            log.debug("Could not read signal file %s: %s", source, exc)
            continue
        finally:
            if consume:
                source.unlink(missing_ok=True)
        for symbol in _parse(text):
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols


def pause_sources(directory: str | os.PathLike = SIGNALS_DIR) -> list[str]:
    """Names of the files currently requesting a pause."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(
        p.name
        for p in directory.iterdir()
        if p.is_file() and (p.name == LEGACY_PAUSE_FILE or p.suffix in PAUSE_SUFFIXES)
    )


def is_paused(directory: str | os.PathLike = SIGNALS_DIR) -> bool:
    return bool(pause_sources(directory))


def clear_signals(directory: str | os.PathLike = SIGNALS_DIR) -> None:
    """Remove stale signal files left over from a previous run."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    suffixes = BUY_SUFFIXES + SELL_SUFFIXES + PAUSE_SUFFIXES
    for path in directory.iterdir():
        if path.is_file() and (
            path.suffix in suffixes or path.name == LEGACY_PAUSE_FILE or path.name.endswith(_CLAIM_SUFFIX)
        ):
            try:
                path.unlink()
            except OSError as exc:
                log.warning("Could not remove signal file %s: %s", path, exc)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_signals(
    name: str, symbols: Iterable[str], kind: str = "buy", directory: str | os.PathLike = SIGNALS_DIR
) -> Path:
    """Replace ``signals/<name>.exs`` (or ``.sell``) with ``symbols``; an empty list removes the file."""
    suffix = ".exs" if kind == "buy" else ".sell"
    path = Path(directory) / f"{name}{suffix}"
    unique = list(dict.fromkeys(s.strip().upper() for s in symbols if s.strip()))
    if unique:
        _atomic_write(path, "\n".join(unique) + "\n")
    else:
        path.unlink(missing_ok=True)
    return path


def set_paused(name: str, paused: bool, directory: str | os.PathLike = SIGNALS_DIR) -> None:
    """Create or remove ``signals/<name>.pause``. Each module owns its own pause file."""
    path = Path(directory) / f"{name}.pause"
    if paused:
        _atomic_write(path, "paused\n")
    else:
        path.unlink(missing_ok=True)
