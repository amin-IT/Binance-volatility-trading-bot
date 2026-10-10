"""Legacy credential helpers (kept so older third-party signal modules keep working)."""

import sys

from binance.exceptions import BinanceAPIException

from bvt.credentials import CredentialsError, check_api_key


def load_correct_creds(creds):
    """Return ``(access_key, secret_key)`` from a parsed creds.yml dict."""
    try:
        return creds["prod"]["access_key"], creds["prod"]["secret_key"]
    except (TypeError, KeyError) as exc:
        sys.exit(f"Your credentials are formatted incorrectly: missing {exc}")


def test_api_key(client, BinanceAPIException=BinanceAPIException):  # noqa: ARG001, N803 - legacy signature
    """Return ``(True, message)`` if the client's API key works, else ``(False, reason)``."""
    try:
        check_api_key(client)
    except CredentialsError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - legacy behaviour: report, don't raise
        return False, f"Fallback exception occurred:\n{exc}"
    return True, "API key validated successfully"
