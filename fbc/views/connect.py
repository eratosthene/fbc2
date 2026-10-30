import secrets

from flask import flash, redirect, request, session, url_for
from flask_appbuilder import BaseView, expose, has_access

from fbc.listing import (
    OAuthError,
    code_from,
    consent_url,
    exchange_code,
    saved_oauth_token,
)


class ConnectEbayView(BaseView):
    """
    Connect eBay, under Sales: the OAuth sign-in that eBay's REST APIs need,
    for picture uploads (eBay Drafts) and order earnings (Ship This).
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
                        "Connected to eBay. Picture uploads and Ship This will"
                        " work now.",
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
