"""
Dev mode, for running fbc against a copy of the live database without
reaching the live eBay and Discogs accounts.

It's on when config.py sets DEV_MODE = True or the environment has
FBC_DEV_MODE=1 (docker/compose.dev.yml sets it); neither can turn the other
off. In dev mode:

- the eBay and Discogs keys in config.py are treated as empty;
- the functions every eBay and Discogs call goes through (listing.oauth_token,
  listing._token_request, discogs_client.client, discogs_client._app_client) refuse to run,
  so the OAuth tokens Connect eBay and Connect Discogs saved in the database
  are never used;
- as a backstop, any request to an eBay or Discogs host is refused;
- the syncs log that they're off and finish without doing anything, and the
  pages that would reach eBay or Discogs say so instead;
- every page shows a Dev Mode banner, and the app is named "... (dev)".
"""

import functools
import logging
import os
from urllib.parse import urlparse

import requests

logger = logging.getLogger()

ENVIRONMENT_VARIABLE = "FBC_DEV_MODE"

# Kept in config.py but treated as empty in dev mode.
EBAY_SECRETS = ("APP_ID", "DEV_ID", "CERT_ID", "RUNAME", "OAUTH_REFRESH_TOKEN")
DISCOGS_SECRETS = ("CONSUMER_KEY", "CONSUMER_SECRET", "USER_TOKEN")

# Hosts no request may go to in dev mode, along with their subdomains.
BLOCKED_DOMAINS = ("ebay.com", "discogs.com")


class ExternalRequestBlocked(requests.ConnectionError):
    """A request to eBay or Discogs that dev mode refused to send."""


def message(what):
    """ "Dev Mode is turned on, <what> disabled." """
    return "Dev Mode is turned on, " + what + " disabled."


def from_environment():
    value = os.environ.get(ENVIRONMENT_VARIABLE, "").strip().lower()
    return value in ("1", "true", "yes", "on")


def init_app(app):
    """
    Work out whether dev mode is on and, if it is, clean the config up before
    the rest of the app reads it. Call it right after the config is loaded.
    """
    dev_mode = bool(app.config.get("DEV_MODE")) or from_environment()
    app.config["DEV_MODE"] = dev_mode

    @app.context_processor
    def dev_mode_context():
        return {"dev_mode": on()}

    if not dev_mode:
        return
    for key, secrets in (
        ("EBAY_SETTINGS", EBAY_SECRETS),
        ("DISCOGS_SETTINGS", DISCOGS_SECRETS),
    ):
        # A copy, so the dict in the config module itself is left alone.
        settings = dict(app.config.get(key) or {})
        settings.update(dict.fromkeys(secrets, ""))
        app.config[key] = settings
    app.config["APP_NAME"] = app.config.get("APP_NAME", "FBC") + " (dev)"
    block_external_requests()
    logger.warning(
        "Dev Mode is turned on: fbc won't reach eBay or Discogs, and syncs do nothing."
    )


def on():
    from fbc import app

    return bool(app.config.get("DEV_MODE"))


def check(error, what="eBay and Discogs"):
    """
    Raise error (OAuthError, DiscogsAuthError) in dev mode. Called first
    thing by the functions every eBay or Discogs call goes through, whose
    callers already show those errors' messages.
    """
    if on():
        raise error(message(what))


def skip_sync():
    """Run in place of a sync in dev mode, so the job finishes as done."""
    logger.info(message("external syncing"))


def is_blocked(url):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def guard_send(send):
    """requests.Session.send, refusing anything to an eBay or Discogs host."""

    @functools.wraps(send)
    def guarded(session, request, **kwargs):
        if is_blocked(request.url):
            host = urlparse(request.url).hostname
            logger.error("Dev Mode refused a request to " + str(host))
            raise ExternalRequestBlocked(
                message("eBay and Discogs") + " Refused a request to " + str(host),
                request=request,
            )
        return send(session, request, **kwargs)

    guarded.dev_mode_guard = True
    return guarded


def block_external_requests():
    """
    Refuse every request to eBay or Discogs from now on, whatever makes it:
    ebaysdk and discogs_client both send through requests.Session.send.
    """
    if not getattr(requests.Session.send, "dev_mode_guard", False):
        requests.Session.send = guard_send(requests.Session.send)
