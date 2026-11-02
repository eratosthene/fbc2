"""
Where the app's settings come from: config.py, then any environment
variable named FBC_CONFIG_<KEY>, which overrides that key. On the live
server they come from docker/.env, so secrets and per-deploy values can stay
out of config.py. See "Settings from the environment" in README.md.

- A value is read as JSON when it can be, so FBC_CONFIG_DEV_MODE=true is a
  boolean, and as a string otherwise.
- A double underscore reaches into a dict: FBC_CONFIG_EBAY_SETTINGS__APP_ID
  sets APP_ID in EBAY_SETTINGS and leaves its other keys alone.

The prefix is longer than FBC_ so that FBC_DEV_MODE, FBC_DEV_DB_PASSWORD and
the dev-scripts' FBC_HOST and FBC_PORT don't become settings.

This module only imports Flask, so dev-scripts/_fake.py can load it without
importing (and so starting) the app.
"""

ENV_PREFIX = "FBC_CONFIG"


def load(config, source="config"):
    """Fill a Flask Config from `source` (the config module), then the
    environment."""
    config.from_object(source)
    # Nothing in the environment turns off dev mode that config.py turns on,
    # just as FBC_DEV_MODE can't (fbc/devmode.py).
    dev_mode = bool(config.get("DEV_MODE"))
    config.from_prefixed_env(ENV_PREFIX)
    if dev_mode:
        config["DEV_MODE"] = True
