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
            "release_id": request.args.get("release_id", ""),
            "master": request.args.get("master", ""),
        }
        # Everything on the form but what a link adds, to put it back after a
        # "Use this master" or "Check the next 20" click.
        keep = {k: v for k, v in form.items() if v or k == "country"}
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
                release_id=form["release_id"],
                master=form["master"],
                offset=request.args.get("offset", 0),
            )
        masters = None
        if request.args.get("find_master"):
            masters = identify.find_masters(form["artist"], form["title"])
        return self.render_template(
            "identify/search.html",
            title="Identify Record",
            form=form,
            found=found,
            masters=masters,
            master_links={
                m["id"]: url_for(
                    "IdentifyRecordView.search", **dict(keep, master=m["id"])
                )
                for m in (masters or {}).get("masters", [])
            },
            more_url=url_for(
                "IdentifyRecordView.search",
                **dict(keep, go=1, offset=found["next_offset"]),
            )
            if found and found.get("next_offset")
            else None,
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

    @expose("/collect", methods=["GET", "POST"])
    @has_access
    def collect(self):
        """
        Add the chosen release to the Discogs collection with its condition,
        notes and folder, then go to Add Unit. The GET shows the form; only
        the POST, from its button, writes to Discogs.
        """
        data = request.form if request.method == "POST" else request.args
        release_id = data.get("release_id", "")
        lot = data.get("lot") or None
        matrix = data.get("matrix", "")
        if not release_id.isdigit():
            flash("Pick a release.", "danger")
            return redirect(url_for("IdentifyRecordView.search"))
        form = identify.collection_form()
        if request.method == "POST" and form["ok"]:
            values = {
                f["name"]: data.get("field_" + str(f["id"]), "") for f in form["fields"]
            }
            try:
                folder_id = int(data.get("folder", ""))
            except ValueError:
                folder_id = None
            if folder_id is None:
                flash("Choose a folder.", "danger")
            else:
                release, instance_id, warnings, problem = identify.collect(
                    release_id, folder_id, values
                )
                for warning in warnings:
                    flash(warning, "warning")
                if problem:
                    flash(problem, "danger")
                    if release is None and instance_id:
                        return redirect(url_for("IdentifyRecordView.search"))
                else:
                    flash("Added to your Discogs collection.", "success")
                    return redirect(
                        url_for(
                            "UnitModelView.add",
                            **identify.unit_add_params(
                                release,
                                lot,
                                matrix,
                                instance_id=instance_id,
                                grading=identify.grading_from(values),
                                notes=values.get("Notes", ""),
                            ),
                        )
                    )
        return self.render_template(
            "identify/collect.html",
            title="Add to Discogs",
            form=form,
            release_id=release_id,
            lot=lot or "",
            matrix=matrix,
            values=request.form,
        )
