"""
Signing in to Discogs. fbc talks to Discogs as the collection's owner through
an OAuth token from Connect Discogs (Discogs > Connect Discogs), made with the
app keys in DISCOGS_SETTINGS; DISCOGS_SETTINGS["USER_TOKEN"], a personal
access token, is used only until then.
"""

import datetime
import logging

logger = logging.getLogger()


class DiscogsAuthError(Exception):
    pass


def _settings():
    from fbc import app

    return app.config["DISCOGS_SETTINGS"]


def has_app_keys():
    settings = _settings()
    return bool(settings.get("CONSUMER_KEY") and settings.get("CONSUMER_SECRET"))


def _app_client(token=None, secret=None):
    import discogs_client

    settings = _settings()
    if not has_app_keys():
        raise DiscogsAuthError(
            "Set DISCOGS_SETTINGS['CONSUMER_KEY'] and ['CONSUMER_SECRET'] in"
            " config.py to your Discogs app's keys (discogs.com > Settings >"
            " Developers)."
        )
    return discogs_client.Client(
        settings["USER_AGENT"],
        consumer_key=settings["CONSUMER_KEY"],
        consumer_secret=settings["CONSUMER_SECRET"],
        token=token,
        secret=secret,
    )


def saved_oauth_token():
    """The newest DiscogsOAuthToken saved by Connect Discogs, or None."""
    from fbc import db
    from fbc.models.discogs import DiscogsOAuthToken

    return (
        db.session.query(DiscogsOAuthToken)
        .order_by(DiscogsOAuthToken.id.desc())
        .first()
    )


def client():
    """
    A discogs_client.Client signed in as the collection's owner: through the
    token Connect Discogs saved, or DISCOGS_SETTINGS["USER_TOKEN"] before
    there is one. Raises DiscogsAuthError if there's neither.
    """
    import discogs_client

    saved = saved_oauth_token()
    if saved:
        return _app_client(saved.token, saved.secret)
    settings = _settings()
    if settings.get("USER_TOKEN"):
        return discogs_client.Client(
            settings["USER_AGENT"], user_token=settings["USER_TOKEN"]
        )
    raise DiscogsAuthError("Connect fbc to Discogs first (Discogs > Connect Discogs).")


def authorize_url(callback_url):
    """
    Start a sign-in: (request_token, request_secret, url), where url is the
    Discogs page that asks the owner to let fbc at their account. Discogs then
    sends the browser to callback_url with a verifier for exchange_verifier().
    """
    from discogs_client.exceptions import DiscogsAPIError

    d = _app_client()
    try:
        return d.get_authorize_url(callback_url)
    except DiscogsAPIError as e:
        raise DiscogsAuthError("Discogs wouldn't start a sign-in: " + str(e))


def verifier_from(text):
    """
    The (oauth_token, oauth_verifier) in what Discogs sent the browser back
    with: the whole address, just its query string, or the bare verifier.
    oauth_token is None when there isn't one.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    text = (text or "").strip()
    if "oauth_verifier=" in text:
        query = urlparse(text).query if "?" in text else text
        params = parse_qs(query)
        token = (params.get("oauth_token") or [None])[0]
        verifier = (params.get("oauth_verifier") or [None])[0]
        return token, verifier
    return None, (unquote(text) or None)


def exchange_verifier(request_token, request_secret, verifier):
    """
    Swap a sign-in's request token and verifier for an access token and save
    it. Returns the saved DiscogsOAuthToken; raises DiscogsAuthError if
    Discogs says no.
    """
    from discogs_client.exceptions import DiscogsAPIError

    from fbc import db
    from fbc.models.discogs import DiscogsOAuthToken

    d = _app_client(request_token, request_secret)
    try:
        token, secret = d.get_access_token(verifier)
        username = d.identity().username
    except DiscogsAPIError as e:
        raise DiscogsAuthError("Discogs didn't accept the sign-in: " + str(e))
    saved = DiscogsOAuthToken(
        token=token,
        secret=secret,
        username=username,
        created=datetime.datetime.utcnow(),
    )
    db.session.add(saved)
    db.session.commit()
    logger.info("Saved a new Discogs OAuth token for " + str(username))
    return saved
