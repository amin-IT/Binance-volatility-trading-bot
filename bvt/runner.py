"""Runs signal modules (``SIGNALLING_MODULES``) in background processes.

A signal module is any Python module with a blocking ``do_work()`` function that
writes signal files (see :mod:`bvt.signals`). Modules are looked up in the
``signal_modules`` package first, then as top-level modules (e.g. a file you drop
in the bot's folder).
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import multiprocessing
import os
import signal
import time
from types import ModuleType

from bvt.config import CONFIG_ENV_VAR
from bvt.console import setup_logging

log = logging.getLogger(__name__)

PACKAGE = "signal_modules"
RESTART_DELAY = 60


class SignalModuleError(Exception):
    pass


def _candidates(name: str) -> list[str]:
    return [f"{PACKAGE}.{name}", name]


def find_signal_module(name: str) -> str:
    """Return the importable module path for ``name`` or raise :class:`SignalModuleError`."""
    for candidate in _candidates(name):
        try:
            if importlib.util.find_spec(candidate) is not None:
                return candidate
        except (ImportError, ValueError):
            continue
    raise SignalModuleError(f"Signal module '{name}' not found in {PACKAGE}/ or the bot folder")


def import_signal_module(name: str) -> ModuleType:
    module = importlib.import_module(find_signal_module(name))
    if not callable(getattr(module, "do_work", None)):
        raise SignalModuleError(f"Signal module '{name}' has no do_work() function")
    return module


def _module_main(name: str, config_path: str, debug: bool) -> None:
    """Entry point of a signal module process."""
    # Ctrl-C is handled by the bot, which then stops us. Forked children inherit the bot's
    # handlers, so SIGTERM must be reset or terminate() would be ignored.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
    os.environ[CONFIG_ENV_VAR] = config_path
    setup_logging(debug)
    module_log = logging.getLogger(name)
    try:
        module = import_signal_module(name)
    except Exception:  # noqa: BLE001 - report any import failure and give up on this module
        module_log.exception("Could not load signal module")
        return
    while True:
        try:
            module.do_work()
            module_log.warning("do_work() returned; restarting in %ss", RESTART_DELAY)
        except SystemExit:
            module_log.warning("Signal module exited")
            return
        except Exception:  # noqa: BLE001 - keep the module alive whatever it raises
            module_log.exception("Signal module crashed; restarting in %ss", RESTART_DELAY)
        time.sleep(RESTART_DELAY)


class SignalModuleRunner:
    def __init__(self, names: tuple[str, ...] | list[str], config_path: str, debug: bool = False) -> None:
        self.names = list(names)
        self.config_path = config_path
        self.debug = debug
        self.processes: dict[str, multiprocessing.Process] = {}
        self._reported_dead: set[str] = set()

    def start(self) -> None:
        if not self.names:
            log.info("No signal modules configured")
            return
        for name in self.names:
            try:
                find_signal_module(name)
            except SignalModuleError as exc:
                log.error("%s", exc)
                continue
            log.info("Starting signal module %s", name)
            process = multiprocessing.Process(
                target=_module_main, args=(name, self.config_path, self.debug), name=name, daemon=True
            )
            process.start()
            self.processes[name] = process

    def check(self) -> None:
        """Report modules whose process has died."""
        for name, process in self.processes.items():
            if not process.is_alive() and name not in self._reported_dead:
                self._reported_dead.add(name)
                log.error("Signal module %s stopped (exit code %s)", name, process.exitcode)

    def stop(self) -> None:
        for name, process in self.processes.items():
            if process.is_alive():
                log.info("Stopping signal module %s", name)
                process.terminate()
        for name, process in self.processes.items():
            process.join(timeout=5)
            if process.is_alive():
                log.warning("Signal module %s did not stop, killing it", name)
                process.kill()
                process.join(timeout=5)
        self.processes.clear()
