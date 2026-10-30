import secrets

from flask import flash, redirect, request, session, url_for
from flask_appbuilder import BaseView, expose, has_access

from fbc.discogs import (
    DiscogsAuthError,
    authorize_url,
    exchange_verifier,
    has_app_keys,
    verifier_from,
)
from fbc.discogs import saved_oauth_token as saved_discogs_token
from fbc.listing import (
    OAuthError,
    code_from,
    consent_url,
    exchange_code,
    saved_oauth_token,
)


class ConnectEbayView(BaseView):
    """
    Connect eBay, under Sales: the OAuth sign-in every eBay call fbc makes
    uses (syncing listings and orders, eBay Drafts, Ship This).
    """

    route_base = "/connectebay"
    default_view = "connect"

    @expose("/", methods=["GET", "POST"])
    @has_access
    def connect(self):
        """
        Connect eBay: sign in on eBay, then come back with the one-time code
        eBay hands the browser. The code arrives here directly if the
        RuName's auth accepted URL points at this page, or can be pasted in
        (the whole address eBay ends on) if it doesn't.
        """
        text = request.form.get("address") if request.method == "POST" else None
        if text is None and request.args.get("code"):
            text = request.query_string.decode()
        if text is not None:
            code, state = code_from(text)
            if not code:
                flash("That doesn't contain a sign-in code from eBay.", "danger")
            elif state and state != session.get("ebay_oauth_state"):
                flash(
                    "That sign-in was started from a different page; start again.",
                    "danger",
                )
            else:
                try:
                    exchange_code(code)
                    session.pop("ebay_oauth_state", None)
                    flash(
                        "Connected to eBay.",
                        "success",
                    )
                except OAuthError as e:
                    flash(str(e), "danger")
            return redirect(url_for("ConnectEbayView.connect"))

        state = session.get("ebay_oauth_state") or secrets.token_urlsafe(16)
        session["ebay_oauth_state"] = state
        try:
            sign_in = consent_url(state)
        except OAuthError as e:
            flash(str(e), "warning")
            sign_in = None
        return self.render_template(
            "connect_ebay.html",
            title="Connect eBay",
            sign_in=sign_in,
            saved=saved_oauth_token(),
        )


class ConnectDiscogsView(BaseView):
    """
    Connect Discogs, under Discogs: the OAuth sign-in fbc uses for everything
    it does with the collection (Sync, labels for listings, Ship This).
    """

    route_base = "/connectdiscogs"
    default_view = "connect"

    @expose("/", methods=["GET", "POST"])
    @has_access
    def connect(self):
        """
        Connect Discogs: Discogs sends the browser back here with a verifier
        after the owner allows fbc in. If it can't reach this page, the
        address it ends on can be pasted in instead.
        """
        if request.args.get("denied"):
            session.pop("discogs_oauth_request", None)
            flash("Discogs sign-in was cancelled.", "warning")
            return redirect(url_for("ConnectDiscogsView.connect"))
        text = request.form.get("address") if request.method == "POST" else None
        if text is None and request.args.get("oauth_verifier"):
            text = request.query_string.decode()
        if text is not None:
            token, verifier = verifier_from(text)
            pending = session.get("discogs_oauth_request")
            if not verifier:
                flash("That doesn't contain a verifier from Discogs.", "danger")
            elif not pending or (token and token != pending[0]):
                flash(
                    "That sign-in was started from a different page; start again.",
                    "danger",
                )
            else:
                try:
                    saved = exchange_verifier(pending[0], pending[1], verifier)
                    session.pop("discogs_oauth_request", None)
                    flash(
                        "Connected to Discogs as " + str(saved.username) + ".",
                        "success",
                    )
                except DiscogsAuthError as e:
                    flash(str(e), "danger")
            return redirect(url_for("ConnectDiscogsView.connect"))

        if not has_app_keys():
            flash(
                "Set DISCOGS_SETTINGS['CONSUMER_KEY'] and ['CONSUMER_SECRET'] in"
                " config.py to your Discogs app's keys.",
                "warning",
            )
        return self.render_template(
            "connect_discogs.html",
            title="Connect Discogs",
            configured=has_app_keys(),
            pending=bool(session.get("discogs_oauth_request")),
            saved=saved_discogs_token(),
        )

    @expose("/start")
    @has_access
    def start(self):
        """Get a request token from Discogs and send the browser to sign in."""
        try:
            token, secret, url = authorize_url(
                url_for("ConnectDiscogsView.connect", _external=True)
            )
        except DiscogsAuthError as e:
            flash(str(e), "danger")
            return redirect(url_for("ConnectDiscogsView.connect"))
        session["discogs_oauth_request"] = [token, secret]
        return redirect(url)
