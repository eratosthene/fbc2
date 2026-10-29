import json
import logging

from flask import abort, flash, redirect, request, url_for
from flask_appbuilder import expose, has_access
from flask_appbuilder.models.sqla.interface import SQLAInterface
from markupsafe import Markup
from wtforms import TextAreaField

from fbc.listing import (
    check_ready,
    new_draft,
    publish_draft,
    seller_profiles,
    upload_picture,
    verify_draft,
)
from fbc.models.ebay import EbayDraft
from fbc.util import CustomModelView
from fbc.widgets import EbayDraftListWidget, TallTextAreaWidget

logger = logging.getLogger()

# eBay allows up to 24 pictures on a listing.
MAX_PICTURES = 24


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
        "can_verify",
        "can_publish",
        "can_policies",
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
    }
    show_columns = [
        "unit",
        "title",
        "price",
        "best_offer",
        "status",
        "description",
        "item_specifics",
        "ebay_listing",
    ]
    edit_columns = ["title", "price", "best_offer", "description", "item_specifics"]
    description_columns = {
        "description": "Shown to buyers. Simple HTML such as <p> and <br> is allowed.",
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

    @expose("/show/<pk>", methods=["GET"])
    @has_access
    def show(self, pk):
        draft = self._draft(pk)
        widgets = self._show(pk)
        return self.render_template(
            self.show_template,
            pk=pk,
            title=self.show_title,
            widgets=widgets,
            related_views=self._related_views,
            draft=draft,
            problems=check_ready(draft),
            result=json.loads(draft.messages) if draft.messages else None,
        )

    @expose("/fromunit/<int:pk>")
    @has_access
    def from_unit(self, pk):
        """Open the Unit's unpublished draft, creating it if there isn't one."""
        from fbc import db
        from fbc.models.inventory import Unit

        unit = db.session.query(Unit).get(pk)
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
        """Upload pictures to eBay's picture hosting and add them to the draft."""
        from fbc import db

        draft = self._draft(pk)
        urls = draft.picture_list()
        for f in request.files.getlist("pictures"):
            if not f.filename:
                continue
            if len(urls) >= MAX_PICTURES:
                flash("eBay allows at most 24 pictures.", "warning")
                break
            url, summary = upload_picture(f.stream, f.filename)
            self._flash_summary(summary)
            if url:
                urls.append(url)
        draft.set_picture_list(urls)
        db.session.commit()
        return self._back(draft)

    @expose("/picture/<int:pk>/<int:index>/<action>", methods=["POST"])
    @has_access
    def picture(self, pk, index, action):
        """Remove a picture, or move it to the front to make it the main one."""
        from fbc import db

        draft = self._draft(pk)
        urls = draft.picture_list()
        if index < 0 or index >= len(urls):
            abort(404)
        url = urls.pop(index)
        if action == "first":
            urls.insert(0, url)
        elif action != "remove":
            abort(400)
        draft.set_picture_list(urls)
        db.session.commit()
        return self._back(draft)

    @expose("/verify/<int:pk>", methods=["POST"])
    @has_access
    def verify(self, pk):
        """Ask eBay to check the listing without creating it."""
        from fbc import db

        draft = self._draft(pk)
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

    @expose("/policies")
    @has_access
    def policies(self):
        """Show the seller's business policy IDs for config.py."""
        profiles, summary = seller_profiles()
        self._flash_summary(summary)
        return self.render_template(
            "ebay_draft/policies.html", title="eBay Business Policies", profiles=profiles
        )
