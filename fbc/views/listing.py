import json
import logging

from flask import abort, flash, redirect, request, send_file, url_for
from flask_appbuilder import expose, has_access
from flask_appbuilder.models.sqla.interface import SQLAInterface
from markupsafe import Markup
from wtforms import TextAreaField

from fbc.ebay import insertions, traffic
from fbc.core import devmode, localtime
from fbc.ebay.api import listing_settings, seller_profiles
from fbc.ebay.drafts import check_ready, describe_template, listing_details, new_draft
from fbc.ebay.publish import publish_draft, verify_draft
from fbc.models.ebay import EbayDraft
from fbc.pictures import store
from fbc.core.forms import CustomModelView
from fbc.core.widgets import EbayDraftListWidget, TallTextAreaWidget

logger = logging.getLogger()

# Filter preset on the eBay Drafts list by the Tasks menu link: status Equal to
# draft (string filter 3), as fbc.sales.tasks.DRAFT_STATUS.
DRAFTS_TO_LIST_FILTERS = {"_flt_3_status": "draft"}


class EbayDraftModelView(CustomModelView):
    datamodel = SQLAInterface(EbayDraft)
    show_template = "ebay_draft/show.html"
    list_widget = EbayDraftListWidget
    base_order = ("id", "desc")
    base_permissions = [
        "can_list",
        "can_show",
        "can_edit",
        "can_delete",
        "can_from_unit",
        "can_pictures",
        "can_picture",
        "can_picturefile",
        "can_rotate",
        "can_verify",
        "can_publish",
        "can_end_and_park",
        "can_policies",
        "can_oauth",
    ]
    list_columns = ["title", "fmt_price", "status", "picture_count", "link_column"]
    search_columns = ["title", "status", "unit"]
    label_columns = {
        "fmt_price": "Price",
        "picture_count": "Pictures",
        "link_column": "Links",
        "best_offer": "Best Offer",
        "item_specifics": "Item Specifics",
        "ebay_listing": "eBay Listing",
        "weight_oz": "Package Weight (oz)",
    }
    show_columns = [
        "unit",
        "title",
        "price",
        "best_offer",
        "status",
        "weight_oz",
        "description",
        "item_specifics",
        "ebay_listing",
    ]
    edit_columns = [
        "title",
        "price",
        "best_offer",
        "weight_oz",
        "description",
        "item_specifics",
    ]
    description_columns = {
        "description": "Shown to buyers. Simple HTML such as <p> and <br> is allowed.",
        "weight_oz": "Packed for shipping, in ounces (16 oz = 1 lb). eBay uses it to"
        " work out calculated shipping.",
    }
    edit_form_extra_fields = {
        "item_specifics": TextAreaField(
            "Item Specifics",
            description="One per line, as Name: value. Separate multiple values with |.",
            widget=TallTextAreaWidget(rows=26),
        ),
    }

    def _draft(self, pk):
        draft = self.datamodel.get(pk)
        if not draft:
            abort(404)
        return draft

    def _back(self, draft):
        return redirect(url_for("EbayDraftModelView.show", pk=draft.id))

    def _flash_summary(self, summary):
        for e in summary["errors"]:
            flash(e, "danger")
        for w in summary["warnings"]:
            flash(w, "warning")

    def _end_candidates(self, over_warning):
        """What to offer ending in place of the warning: rows for the page."""
        from fbc.models.discogs import DiscogsListing

        if not over_warning:
            return []
        now = localtime.utcnow()
        rows = []
        for c in insertions.end_candidates(now):
            unit = c["unit"]
            rows.append(
                dict(
                    c,
                    age=(now - c["listing"].start_time).days,
                    views_age=(
                        traffic.age_text(c["views_checked"], now)
                        if c["views"] is not None
                        else None
                    ),
                    on_discogs=bool(unit and DiscogsListing.open_for(unit)),
                )
            )
        return rows

    @expose("/show/<pk>", methods=["GET"])
    @has_access
    def show(self, pk):
        draft = self._draft(pk)
        widgets = self._show(pk)
        over_warning = insertions.publish_warning(insertions.listing_summary())
        end_candidates = self._end_candidates(over_warning)
        return self.render_template(
            self.show_template,
            pk=pk,
            title=self.show_title,
            widgets=widgets,
            related_views=self._related_views,
            draft=draft,
            problems=check_ready(draft),
            pictures=draft.stored_pictures(),
            pictures_folder=store.folder() is not None,
            over_warning=over_warning,
            end_candidates=end_candidates,
            views_unknown=bool(end_candidates)
            and all(c["views"] is None for c in end_candidates),
            uncovered_fee=insertions.uncovered_insertion_fee(
                json.loads(draft.messages) if draft.messages else None
            ),
            result=json.loads(draft.messages) if draft.messages else None,
        )

    @expose("/fromunit/<int:pk>")
    @has_access
    def from_unit(self, pk):
        """Open the Unit's unpublished draft, creating it if there isn't one."""
        from fbc import db
        from fbc.models.inventory import Unit

        unit = db.session.get(Unit, pk)
        if not unit:
            abort(404)
        draft = (
            db.session.query(EbayDraft).filter_by(unit_id=pk, status="draft").first()
        )
        if not draft:
            draft = new_draft(unit)
            db.session.add(draft)
            db.session.commit()
            logger.info("Created eBay draft " + str(draft.id) + " for unit " + str(pk))
        return self._back(draft)

    @expose("/pictures/<int:pk>", methods=["POST"])
    @has_access
    def pictures(self, pk):
        """
        Keep the chosen pictures as the Unit's own, after its other ones.
        Nothing goes to eBay until the draft is checked or published.
        """
        from fbc import db

        draft = self._draft(pk)
        if draft.status != "draft":
            abort(404)
        for message, level in store.add_files(
            draft.unit, request.files.getlist("pictures")
        ):
            flash(message, level)
        db.session.commit()
        return self._back(draft)

    def _stored(self, draft, index):
        pictures = draft.stored_pictures()
        if index < 0 or index >= len(pictures):
            abort(404)
        return pictures[index]

    @expose("/picture/<int:pk>/<int:index>/<action>", methods=["POST"])
    @has_access
    def picture(self, pk, index, action):
        """
        Remove a picture, or move it to the front to make it the main one.
        Removing deletes it from the Unit, for every marketplace.
        """
        from fbc import db

        draft = self._draft(pk)
        picture = self._stored(draft, index)
        if action == "first":
            store.make_first(picture)
        elif action == "remove":
            try:
                store.remove(picture)
            except store.NotConfigured as e:
                flash(str(e), "danger")
        else:
            abort(400)
        db.session.commit()
        return self._back(draft)

    @expose("/picturefile/<int:pk>/<int:index>")
    @has_access
    def picturefile(self, pk, index):
        """A stored picture's working copy, for the draft page to show."""
        picture = self._stored(self._draft(pk), index)
        try:
            return send_file(store.path(picture), mimetype="image/jpeg", max_age=0)
        except (OSError, store.NotConfigured):
            abort(404)

    @expose("/rotate/<int:pk>/<int:index>/<direction>", methods=["POST"])
    @has_access
    def rotate(self, pk, index, direction):
        """
        Turn a picture 90 degrees, in the stored files. eBay's copy is
        forgotten and the new one is sent when the draft is next checked or
        published.
        """
        from fbc import db

        draft = self._draft(pk)
        if draft.status != "draft" or direction not in ("left", "right"):
            abort(404 if draft.status != "draft" else 400)
        picture = self._stored(draft, index)
        try:
            store.rotate(picture, direction)
        except (ValueError, store.NotConfigured) as e:
            flash(str(e), "danger")
            return self._back(draft)
        db.session.commit()
        return self._back(draft)

    @expose("/verify/<int:pk>", methods=["POST"])
    @has_access
    def verify(self, pk):
        """Ask eBay to check the listing without creating it."""
        from fbc import db

        draft = self._draft(pk)
        if devmode.on():
            flash(devmode.message("checking listings with eBay"), "warning")
            return self._back(draft)
        summary = verify_draft(draft)
        draft.messages = json.dumps(summary)
        db.session.commit()
        if not summary["errors"]:
            flash("eBay accepted this listing. Nothing has been listed yet.", "success")
        return self._back(draft)

    @expose("/publish/<int:pk>", methods=["POST"])
    @has_access
    def publish(self, pk):
        """List the draft on eBay. This makes it live."""
        from fbc import db

        draft = self._draft(pk)
        if draft.status == "published":
            flash("This draft is already published.", "warning")
            return self._back(draft)
        if devmode.on():
            flash(devmode.message("publishing to eBay"), "warning")
            return self._back(draft)
        warning = insertions.publish_warning(insertions.listing_summary())
        if warning and not request.form.get("publish_anyway"):
            flash(warning + " Tick Publish anyway to go ahead.", "warning")
            return self._back(draft)
        summary = publish_draft(draft)
        draft.messages = json.dumps(summary)
        db.session.commit()
        if draft.status == "published":
            flash(
                Markup(
                    'Listed on eBay: <a href="'
                    + draft.ebay_listing.url
                    + '">'
                    + draft.ebay_listing_id
                    + "</a>"
                ),
                "success",
            )
        return self._back(draft)

    @expose("/endandpark/<int:pk>/<item_id>", methods=["POST"])
    @has_access
    def end_and_park(self, pk, item_id):
        """
        End one active listing to make room for this draft, and park it. It
        goes through publish.end_listing, so Dev Mode refuses it.
        """
        from fbc import db
        from fbc.models.ebay import EbayListing

        # Inside the method: sync.ending imports models.sync, and a model
        # imported before AppBuilder starts gets its table made by it, which
        # breaks the migration that creates it.
        from fbc.sync import ending

        draft = self._draft(pk)
        listing = db.session.get(EbayListing, item_id)
        if not listing:
            abort(404)
        # Tell the owner if the views on the list were out of date. It never
        # stops the ending, and Dev Mode skips it, since ending is refused.
        if not devmode.on():
            shown = request.form.get("views_shown", "")
            note = traffic.recheck(listing, int(shown) if shown.isdigit() else None)
            if note:
                flash(note, "info")
        ok, message = ending.end_and_park(listing)
        flash(
            ("Ended " + listing.title + ". " if ok else "Couldn't end it: ") + message,
            "success" if ok else "danger",
        )
        return self._back(draft)

    @expose("/policies")
    @has_access
    def policies(self):
        """
        Show where new listings get shipping and returns from: what a listing
        uses (?item_id=, else the configured template, else the newest
        listing), and any business policies on the account.
        """
        from fbc import db
        from fbc.models.ebay import EbayListing

        if devmode.on():
            return self.render_template(
                "ebay_draft/policies.html",
                title="Shipping & Returns",
                dev_mode_message=devmode.message("eBay lookups"),
            )
        profiles, summary, details = seller_profiles()
        self._flash_summary(summary)
        template_id = listing_settings().get("TEMPLATE_ITEM_ID")
        item_id = request.args.get("item_id") or template_id
        if not item_id:
            newest = (
                db.session.query(EbayListing)
                .order_by(EbayListing.start_time.desc())
                .first()
            )
            item_id = newest.item_id if newest else None
        used = None
        copied = []
        if item_id:
            used, fields, summary = listing_details(item_id)
            copied = describe_template(fields)
            self._flash_summary(summary)
        return self.render_template(
            "ebay_draft/policies.html",
            title="Shipping & Returns",
            profiles=profiles,
            details=details,
            item_id=item_id,
            template_id=template_id,
            used=used,
            copied=copied,
        )

    @expose("/oauth", methods=["GET", "POST"])
    @has_access
    def oauth(self):
        """
        Connect eBay's old address, kept for RuNames whose auth accepted URL
        still points here.
        """
        target = url_for("ConnectEbayView.connect")
        if request.query_string:
            target += "?" + request.query_string.decode()
        # 307 keeps a pasted address POSTed to the old address a POST.
        return redirect(target, code=307)
