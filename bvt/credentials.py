"""API credentials and Binance client creation."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from binance.client import Client
from binance.exceptions import BinanceAPIException

from bvt.config import Config, ConfigError, load_yaml

log = logging.getLogger(__name__)

_PLACEHOLDERS = {"", "replace_me", "your_api_key", "your_secret_key"}


class CredentialsError(Exception):
    """Raised when credentials are missing, malformed or rejected by Binance."""


@dataclass(frozen=True)
class Credentials:
    api_key: str
    api_secret: str

    def __repr__(self) -> str:  # never print secrets
        return f"Credentials(api_key='{self.api_key[:4]}…')"


def load_credentials(path: str | os.PathLike, testnet: bool = False) -> Credentials | None:
    """Load API keys from environment variables or the creds YAML file.

    Environment variables win: ``BINANCE_API_KEY``/``BINANCE_API_SECRET`` (or
    ``BINANCE_TESTNET_API_KEY``/``BINANCE_TESTNET_API_SECRET`` for the testnet).
    In the YAML file the keys live under ``prod:`` (or ``testnet:``) as
    ``access_key`` and ``secret_key``. Returns ``None`` when nothing is configured.
    """
    prefix = "BINANCE_TESTNET_API" if testnet else "BINANCE_API"
    env_key, env_secret = os.environ.get(f"{prefix}_KEY"), os.environ.get(f"{prefix}_SECRET")
    if env_key and env_secret:
        return Credentials(env_key.strip(), env_secret.strip())

    if not Path(path).is_file():
        return None
    try:
        data = load_yaml(path)
    except ConfigError as exc:
        raise CredentialsError(str(exc)) from None

    section_name = "testnet" if testnet else "prod"
    section = (data or {}).get(section_name) if isinstance(data, dict) else None
    if section is None:
        return None
    if not isinstance(section, dict):
        raise CredentialsError(
            f"Your credentials in {path} are formatted incorrectly: '{section_name}' must contain "
            "'access_key' and 'secret_key' (with a space after each colon)"
        )
    key = str(section.get("access_key") or "").strip()
    secret = str(section.get("secret_key") or "").strip()
    if key.lower() in _PLACEHOLDERS or secret.lower() in _PLACEHOLDERS:
        return None
    return Credentials(key, secret)


def create_client(config: Config, credentials: Credentials | None = None) -> Client:
    """Create a Binance client for the configured exchange (binance.com, binance.us or testnet).

    Without credentials the client can only use public market data, which is all
    test mode and the signal modules need.
    """
    key = credentials.api_key if credentials else None
    secret = credentials.api_secret if credentials else None
    tld = "us" if config.american_user and not config.live_testnet else "com"
    try:
        client = Client(key, secret, tld=tld, testnet=config.live_testnet, ping=False)
        client.ping()
    except BinanceAPIException as exc:
        raise CredentialsError(_explain(exc)) from None
    return client


def sync_time(client: Client) -> None:
    """Compensate for a local clock that drifts from Binance's (error -1021)."""
    try:
        server_ms = client.get_server_time()["serverTime"]
    except Exception as exc:  # noqa: BLE001 - best effort; signed requests will report real problems
        log.debug("Could not fetch Binance server time: %s", exc)
        return
    offset = server_ms - int(time.time() * 1000)
    client.timestamp_offset = offset
    if abs(offset) > 1000:
        log.warning("Your clock is %.1fs off from Binance; compensating. Consider syncing it with NTP.", offset / 1000)


def check_api_key(client: Client) -> None:
    """Make an authenticated call so bad keys fail at startup rather than at the first trade."""
    try:
        client.get_account()
    except BinanceAPIException as exc:
        raise CredentialsError(_explain(exc)) from None


def _explain(exc: BinanceAPIException) -> str:
    if exc.status_code == 451 or "restricted location" in str(exc.message).lower():
        return (
            f"{exc}\n  Binance does not serve your location from this endpoint. "
            "US residents need a Binance.US account and AMERICAN_USER: True."
        )
    if exc.code in (-2015, -2014):
        return (
            f"{exc}\n  Your API key is incorrect, IP restricted or lacks permissions:\n"
            "  - check access_key/secret_key in creds.yml (or BINANCE_API_KEY/BINANCE_API_SECRET)\n"
            "  - if you are in the USA set AMERICAN_USER: True\n"
            "  - if your key is IP restricted, allow this machine's public IP\n"
            "  - the key needs 'Enable Spot & Margin Trading' to place orders"
        )
    if exc.code == -1022:
        return f"{exc}\n  The signature is invalid: your secret_key is probably wrong."
    if exc.code == -1021:
        return f"{exc}\n  Your system clock is out of sync. Sync it with an NTP server such as pool.ntp.org."
    return f"Binance rejected the request: {exc}"
