import logging

from flask import flash, g, jsonify, redirect, request, url_for
from flask_appbuilder import IndexView, has_access
from flask_appbuilder.views import expose

from fbc.sales import cards, layout, tasks
from fbc.sync import jobs, state
from fbc.ebay import insertions

logger = logging.getLogger()


def banner_figures(shared):
    """
    What the pinned alerts above the cards need: supplies running low,
    the To do counts, and this month's free insertions. Not a card: it can't
    be moved or hidden.
    """
    from fbc.models.supplies import Supply

    return {
        "low_supplies": Supply.low_list(),
        "tasks": tasks.counts(),
        "listings": shared.get(cards.listing_summary),
    }


def home_context(shown_cards=None, user_id=None):
    """
    What the home page renders: the banner's figures, `cards` (the cards to
    show in order, each with its figures) and `hidden_cards` (the user's
    hidden ones, with no figures). `shown_cards` None means the cards in the
    user's layout (`user_id`; the default order for none). Only the cards
    shown have their figures worked out.
    """
    shared = cards.Shared()
    context = banner_figures(shared)
    hidden = []
    if shown_cards is None:
        arranged = layout.for_user(user_id)
        shown_cards, hidden = arranged.visible, arranged.hidden_cards
    context["cards"] = cards.figures_for(shown_cards, shared)
    context["hidden_cards"] = hidden
    return context


def home_totals():
    """Every figure the home page has, in one dict, for reports and tests."""
    context = home_context()
    totals = {
        key: value
        for key, value in context.items()
        if key not in ("cards", "hidden_cards")
    }
    for shown in context["cards"]:
        totals.update(shown.figures)
    return totals


def _user_id():
    user = g.get("user")
    return getattr(user, "id", None)


class MyIndexView(IndexView):
    """
    The home page and the Sync buttons' routes. Every route needs signing in
    (has_access), because they show the shop's totals and start syncs; Flask-
    AppBuilder's own home page is public.
    """

    index_template = "index.html"

    @expose("/")
    @has_access
    def index(self):
        self.update_redirect()
        context = home_context(user_id=_user_id())
        return self.render_template(
            self.index_template,
            appbuilder=self.appbuilder,
            customize=request.args.get("customize") == "1",
            **context,
        )

    def _change_layout(self, change):
        """
        Run `change(user_id)` on the signed-in user's own layout and answer
        with JSON. A card key that isn't a card is a 400 and changes nothing.
        """
        try:
            change(g.user.id)
        except layout.BadKey as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True)

    @expose("/layout/order", methods=["POST"])
    @has_access
    def layoutorder(self):
        """Save the order the visible cards were dragged into: {"order": [keys]}."""
        data = request.get_json(silent=True)
        keys = data.get("order") if isinstance(data, dict) else None
        return self._change_layout(lambda uid: layout.reorder(uid, keys))

    @expose("/layout/hide/<key>", methods=["POST"])
    @has_access
    def layouthide(self, key):
        return self._change_layout(lambda uid: layout.set_hidden(uid, key, True))

    @expose("/layout/show/<key>", methods=["POST"])
    @has_access
    def layoutshow(self, key):
        return self._change_layout(lambda uid: layout.set_hidden(uid, key, False))

    @expose("/layout/reset", methods=["POST"])
    @has_access
    def layoutreset(self):
        """Delete the saved layout: the default order, nothing hidden."""
        return self._change_layout(layout.reset)

    @expose("/calibrateinsertions", methods=["POST"])
    @has_access
    def calibrateinsertions(self):
        """Save the free listings left that the eBay app shows."""
        limit = insertions.free_insertions_limit()
        try:
            remaining = int(request.form.get("remaining", ""))
        except ValueError:
            remaining = -1
        if not 0 <= remaining <= limit:
            flash(
                "Enter the number of free listings left that the eBay app shows,"
                " from 0 to " + str(limit) + ".",
                "danger",
            )
        else:
            change = insertions.calibrate(remaining)
            flash(
                "Saved. fbc's count was "
                + (
                    "the same."
                    if change == 0
                    else str(abs(change)) + (" low." if change > 0 else " high.")
                ),
                "success",
            )
        return redirect(url_for("MyIndexView.index"))

    def _start_sync(self, job, fallback_url):
        """
        Start a sync (a fbc.sync.jobs Job) as a background job (see fbc.sync).
        A request from the Sync buttons' script gets the job as JSON, to show
        in a modal; any other request gets a page that shows the job's log.
        In dev mode the job only logs that syncing is off.
        """
        job_id = state.start(
            job.kind, job.title, job.fn, request.referrer or fallback_url
        )
        job_url = url_for("MyIndexView.syncjob", job_id=job_id)
        best = request.accept_mimetypes.best_match(["text/html", "application/json"])
        if best == "application/json":
            return jsonify(id=job_id, url=job_url)
        self.update_redirect()
        return self.render_template(
            "sync_job.html", appbuilder=self.appbuilder, job_url=job_url
        )

    @expose("/syncjob/<job_id>")
    @has_access
    def syncjob(self, job_id):
        job = state.read(job_id, request.args.get("offset", 0, type=int))
        if job is None:
            return jsonify(error="No such sync."), 404
        return jsonify(job)

    @expose("/syncdiscogs")
    @has_access
    def syncdiscogs(self):
        return self._start_sync(jobs.discogs_job(), "/discogsreleasemodelview/list/")

    @expose("/syncdiscogslistings")
    @has_access
    def syncdiscogslistings(self):
        return self._start_sync(
            jobs.discogs_listings_job(), "/discogslistingmodelview/list/"
        )

    @expose("/syncdiscogsorders")
    @has_access
    def syncdiscogsorders(self):
        return self._start_sync(
            jobs.discogs_orders_job(), "/discogsordermodelview/list/"
        )

    @expose("/syncdiscogsordersdeep")
    @has_access
    def syncdiscogsordersdeep(self):
        return self._start_sync(
            jobs.discogs_orders_job(deep=True), "/discogsordermodelview/list/"
        )

    @expose("/syncebaylistings")
    @has_access
    def syncebaylistings(self):
        return self._start_sync(jobs.ebay_listings_job(), "/ebaylistingmodelview/list/")

    @expose("/syncebayviews")
    @has_access
    def syncebayviews(self):
        return self._start_sync(jobs.ebay_views_job(), "/ebaylistingmodelview/list/")

    @expose("/syncebayorders")
    @has_access
    def syncebayorders(self):
        update = request.args.get("doupdate") == "True"
        return self._start_sync(
            jobs.ebay_orders_job(update=update), "/ebayordermodelview/list/"
        )

    @expose("/syncebayordersdeep")
    @has_access
    def syncebayordersdeep(self):
        return self._start_sync(
            jobs.ebay_orders_job(deep=True), "/ebayordermodelview/list/"
        )
