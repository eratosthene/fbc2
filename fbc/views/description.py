from flask import flash, redirect, request, url_for
from flask_appbuilder import BaseView, expose, has_access

from fbc.ebay import template


class DescriptionTemplateView(BaseView):
    """
    Description Template, under Sales: the layout new eBay Drafts' descriptions
    are built from. Preview shows it for a Unit, without saving.
    """

    route_base = "/descriptiontemplate"
    default_view = "edit"

    @expose("/", methods=["GET", "POST"])
    @has_access
    def edit(self):
        """GET shows the template. A POST has action=save, preview or reset."""
        from fbc import db
        from fbc.models.templates import DescriptionTemplate

        action = request.form.get("action") if request.method == "POST" else None
        body = request.form.get("body") if action else None
        if body is None:
            body = template.current()
        else:
            body = body.replace("\r\n", "\n")
        error = None
        preview = None

        if action == "reset":
            row = db.session.get(DescriptionTemplate, template.NAME)
            if row:
                db.session.delete(row)
                db.session.commit()
            flash("Back to the built-in template.", "success")
            return redirect(url_for("DescriptionTemplateView.edit"))

        unit = self._unit(request.values.get("unit"))
        if action in ("save", "preview"):
            try:
                template.validate(body)
            except template.TemplateError as e:
                error = str(e)
            else:
                if action == "save":
                    row = db.session.get(DescriptionTemplate, template.NAME)
                    if row is None:
                        row = DescriptionTemplate(name=template.NAME)
                        db.session.add(row)
                    row.body = body
                    db.session.commit()
                    flash("Saved. New eBay Drafts use it.", "success")
                    return redirect(url_for("DescriptionTemplateView.edit"))
        if (action == "preview" and not error) or action is None:
            if unit is not None:
                preview = template.render(body, unit)
        return self.render_template(
            "description_template.html",
            title="Description Template",
            body=body,
            error=error,
            unit=unit,
            preview=preview,
            fields=template.FIELDS,
            is_saved=template.saved() is not None,
        )

    @staticmethod
    def _unit(text):
        """The Unit to preview: the one asked for, else the latest with notes."""
        from fbc import db
        from fbc.models.inventory import Unit

        text = (text or "").strip()
        if text.isdigit():
            return db.session.get(Unit, int(text))
        noted = Unit.record_note.isnot(None) | Unit.sleeve_note.isnot(None)
        return (
            db.session.query(Unit).filter(noted).order_by(Unit.id.desc()).first()
            or db.session.query(Unit).order_by(Unit.id.desc()).first()
        )
