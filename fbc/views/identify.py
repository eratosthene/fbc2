from flask import flash, redirect, request, url_for
from flask_appbuilder import BaseView, expose, has_access

from fbc import db, devmode, identify
from fbc.models.inventory import PurchaseLot


class IdentifyRecordView(BaseView):
    """
    Identify Record, under Inventory: find the Discogs release a record in
    hand is, then go to Add Unit for it. See fbc/identify.py.
    """

    route_base = "/identifyrecord"
    default_view = "search"

    def _lots(self):
        return db.session.query(PurchaseLot).order_by(PurchaseLot.id.desc()).all()

    @expose("/")
    @has_access
    def search(self):
        form = {
            "artist": request.args.get("artist", ""),
            "title": request.args.get("title", ""),
            "catno": request.args.get("catno", ""),
            "barcode": request.args.get("barcode", ""),
            "country": request.args.get("country", identify.DEFAULT_COUNTRY),
            "matrix": request.args.get("matrix", ""),
            "type": request.args.get("type", identify.DEFAULT_TYPE),
            "reissue": request.args.get("reissue", ""),
            "color": request.args.get("color", ""),
            "lot": request.args.get("lot", ""),
        }
        found = None
        if request.args.get("sample") and devmode.on():
            found = identify.sample(form["country"], form["matrix"], form["color"])
        elif request.args.get("go"):
            found = identify.search(
                **{k: form[k] for k in ("artist", "title", "catno", "barcode")},
                country=form["country"],
                matrix=form["matrix"],
                vinyl_type=form["type"],
                reissue=form["reissue"],
                color=form["color"],
            )
        return self.render_template(
            "identify/search.html",
            title="Identify Record",
            form=form,
            found=found,
            lots=self._lots(),
            dev_mode=devmode.on(),
            default_country=identify.DEFAULT_COUNTRY,
            types=[label for label, _ in identify.TYPES],
            reissue_choices=identify.REISSUE_CHOICES,
        )

    @expose("/pick", methods=["POST"])
    @has_access
    def pick(self):
        """Save the chosen release in fbc and go to Add Unit with it chosen."""
        release_id = request.form.get("release_id", "")
        lot = request.form.get("lot") or None
        matrix = request.form.get("matrix", "")
        if not release_id.isdigit():
            flash("Pick a release.", "danger")
            return redirect(url_for("IdentifyRecordView.search"))
        if devmode.on() and int(release_id) in identify.SAMPLE_IDS:
            # The sample data's releases don't exist anywhere to save.
            flash("Sample releases can't be added; they're only for testing.", "info")
            return redirect(url_for("IdentifyRecordView.search", sample=1))
        release, problem = identify.save_release(release_id)
        if problem:
            flash(problem, "danger")
            return redirect(url_for("IdentifyRecordView.search"))
        return redirect(
            url_for(
                "UnitModelView.add",
                **identify.unit_add_params(release, lot, matrix),
            )
        )
