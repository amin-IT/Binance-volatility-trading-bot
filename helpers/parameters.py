"""Legacy config helpers (kept so older third-party signal modules keep working)."""

import argparse
import sys

from bvt.config import ConfigError, config_path_from_env_or_argv, load_yaml


def load_config(file):
    """Return the raw YAML document as a dict (exits with a message on errors)."""
    try:
        return load_yaml(file)
    except ConfigError as exc:
        sys.exit(str(exc))


def parse_args():
    """Parse the bot's command line; unknown arguments are ignored."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--debug", "-d", action="store_true")
    parser.add_argument("--config", "-c")
    parser.add_argument("--creds", "-u")
    parser.add_argument("--notimeout", action="store_true")
    args, _ = parser.parse_known_args()
    if not args.config:
        # signal modules run in child processes: use the config the bot was started with
        args.config = config_path_from_env_or_argv()
    return args
