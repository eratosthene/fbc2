"""
The app's log level, from the LOG_LEVEL environment variable.

The live image sets LOG_LEVEL=info (docker/Dockerfile); the dev scripts set
debug. This module imports nothing from the app, so tests can load it alone.
"""

import logging
import os

DEFAULT_LEVEL = logging.INFO
# The eBay SDK logs every request and response at DEBUG, under "ebaysdk".
SDK_LOGGER = "ebaysdk"


def parse_level(value):
    """A logging level from a name such as "debug" or "Warning". Anything
    else, including nothing, is INFO."""
    level = logging.getLevelName((value or "").strip().upper())
    # getLevelName gives a string for a name it doesn't know, and also maps
    # the other way, so only take a real level that isn't NOTSET.
    if isinstance(level, int) and level > logging.NOTSET:
        return level
    return DEFAULT_LEVEL


def init_logging(environ=None):
    """Set the root logger's level from LOG_LEVEL, and keep the eBay SDK at
    WARNING unless that is DEBUG. Returns the level."""
    level = parse_level((os.environ if environ is None else environ).get("LOG_LEVEL"))
    logging.getLogger().setLevel(level)
    logging.getLogger(SDK_LOGGER).setLevel(
        logging.DEBUG if level <= logging.DEBUG else logging.WARNING
    )
    return level
