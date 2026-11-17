"""eBay OAuth: the consent link, exchanging and refreshing tokens, and the
access token each call needs."""

import datetime
import logging

from fbc.core import devmode, localtime

logger = logging.getLogger()


def _http_error(response):
    """A one-line description of an HTTP response eBay didn't answer properly."""
    if response is None:
        return "no response"
    status = getattr(response, "status_code", "?")
    reason = getattr(response, "reason", "") or ""
    text = getattr(response, "text", "") or ""
    if not isinstance(text, str):
        text = ""
    text = " ".join(text.split())[:300]
    return (
        "HTTP " + str(status) + " " + reason + (": " + text if text else "")
    ).strip()


OAUTH_AUTHORIZE_URL = "https://auth.ebay.com/oauth2/authorize"


OAUTH_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"


MEDIA_URL = "https://apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file"


MEDIA_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.inventory"


# Reading an order's earnings (Ship This) through the Finances API.
FINANCES_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.finances"


# The Trading API (listings, orders, policies) takes a user token minted with
# just the base scope.
TRADING_SCOPE = "https://api.ebay.com/oauth/api_scope"


# Reading how many views each listing had, through the Sell Analytics API
# (see #208). Until the owner presses Connect eBay again, a saved refresh
# token lacks it, and only calls that ask for this scope fail.
ANALYTICS_SCOPE = "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly"


# What Connect eBay asks the seller to allow.
CONSENT_SCOPES = [TRADING_SCOPE, MEDIA_SCOPE, FINANCES_SCOPE, ANALYTICS_SCOPE]


# The current OAuth access token for each scope and when it stops working.
# Access tokens last two hours, so each worker keeps them rather than asking
# per call. Each is minted for one scope, so a refresh token from before a
# scope was added to Connect eBay still works for the scopes it has.
_oauth = {}


class OAuthError(Exception):
    pass


def _remember_access_token(body, now, scopes):
    for scope in scopes:
        _oauth[scope] = {
            "token": body["access_token"],
            # Renew a minute early so a token never expires mid-request.
            "expires": now
            + datetime.timedelta(seconds=int(body.get("expires_in", 7200)) - 60),
        }


def forget_access_token(scope):
    """Drop a token eBay rejected, so the next call mints a new one."""
    _oauth.pop(scope, None)


def _token_request(data):
    """POST to eBay's OAuth token endpoint; the parsed reply, or OAuthError."""
    import requests
    from flask import current_app

    devmode.check(OAuthError, "eBay")
    ebayconfig = current_app.config["EBAY_SETTINGS"]
    r = requests.post(
        OAUTH_TOKEN_URL,
        auth=(ebayconfig["APP_ID"], ebayconfig["CERT_ID"]),
        data=data,
        timeout=30,
    )
    if r.status_code != 200:
        try:
            body = r.json()
            detail = body.get("error_description") or body.get("error") or str(body)
        except ValueError:
            detail = _http_error(r)
        raise OAuthError(detail)
    return r.json()


def consent_url(state):
    """
    The eBay sign-in page that asks the seller to let fbc manage their
    listings and pictures and read their order earnings and listing views. eBay then sends the
    browser to the RuName's "auth accepted" URL with a one-time code for
    exchange_code().
    """
    from urllib.parse import urlencode
    from flask import current_app

    ebayconfig = current_app.config["EBAY_SETTINGS"]
    if not ebayconfig.get("RUNAME"):
        raise OAuthError(
            "Set EBAY_SETTINGS['RUNAME'] in config.py to your app's eBay"
            " Redirect URL name (RuName)."
        )
    return (
        OAUTH_AUTHORIZE_URL
        + "?"
        + urlencode(
            {
                "client_id": ebayconfig["APP_ID"],
                "redirect_uri": ebayconfig["RUNAME"],
                "response_type": "code",
                "scope": " ".join(CONSENT_SCOPES),
                "state": state,
            }
        )
    )


def code_from(text):
    """
    The (code, state) in what eBay sent the browser back with: the whole
    address, just its query string, or the bare code. state is None when
    there isn't one.
    """
    from urllib.parse import parse_qs, unquote, urlparse

    text = (text or "").strip()
    if "code=" in text:
        query = urlparse(text).query if "?" in text else text
        params = parse_qs(query)
        code = (params.get("code") or [None])[0]
        state = (params.get("state") or [None])[0]
        return code, state
    return (unquote(text) or None), None


def exchange_code(code):
    """
    Swap a one-time sign-in code for tokens and save the refresh token.
    Returns the saved EbayOAuthToken; raises OAuthError if eBay says no.
    """
    from flask import current_app
    from fbc import db
    from fbc.models.ebay import EbayOAuthToken

    now = localtime.utcnow()
    try:
        body = _token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": current_app.config["EBAY_SETTINGS"].get("RUNAME", ""),
            }
        )
    except OAuthError as e:
        raise OAuthError("eBay didn't accept the sign-in code: " + str(e))
    expires = None
    if body.get("refresh_token_expires_in"):
        expires = now + datetime.timedelta(
            seconds=int(body["refresh_token_expires_in"])
        )
    saved = EbayOAuthToken(
        refresh_token=body["refresh_token"], expires=expires, created=now
    )
    db.session.add(saved)
    db.session.commit()
    _remember_access_token(body, now, CONSENT_SCOPES)
    logger.info("Saved a new eBay OAuth refresh token, expires " + str(expires))
    return saved


def saved_oauth_token():
    """The newest EbayOAuthToken saved by Connect eBay, or None."""
    from fbc import db
    from fbc.models.ebay import EbayOAuthToken

    return db.session.query(EbayOAuthToken).order_by(EbayOAuthToken.id.desc()).first()


def oauth_token(scope=MEDIA_SCOPE):
    """
    An OAuth user access token for `scope`, minted from the refresh token
    Connect eBay saved (or EBAY_SETTINGS["OAUTH_REFRESH_TOKEN"]). Every eBay
    call fbc makes uses one: the REST APIs and, through trading(), the
    Trading API.
    """
    from flask import current_app

    # Before the cache: a token minted before dev mode was turned on would
    # still work.
    devmode.check(OAuthError, "eBay")
    now = localtime.utcnow()
    cached = _oauth.get(scope)
    if cached and cached["expires"] > now:
        return cached["token"]
    saved = saved_oauth_token()
    refresh_token = (
        saved.refresh_token
        if saved
        else current_app.config["EBAY_SETTINGS"].get("OAUTH_REFRESH_TOKEN")
    )
    if not refresh_token:
        raise OAuthError(
            "Connect fbc to eBay first (Sales > Connect eBay);"
            " everything fbc does on eBay needs it."
        )
    try:
        body = _token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": scope,
            }
        )
    except OAuthError as e:
        raise OAuthError(
            "eBay refused the saved OAuth token (" + str(e) + "); connect"
            " fbc to eBay again under Sales > Connect eBay."
        )
    _remember_access_token(body, now, [scope])
    return _oauth[scope]["token"]


def _rest_errors(r):
    """Error messages from an eBay REST API error response."""
    try:
        errors = r.json().get("errors") or []
    except ValueError:
        errors = []
    messages = [e.get("longMessage") or e.get("message") or str(e) for e in errors]
    return messages or [_http_error(r)]
