import re

from flask import abort, flash, redirect, request, send_file, url_for
from flask_appbuilder import BaseView, expose, has_access

from fbc.pictures import store

# The name a picture is shown under, which is its place: unit-123-1.jpg.
NAME = re.compile(r"unit-(\d+)-(\d+)\.jpg")


class UnitPicturesView(BaseView):
    """
    A Unit's pictures, kept for every marketplace (#140): add, order, turn and
    remove them, and take them to a Discogs listing, which fbc can't send them
    to (Discogs's API has no photo upload). Each picture is shown full size,
    the right way up, under a real name (`unit-123-1.jpg`), and all of them
    come as one zip.
    """

    route_base = "/unitpictures"
    default_view = "show"

    def _unit(self, pk):
        from fbc import db
        from fbc.models.inventory import Unit

        unit = db.session.get(Unit, pk)
        if not unit:
            abort(404)
        return unit

    def _picture(self, unit, index):
        pictures = store.for_unit(unit)
        if index < 0 or index >= len(pictures):
            abort(404)
        return pictures[index]

    def _back(self, unit):
        return redirect(url_for("UnitPicturesView.show", pk=unit.id))

    @expose("/show/<int:pk>")
    @has_access
    def show(self, pk):
        unit = self._unit(pk)
        return self.render_template(
            "pictures/show.html",
            title="Pictures",
            unit=unit,
            pictures=store.for_unit(unit),
            name=store.display_name,
            max_pictures=store.MAX_PICTURES,
            pictures_folder=store.folder() is not None,
        )

    @expose("/add/<int:pk>", methods=["POST"])
    @has_access
    def add(self, pk):
        """Keep the chosen files as the Unit's next pictures."""
        from fbc import db

        unit = self._unit(pk)
        for message, level in store.add_files(unit, request.files.getlist("pictures")):
            flash(message, level)
        db.session.commit()
        return self._back(unit)

    @expose("/picture/<int:pk>/<int:index>/<action>", methods=["POST"])
    @has_access
    def picture(self, pk, index, action):
        """Make a picture the main one, or remove it (for every marketplace)."""
        from fbc import db

        unit = self._unit(pk)
        picture = self._picture(unit, index)
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
        return self._back(unit)

    @expose("/rotate/<int:pk>/<int:index>/<direction>", methods=["POST"])
    @has_access
    def rotate(self, pk, index, direction):
        """Turn a picture 90 degrees, in the stored files."""
        from fbc import db

        if direction not in ("left", "right"):
            abort(400)
        unit = self._unit(pk)
        picture = self._picture(unit, index)
        try:
            store.rotate(picture, direction)
        except (ValueError, store.NotConfigured) as e:
            flash(str(e), "danger")
            return self._back(unit)
        db.session.commit()
        return self._back(unit)

    @expose("/file/<int:pk>/<filename>")
    @has_access
    def file(self, pk, filename):
        """
        A picture's working copy under its real name, for the page to show and
        for dragging to Discogs. The name's number is the picture's place, as
        in the zip, so it can change when the pictures are reordered.
        """
        unit = self._unit(pk)
        found = NAME.fullmatch(filename)
        if not found or int(found.group(1)) != pk:
            abort(404)
        picture = self._picture(unit, int(found.group(2)) - 1)
        try:
            return send_file(
                store.path(picture),
                mimetype="image/jpeg",
                download_name=filename,
                max_age=0,
            )
        except (OSError, store.NotConfigured):
            abort(404)

    @expose("/zip/<int:pk>")
    @has_access
    def zip(self, pk):
        """Every working copy in one download, named as the page names them."""
        unit = self._unit(pk)
        try:
            archive = store.zip_of(unit)
        except (OSError, store.NotConfigured) as e:
            flash("Couldn't make the zip: " + str(e), "danger")
            return self._back(unit)
        if archive is None:
            flash("This Unit has no pictures.", "warning")
            return self._back(unit)
        return send_file(
            archive,
            mimetype="application/zip",
            as_attachment=True,
            download_name="unit-" + str(unit.id) + "-pictures.zip",
            max_age=0,
        )
